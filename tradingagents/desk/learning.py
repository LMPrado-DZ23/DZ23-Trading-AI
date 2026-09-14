"""Aprendizado dos traders (MA-09 operacional).

Os agentes ficam melhores pelo único caminho honesto: **desfecho realizado**.
Cada operação fechada vira estatística por (estratégia × regime), e isso
ajusta a confiança e a exposição sugerida nas próximas.

Freios contra autoengano — cada um existe porque o erro oposto é comum:
- Só aprende de operação FECHADA. Posição aberta é esperança, não resultado.
- Amostra mínima antes de mudar comportamento: 3 acertos seguidos não fazem
  uma estratégia boa.
- Peso decai com o tempo: mercado de seis meses atrás não manda no de hoje.
- O aprendizado NUNCA aumenta risco além do mandato: ele pode reduzir
  exposição livremente, mas aumentar só até o teto que o dono definiu.
- Estratégia ruim é rebaixada; e rebaixamento exige evidência, não um dia
  ruim.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .signals import Regime

AMOSTRA_MINIMA = 12          # abaixo disso, comportamento é o padrão
MEIA_VIDA_DIAS = 45.0        # peso de um desfecho cai pela metade em 45 dias
PISO_CONFIANCA = Decimal("0.20")
TETO_CONFIANCA = Decimal("1.00")


@dataclass(frozen=True)
class Desfecho:
    """Uma operação FECHADA — a única coisa de que se aprende."""
    estrategia: str
    regime: str
    instrumento: str
    retorno: Decimal            # fração: 0.02 = +2%
    fechado_em: datetime
    forca_do_sinal: float = 0.0

    def peso(self, agora: datetime | None = None) -> float:
        agora = agora or datetime.now(timezone.utc)
        dias = max(0.0, (agora - self.fechado_em).total_seconds() / 86400)
        return 0.5 ** (dias / MEIA_VIDA_DIAS)


@dataclass
class Estatistica:
    amostras: int = 0
    peso_total: float = 0.0
    ganhos: float = 0.0
    perdas: float = 0.0
    retorno_ponderado: float = 0.0

    @property
    def taxa_acerto(self) -> float:
        total = self.ganhos + self.perdas
        return self.ganhos / total if total else 0.0

    @property
    def retorno_medio(self) -> float:
        return self.retorno_ponderado / self.peso_total if self.peso_total else 0.0

    @property
    def maduro(self) -> bool:
        return self.amostras >= AMOSTRA_MINIMA


class MemoriaDeDesempenho:
    """Memória por (estratégia × regime). Persistida em JSON no volume."""

    def __init__(self, caminho: str | Path | None = None, *,
                 armazem=None, tenant_id: str = "legacy"):
        # `armazem` (opcional) leva o histórico para fora do servidor: é o que
        # permite trocar de máquina sem perder semanas de aprendizado.
        self.caminho = Path(caminho) if caminho else None
        self.armazem = armazem
        self.tenant_id = tenant_id
        self._desfechos: list[Desfecho] = []
        self._carregar()

    # -- persistência --------------------------------------------------
    def _carregar(self) -> None:
        dados = None
        if self.armazem is not None:
            dados = self.armazem.carregar(self.tenant_id, "aprendizado")
        if dados is None:
            if not (self.caminho and self.caminho.exists()):
                return
            try:
                dados = json.loads(self.caminho.read_text())
            except ValueError:
                return
        for d in dados.get("desfechos", []):
            try:
                self._desfechos.append(Desfecho(
                    d["estrategia"], d["regime"], d["instrumento"],
                    Decimal(str(d["retorno"])),
                    datetime.fromisoformat(d["fechado_em"]),
                    float(d.get("forca_do_sinal", 0.0))))
            except (KeyError, ValueError):
                continue

    def salvar(self) -> None:
        payload = {"desfechos": [
            {"estrategia": d.estrategia, "regime": d.regime,
             "instrumento": d.instrumento, "retorno": str(d.retorno),
             "fechado_em": d.fechado_em.isoformat(),
             "forca_do_sinal": d.forca_do_sinal}
            for d in self._desfechos[-2000:]]}
        if self.armazem is not None:
            self.armazem.salvar(self.tenant_id, "aprendizado", payload)
            return
        if not self.caminho:
            return
        from .storage import escrever_json_atomico
        escrever_json_atomico(self.caminho, payload)

    # -- aprendizado ---------------------------------------------------
    def registrar(self, desfecho: Desfecho) -> None:
        self._desfechos.append(desfecho)
        self.salvar()

    def estatistica(self, estrategia: str, regime: str | Regime | None = None,
                    agora: datetime | None = None) -> Estatistica:
        regime = regime.value if isinstance(regime, Regime) else regime
        st = Estatistica()
        for d in self._desfechos:
            if d.estrategia != estrategia:
                continue
            if regime is not None and d.regime != regime:
                continue
            p = d.peso(agora)
            st.amostras += 1
            st.peso_total += p
            st.retorno_ponderado += p * float(d.retorno)
            if d.retorno > 0:
                st.ganhos += p
            elif d.retorno < 0:
                st.perdas += p
        return st

    # -- uso pelos traders ---------------------------------------------
    def ajuste_de_confianca(self, estrategia: str, regime: str | Regime,
                            base: Decimal, agora: datetime | None = None) -> Decimal:
        """Confiança ajustada pelo histórico NAQUELE regime.

        Sem amostra madura, devolve a base — não inventa competência.
        """
        st = self.estatistica(estrategia, regime, agora)
        if not st.maduro:
            return base
        # fator suave: acerto 50% => 1.0; 70% => ~1.2; 30% => ~0.8
        fator = Decimal(str(round(0.5 + st.taxa_acerto, 3)))
        ajustada = base * fator
        return max(PISO_CONFIANCA, min(TETO_CONFIANCA, ajustada))

    def fator_de_exposicao(self, estrategia: str, regime: str | Regime,
                           agora: datetime | None = None) -> Decimal:
        """Multiplicador de tamanho. Pode encolher à vontade; para crescer,
        exige histórico maduro E positivo — e nunca passa de 1.0, porque o
        teto de risco é do mandato, não do aprendizado."""
        st = self.estatistica(estrategia, regime, agora)
        if not st.maduro:
            return Decimal("1.0")
        if st.retorno_medio <= 0:
            severidade = min(0.6, abs(st.retorno_medio) * 12)
            return max(Decimal("0.25"), Decimal(str(round(1 - severidade, 3))))
        return Decimal("1.0")

    def deve_rebaixar(self, estrategia: str, agora: datetime | None = None) -> tuple[bool, str]:
        """Rebaixar exige evidência: amostra madura e resultado negativo
        consistente — não um dia ruim."""
        st = self.estatistica(estrategia, None, agora)
        if not st.maduro:
            return False, f"amostra insuficiente ({st.amostras}/{AMOSTRA_MINIMA})"
        if st.retorno_medio < -0.005 and st.taxa_acerto < 0.45:
            return True, (f"retorno médio {st.retorno_medio:+.2%} e acerto "
                          f"{st.taxa_acerto:.0%} em {st.amostras} operações")
        return False, (f"desempenho aceitável ({st.retorno_medio:+.2%}, "
                       f"{st.taxa_acerto:.0%} de acerto)")

    def melhor_regime(self, estrategia: str) -> tuple[str, float] | None:
        """Em que cenário esta estratégia funciona melhor (se é que funciona)."""
        melhores = []
        for regime in Regime:
            st = self.estatistica(estrategia, regime)
            if st.maduro:
                melhores.append((regime.value, st.retorno_medio))
        if not melhores:
            return None
        return max(melhores, key=lambda x: x[1])

    def relatorio(self) -> list[dict]:
        """O que o painel mostra: aprendizado visível, não caixa-preta."""
        estrategias = sorted({d.estrategia for d in self._desfechos})
        saida = []
        for e in estrategias:
            geral = self.estatistica(e)
            rebaixar, motivo = self.deve_rebaixar(e)
            melhor = self.melhor_regime(e)
            saida.append({
                "estrategia": e, "operacoes": geral.amostras,
                "maduro": geral.maduro,
                "taxa_acerto": round(geral.taxa_acerto, 3),
                "retorno_medio": round(geral.retorno_medio, 5),
                "melhor_regime": melhor[0] if melhor else None,
                "rebaixar": rebaixar, "avaliacao": motivo,
                "aviso": "histórico não prevê o futuro"})
        return saida


__all__ = ["MemoriaDeDesempenho", "Desfecho", "Estatistica", "AMOSTRA_MINIMA"]
