"""Compartilhamento controlado de conhecimento entre salas (BT-7).

O que se prova aqui:
- O pacote carrega ESTATÍSTICAS rotuladas — nunca certificação.
- Adotar um pacote registra um prior auditável e NÃO forma ninguém:
  a elegibilidade no contexto de destino continua exigindo evidência
  própria daquela sala.
- Pacote com "estado: formada" embutido é RECUSADO na adoção.
- Prior sobrevive ao reinício.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.conhecimento import (
    AVISO_CONHECIMENTO,
    PacoteDeConhecimento,
    adotar_conhecimento,
    campos_de_certificacao,
    montar_pacote,
    resumo_dos_priors,
)
from tradingagents.desk.learning import Desfecho
from tradingagents.desk.salas import (
    ContextoDaSala,
    ContextoDeCertificacao,
    EscolaCentral,
    RegistroDeSalas,
    SalaError,
)

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ESTRATEGIA = "trend_following"


class ArmazemFake:
    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _desfechos(estrategia, retornos, *, inicio=BASE, versao="1"):
    return [
        Desfecho(estrategia=estrategia, regime="tendencia",
                 instrumento="BTC/USDT", retorno=Decimal(str(r)),
                 fechado_em=inicio + timedelta(minutes=i), versao=versao)
        for i, r in enumerate(retornos)
    ]


def _sala_com_evidencia(reg, corretora="binance"):
    sala = reg.abrir_sala(ContextoDaSala(corretora, "paper-1"))
    for d in _desfechos(ESTRATEGIA, [0.010, -0.004, 0.008, 0.012]):
        sala.registrar_desfecho(d)
    for d in _desfechos("reversao_z", [0.002, 0.003]):
        sala.registrar_desfecho(d)
    return sala


class TestPacote:
    def test_pacote_tem_estatisticas_e_nenhuma_certificacao(self):
        sala = _sala_com_evidencia(RegistroDeSalas(armazem=ArmazemFake()))
        pacote = montar_pacote(sala)
        assert pacote.origem == "binance|paper-1|spot|paper"
        assert pacote.corretora == "binance"
        assert pacote.aviso == AVISO_CONHECIMENTO
        chaves = {e["estrategia"] for e in pacote.estatisticas}
        assert chaves == {ESTRATEGIA, "reversao_z"}
        linha = next(e for e in pacote.estatisticas
                     if e["estrategia"] == ESTRATEGIA)
        assert linha["amostras"] == 4
        assert linha["versao"] == "1"
        assert linha["unidade"] == "retorno_liquido"
        assert linha["taxa_acerto"] == pytest.approx(0.75)
        assert campos_de_certificacao(pacote.como_dict()) == []

    def test_filtro_de_estrategias(self):
        sala = _sala_com_evidencia(RegistroDeSalas(armazem=ArmazemFake()))
        pacote = montar_pacote(sala, estrategias=("reversao_z",))
        assert [e["estrategia"] for e in pacote.estatisticas] == ["reversao_z"]

    def test_laboratorio_usa_unidade_de_taxa_de_acerto(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        sala = reg.abrir_sala(ContextoDaSala("iq_option", "lab-1",
                                             "opcoes_binarias", "paper",
                                             Decimal("0.85")))
        sala.registrar_desfecho(_desfechos(ESTRATEGIA, [0.01, -1.0])[0])
        pacote = montar_pacote(sala)
        assert pacote.estatisticas[0]["unidade"] == "taxa_de_acerto"


class TestAdocaoControlada:
    def test_adotar_registra_prior_rotulado_e_permanece(self):
        armazem = ArmazemFake()
        reg = RegistroDeSalas(armazem=armazem)
        origem = _sala_com_evidencia(reg)
        destino = reg.abrir_sala(ContextoDaSala("okx", "paper-1"))
        registro = adotar_conhecimento(destino, montar_pacote(origem))

        assert registro["origem"] == origem.contexto.chave
        assert "recebido_em" in registro
        assert destino.resumo()["priors_conhecimento"] == 1
        priors = resumo_dos_priors(destino)
        assert priors[0]["amostras_totais"] == 6

        novo = RegistroDeSalas(armazem=armazem)   # reinício
        d2 = novo.abrir_sala(ContextoDaSala("okx", "paper-1"))
        assert d2.resumo()["priors_conhecimento"] == 1

    def test_pacote_com_certificacao_embutida_e_recusado(self):
        reg = RegistroDeSalas(armazem=ArmazemFake())
        destino = reg.abrir_sala(ContextoDaSala("okx", "paper-1"))
        contaminado = PacoteDeConhecimento(
            origem="binance|paper-1|spot|paper", corretora="binance",
            mercado="spot", gerado_em=BASE,
            estatisticas=({"estrategia": ESTRATEGIA, "estado": "formada",
                           "amostras": 999},))
        with pytest.raises(SalaError, match="certificação não"):
            adotar_conhecimento(destino, contaminado)
        assert destino.priors_conhecimento == []

    def test_pacote_da_propria_sala_e_recusado(self):
        sala = _sala_com_evidencia(RegistroDeSalas(armazem=ArmazemFake()))
        with pytest.raises(SalaError, match="é eco"):
            adotar_conhecimento(sala, montar_pacote(sala))

    def test_prior_nao_certifica_ninguem(self):
        armazem = ArmazemFake()
        central = EscolaCentral(armazem=armazem)
        reg = RegistroDeSalas(armazem=armazem)
        origem = _sala_com_evidencia(reg, corretora="binance")
        # Muitas estatísticas boas na origem…
        for d in _desfechos(ESTRATEGIA, [0.011] * 300,
                            inicio=BASE + timedelta(hours=1)):
            origem.registrar_desfecho(d)
        # …mas o destino apenas ADOTA o pacote: nada de formação.
        destino = reg.abrir_sala(ContextoDaSala("bybit", "paper-1"))
        adotar_conhecimento(destino, montar_pacote(origem))

        elegivel, motivo = central.elegivel_para_live(
            ESTRATEGIA, "1",
            contexto=ContextoDeCertificacao("bybit", "spot", "BTC/USDT",
                                            "1h"))
        assert elegivel is False
        assert "nenhuma evidência" in motivo

        av = central.avaliar(sala=destino, estrategia=ESTRATEGIA,
                             ativo="BTC/USDT", agora=BASE + timedelta(days=5))
        assert av.amostras == 0
        assert av.estado.value == "coletando"


class TestCamposDeCertificacao:
    def test_detecta_campos_em_qualquer_profundidade(self):
        doc = {"origem": "x", "estatisticas": [
            {"estrategia": "e", "detalhe": {"formado_em": "2026-01-01"}}]}
        assert campos_de_certificacao(doc) == ["formado_em"]

    def test_documento_limpo_passa(self):
        assert campos_de_certificacao(
            {"origem": "x", "estatisticas": [{"amostras": 10}]}) == []
