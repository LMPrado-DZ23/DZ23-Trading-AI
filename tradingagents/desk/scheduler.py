"""Agendador persistente da mesa (MA-03, cenário 11).

O ciclo passa a acontecer sozinho: laço em thread própria, disparado por
intervalo/evento, independente de navegador aberto ou de qualquer cliente.

Invariantes codificadas:
- Ao (re)iniciar, RECONCILIA antes de retomar: ordens incertas são
  resolvidas contra a corretora antes de qualquer proposta nova.
- Sinal velho não vira ordem atrasada: snapshots com idade acima do limite
  são descartados com motivo.
- O estado durável (posições, ciclos, ordens pendentes) é salvo a cada
  ciclo; reinício restaura de disco, não da memória de um chat.
- Falha de um ciclo não derruba o laço; erros são contados e expostos.
"""
from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .execution import OrderState
from .models import utcnow


@dataclass
class SchedulerState:
    """Instantâneo durável — o que precisa sobreviver a um reinício."""
    cycles_run: int = 0
    last_cycle_at: str | None = None
    errors: int = 0
    open_positions: list[str] = field(default_factory=list)
    pending_reconciliation: list[str] = field(default_factory=list)
    stopped: bool = False
    ultimo_bloqueio: str = ""     # por que o laço NÃO executou (modo/posse)
    ultimo_modo: str = ""         # "observador" quando ciclou sem executar

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> SchedulerState:
        try:
            data = json.loads(raw)
        except ValueError:
            return cls()
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


