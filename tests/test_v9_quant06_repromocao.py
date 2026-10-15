"""V9-QUANT-06 — deterioração não pode ser anulada no mesmo ciclo.

Achado do baseline: `desk/evolucao.py` — o ciclo promove challengers aprovados
sem consultar o resultado do próprio `vigiar()` que acabou de rodar: o rollback
por deterioração (C→B) é imediatamente desfeito pela promoção da MESMA evidência
antiga (B→C) no mesmo ciclo. `promover()` manual também aceita evidência
revogada e trader suspenso.

Contrato testado:
- ciclo que reverte não repromove a mesma evidência no mesmo ciclo;
- evidência revertida fica marcada como revogada e continua bloqueada;
- promover() recusa evidência revogada e trader suspenso (fail-closed);
- evidência NOVA (outro experimento) volta a permitir promoção legítima;
- o bloqueio é por trader/versão: o rollback de um trader não impede a
  promoção de outro.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.evolucao import EvolucaoAutonoma, Experimento
from tradingagents.desk.laboratorio import Hipotese
from tradingagents.desk.learning import AMOSTRA_MINIMA, Desfecho

AGORA = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def _desfecho(agente: str, retorno: str, *, versao: str = "2") -> Desfecho:
    return Desfecho(estrategia="momentum", regime="tendencia_alta",
                    instrumento="BTC/USDT", retorno=Decimal(retorno),
                    fechado_em=AGORA, agente=agente, versao=versao)


def _serie_teste(n: int = 300) -> list[float]:
    """De lado na primeira parte; sobe firme na região de teste (+0,4%/barra,
    bem acima do custo de ida e volta ~0,32%) — faz o challenger aprovar."""
    lado = [100.0, 99.7, 100.0, 100.3, 99.8]
    valores = []
    for i in range(n):
        if i < 180:
            valores.append(lado[i % 5])
        else:
            valores.append(round(valores[-1] * 1.004, 6))
    return valores


def _h_momente(**kw) -> Hipotese:
    base = {
        "id": "H-MOM", "titulo": "momentum simples", "familia": "momentum",
        "tipo_de_regra": "momentum",
        "parametros": (("janela", 5), ("limiar", 0.001)),
        "instrumentos": ("BTC/USDT",),
        "janela_treino_barras": 100, "janela_teste_barras": 120,
        "horizonte_barras": 2,
    }
    base.update(kw)
    return Hipotese(**base)


def _exp(**kw) -> Experimento:
    base = {
        "id": "X-001", "trader": "trader-momentum",
        "champion": _h_momente(parametros=(("janela", 10), ("limiar", 0.05))),
        "challenger": _h_momente(),
    }
    base.update(kw)
    return Experimento(**base)


def _base() -> Hipotese:
    return _h_momente(parametros=(("janela", 10), ("limiar", 0.05)))


def _aprovado(evo: EvolucaoAutonoma, exp: Experimento) -> None:
    evo.registrar_experimento(exp)
    veredito = evo.executar_experimento(exp, {"BTC/USDT": _serie_teste()})
    assert veredito.aprovado, "fixture: o par champion/challenger aprova"


def _evo_revertido() -> tuple[EvolucaoAutonoma, Experimento]:
    """Base B definida, challenger C promovido e deterioração pendente: o
    primeiro vigiar()/ciclo() dispara ROLLBACK C→B."""
    evo = EvolucaoAutonoma()
    exp = _exp()
    _aprovado(evo, exp)
    evo.definir_base("trader-momentum", _base())
    evo.promover(exp, modo="paper")
    for _ in range(AMOSTRA_MINIMA + 2):
        evo.registrar_operacao(_desfecho("trader-momentum", "-0.01"))
    return evo, exp


def _evo_suspenso() -> tuple[EvolucaoAutonoma, Experimento]:
    """Base definida sem versão anterior: a deterioração vira SUSPENSÃO no
    primeiro vigiar(); a evidência aprovada segue no livro, não promovida."""
    evo = EvolucaoAutonoma()
    exp = _exp()
    _aprovado(evo, exp)
    evo.definir_base("trader-momentum", _base())
    for _ in range(AMOSTRA_MINIMA + 2):
        evo.registrar_operacao(_desfecho("trader-momentum", "-0.02"))
    return evo, exp


class TestCicloNaoAnulaRollback:
    def test_ciclo_que_reverte_nao_repromove_no_mesmo_ciclo(self):
        evo, _ = _evo_revertido()
        resumo = evo.ciclo()
        assert any(e["tipo"] == "rollback" for e in resumo["rollback"])
        assert resumo["promocao"] == []
        assert (evo.versao_vigente("trader-momentum")["assinatura"]
                == _base().assinatura())

    def test_evidencia_revertida_continua_bloqueada_nos_ciclos_seguintes(
            self):
        evo, exp = _evo_revertido()
        evo.ciclo()
        resumo2 = evo.ciclo()
        assert resumo2["promocao"] == []
        assert (evo.versao_vigente("trader-momentum")["assinatura"]
                == _base().assinatura())
        resultados = evo.resultados_dos_experimentos()
        assert resultados[exp.assinatura()]["revogado"] is True

    def test_promocao_manual_de_evidencia_revertida_e_recusada(self):
        evo, exp = _evo_revertido()
        evo.vigiar()
        with pytest.raises(ValueError, match="revogad"):
            evo.promover(exp, modo="paper")


class TestTraderSuspensoNaoPromove:
    def test_ciclo_nao_promove_trader_suspenso_no_mesmo_ciclo(self):
        evo, _ = _evo_suspenso()
        resumo = evo.ciclo()
        assert any(e["tipo"] == "suspensao" for e in resumo["suspensao"])
        assert resumo["promocao"] == []
        assert "trader-momentum" in evo.bloqueios()

    def test_promocao_manual_com_trader_suspenso_e_recusada(self):
        evo, exp = _evo_suspenso()
        evo.vigiar()
        with pytest.raises(ValueError, match="suspens"):
            evo.promover(exp, modo="paper")


class TestNovaEvidenciaEOutrosTraders:
    def test_nova_evidencia_permite_promocao_de_novo(self):
        evo, _ = _evo_revertido()
        evo.ciclo()  # rollback + revogação da evidência antiga
        novo = _exp(id="X-007")
        _aprovado(evo, novo)
        resumo = evo.ciclo()
        assert len(resumo["promocao"]) == 1
        promocao = resumo["promocao"][0]
        assert promocao["agent_id"] == "trader-momentum"
        assert promocao["assinatura"] == _h_momente().assinatura()
        promocoes = [e for e in evo.eventos() if e["tipo"] == "promocao"]
        assert promocoes[-1]["evidencia"] == novo.assinatura()

    def test_rollback_de_um_trader_nao_bloqueia_promocao_de_outro(self):
        evo, _ = _evo_revertido()
        outro = _exp(id="X-008", trader="trader-scalping")
        _aprovado(evo, outro)
        resumo = evo.ciclo()
        assert ([p["agent_id"] for p in resumo["promocao"]]
                == ["trader-scalping"])
        assert (evo.versao_vigente("trader-momentum")["assinatura"]
                == _base().assinatura())
        assert (evo.versao_vigente("trader-scalping")["assinatura"]
                == _h_momente().assinatura())