class DeskScheduler:
    """Laço de vida da mesa. Não conhece navegador, chat nem sessão."""

    def __init__(self, orchestrator, coordinator, *, snapshot_provider,
                 price_provider, state_path: str | Path, on_cycle=None,
                 armazem=None, tenant_id: str = "legacy",
                 interval_seconds: int = 300, max_snapshot_age_minutes: int = 15,
                 guarda_operacao=None):
        self.orchestrator = orchestrator
        self.coordinator = coordinator
        self.snapshot_provider = snapshot_provider
        self.price_provider = price_provider
        self.state_path = Path(state_path)
        self.armazem = armazem
        self.tenant_id = tenant_id
        self.interval = interval_seconds
        self.max_age = timedelta(minutes=max_snapshot_age_minutes)
        self.on_cycle = on_cycle       # o painel escuta aqui
        # Porteiro do modo/posse: () -> (acao, motivo), acao em
        # {"executar", "observar", "bloquear"}. Ausente = sempre executar.
        self.guarda_operacao = guarda_operacao
        self.state = self._load()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- persistência --------------------------------------------------
    def _load(self) -> SchedulerState:
        if self.armazem is not None:
            doc = self.armazem.carregar(self.tenant_id, "mesa")
            if doc is not None:
                return SchedulerState(**{k: v for k, v in doc.items()
                                         if k in SchedulerState.__annotations__})
        if self.state_path.exists():
            return SchedulerState.from_json(self.state_path.read_text())
        return SchedulerState()

    def save(self) -> None:
        self.state.open_positions = sorted(self.orchestrator.open_exposure())
        self.state.pending_reconciliation = [
            r.order_id for r in self.coordinator.pending_reconciliation()]
        if self.armazem is not None:
            self.armazem.salvar(self.tenant_id, "mesa", dict(self.state.__dict__))
            # Livro de GESTÃO durável (M3/M4): política, topo, parcial e
            # quantidade por posição sobrevivem ao reinício. Falha aqui não
            # derruba o ciclo — o financeiro (OMS) já foi persistido.
            with contextlib.suppress(Exception):
                self.armazem.salvar(self.tenant_id, "livro",
                                    self.orchestrator.livro_doc())
            return
        from .storage import escrever_json_atomico
        # Temporário único por escrita: o laço grava a cada ciclo e duas
        # gravações concorrentes sobre um .tmp fixo se atropelavam.
        escrever_json_atomico(self.state_path, dict(self.state.__dict__))

    # -- recuperação ---------------------------------------------------
    def recover(self, exchange_lookup=None) -> dict:
        """Reconcilia ANTES de retomar. Sem resposta da corretora, a ordem
        segue incerta e a reserva continua travada."""
        resolved, still_unknown = [], []
        for rec in list(self.coordinator.pending_reconciliation()):
            view = exchange_lookup(rec) if exchange_lookup else None
            self.coordinator.reconcile(rec.order_id, view)
            (resolved if not rec.needs_reconciliation else still_unknown).append(rec.order_id)
        self.save()
        return {"reconciliadas": resolved, "ainda_incertas": still_unknown,
                "posicoes": sorted(self.orchestrator.open_exposure())}

    # -- ciclo ---------------------------------------------------------
    def fresh_snapshots(self, now=None) -> tuple[list, list[str]]:
        now = now or utcnow()
        fresh, discarded = [], []
        for snap in self.snapshot_provider():
            if now - snap.cutoff > self.max_age:
                discarded.append(f"{snap.instrument}: snapshot vencido, não vira ordem")
            else:
                fresh.append(snap)
        return fresh, discarded

    def _executar_saida(self, s: dict) -> dict:
        """Executa UMA saída estruturada do motor global (M3/M4).

        Garantias codificadas aqui:
        - Nunca mais que o LOTE vivo (o financeiro manda; o diário é gestão).
        - Nunca duplica: com ordem de venda em curso para o instrumento, a
          segunda saída vira "em_curso" — não uma segunda ordem.
        - Procedência obrigatória (mesmo contrato das entradas).
        """
        inst = str(s.get("instrumento") or "")
        agente = str(s.get("agente") or "")
        base = {"instrumento": inst, "agente": agente,
                "motivo": s.get("motivo", ""),
                "reason_code": s.get("reason_code", ""),
                "urgencia": s.get("urgencia", "")}
        if not inst or not agente:
            return {**base, "estado": "ignorada", "nota": "saída sem instrumento/agente"}
        if self.coordinator.tem_saida_em_curso(inst):
            existente = next(
                (r.order_id for r in self.coordinator.orders.values()
                 if r.instrumento == inst and r.lado == "sell" and r.nao_terminal),
                "")
            return {**base, "estado": "em_curso", "ordem": existente}
        lot = self.coordinator.ledger.lot_quantity(inst, agente)
        if lot <= 0:
            # Sem lastro financeiro: o diário estava à frente do livro.
            self.orchestrator.close_position(inst)
            return {**base, "estado": "sem_lote",
                    "nota": "livro financeiro sem lote vivo — diário limpo"}
        fracao = Decimal(str(s.get("fracao") or "1"))
        qtd = lot if fracao >= 1 else (lot * fracao)
        if qtd <= 0:
            qtd = lot
        precos = self.price_provider() or {}
        price = precos.get(inst) or (Decimal(str(s["preco_referencia"]))
                                     if s.get("preco_referencia") else None)
        if price is None or price <= 0:
            raise RuntimeError("sem preço para executar a saída")
        custo_lote = self.coordinator.ledger.lot_cost(inst, agente)
        cost_basis = custo_lote * min(Decimal("1"), qtd / lot)
        from .provenance import build_state, validar
        visto = s.get("visto_em") or ""
        try:
            cutoff = datetime.fromisoformat(visto) if visto else None
        except ValueError:
            cutoff = None
        prov = validar(build_state(
            instrument=inst, cutoff=cutoff,
            source=(s.get("fonte") or "fixture"),
            strategy=s.get("politica") or "saida-global", agent_id=agente,
            worker_id=getattr(self.orchestrator, "worker_id", "w1")))
        out = self.coordinator.close_position(
            instrument=inst, quantity=qtd, price=price, cost_basis=cost_basis,
            agent_id=agente, provenance=prov)
        estado = out.get("estado", "")
        feito = {**base, "estado": estado, "ordem": out.get("order_id", ""),
                 "quantidade": str(out.get("quantidade", qtd)),
                 "preco": str(out.get("preco", price))}
        if estado == "filled":
            preco_real = Decimal(str(out.get("preco") or price))
            if qtd >= lot:
                self.orchestrator.close_position(inst, preco_saida=preco_real)
            else:
                self.orchestrator.registrar_saida_parcial(
                    inst, qtd, preco_real,
                    tomou_parcial_no_alvo=(s.get("reason_code")
                                           == "PARTIAL_TARGET"))
        return feito

    def run_once(self, executar: bool = True, persistir: bool = True) -> dict:
        """Um ciclo. `executar=False` = modo observador: analisa e propõe,
        nunca submete ordem; `persistir=False` mantém o ciclo puro — quem só
        observa não grava o estado financeiro da mesa que outro processo pode
        estar executando."""
        snaps, discarded = self.fresh_snapshots()
        out = {"descartados": discarded, "ordens": [], "encerradas": [],
               "resumo": "sem dados elegíveis"}
        # PROTEÇÃO PRIMEIRO: antes de avaliar entrada nova, confere se o stop
        # das posições compradas continua vivo na corretora. Um stop sumido
        # bloqueia risco novo; cancelado é recolocado uma vez.
        if executar:
            try:
                problemas_stop = self.coordinator.verificar_stops()
                if problemas_stop:
                    out.setdefault("stops", []).extend(problemas_stop)
                    self.state.errors += len(problemas_stop)
            except Exception as exc:
                self.state.errors += 1
                out.setdefault("erros", []).append(
                    f"reconciliação de stop: {type(exc).__name__}: {exc}"[:160])
        if snaps:
            report = self.orchestrator.run_cycle(snaps, self.price_provider())
            if executar:
                # PROTEÇÃO PRIMEIRO: as saídas decididas pelo motor global
                # executam antes de qualquer entrada nova do ciclo.
                for s in getattr(report, "saidas", []):
                    try:
                        out["encerradas"].append(self._executar_saida(s))
                    except Exception as exc:
                        self.state.errors += 1
                        out.setdefault("erros", []).append(
                            f"saída {s.get('instrumento', '?')}: "
                            f"{type(exc).__name__}: {exc}"[:160])
                for intent in report.intents:
                    try:
                        rec = self.coordinator.submit(intent)
                        out["ordens"].append({"ordem": rec.order_id, "estado": rec.state.value})
                        # Compra executada vira posição NO LIVRO da mesa — sem
                        # isto a gestão de saída fica cega em produção (o livro
                        # só era alimentado em teste).
                        if (rec.state is OrderState.FILLED
                                and getattr(intent.action, "value", "") == "buy"
                                and rec.filled_qty > 0 and rec.avg_price > 0):
                            self.orchestrator.register_fill(
                                intent.reservation.instrument, rec.filled_qty,
                                rec.avg_price, intent.reservation.agent_id,
                                rec.filled_qty * rec.avg_price)
                    except Exception as exc:
                        self.state.errors += 1
                        out.setdefault("erros", []).append(f"{type(exc).__name__}: {exc}"[:160])
                out["resumo"] = report.summary
            else:
                out["propostas"] = [
                    {"agente": i.reservation.agent_id or "",
                     "instrumento": i.reservation.instrument,
                     "lado": i.action.value,
                     "quantidade": f"{i.reservation.quantity:f}",
                     "tipo": "entrada" if i.action.value == "buy" else "saida"}
                    for i in report.intents]
                out["resumo"] = (f"modo observador: {len(report.intents)} "
                                 "proposta(s) analisada(s), nenhuma ordem enviada")
            # Teto generoso: o painel mostra o motivo de NO_TRADE POR trader —
            # truncar em 6 escondia justamente os experimentais do fim da fila.
            out["vetos"] = [f"{a}: {w}" for a, w in report.vetoed][:12]
            out["abstencoes"] = [f"{a} · {i}: {r}" for a, i, r in report.abstentions][:24]
            # Soma, não substitui: erro de saída do motor (antes daqui) não
            # pode ser apagado pelo relatório do ciclo.
            out["erros"] = out.get("erros", []) + report.errors[:4]
        self.state.cycles_run += 1
        self.state.last_cycle_at = utcnow().isoformat()
        if persistir:
            self.save()
            if self.on_cycle:
                with contextlib.suppress(Exception):
                    self.on_cycle(out)   # painel nunca derruba o ciclo
        return out

    # -- laço ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                acao, motivo = "executar", ""
                if self.guarda_operacao is not None:
                    try:
                        acao, motivo = self.guarda_operacao()
                    except Exception as exc:
                        # guarda quebrado = fail-closed, nunca "executa assim mesmo"
                        acao, motivo = "bloquear", f"guarda falhou ({type(exc).__name__})"
                if acao == "bloquear":
                    self.state.ultimo_bloqueio = motivo or "execução bloqueada"
                    self.state.ultimo_modo = ""
                    self.save()
                else:
                    self.state.ultimo_bloqueio = ""
                    self.state.ultimo_modo = "observador" if acao == "observar" else ""
                    self.run_once(executar=acao != "observar",
                                  persistir=acao != "observar")
            except Exception:                      # ciclo ruim não mata a mesa
                self.state.errors += 1
                self.save()
            self._stop.wait(self.interval)

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self.state.stopped = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="desk-scheduler",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        """Parada controlada: encerra o laço, mantém posições e estado."""
        self._stop.set()
        self.state.stopped = True
        self.save()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        return {"rodando": self.running, "intervalo_s": self.interval,
                "ciclos": self.state.cycles_run, "erros": self.state.errors,
                "ultimo_ciclo": self.state.last_cycle_at,
                "posicoes": self.state.open_positions,
                "pendencias": self.state.pending_reconciliation,
                "ultimo_bloqueio": self.state.ultimo_bloqueio,
                "ultimo_modo": self.state.ultimo_modo}


__all__ = ["DeskScheduler", "SchedulerState"]
