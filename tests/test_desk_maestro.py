"""Maestro Central multi-sala (BT-8).

O que se prova:
- Um dono por instrumento em TODAS as salas: a segunda candidata no mesmo
  ativo é negada, com o motivo citando quem já é dono.
- Conflito entre candidatas novas é resolvido por prioridade declarada —
  uma vence, a outra é negada; o resultado é determinístico.
- Pausa global exige motivo, bloqueia tudo, persiste no reinício e alimenta
  o `vigia` do orquestrador da sala.
- Teto de exposição global soma as salas e, no teto, novas entradas param.
- Nada disso opera: o Maestro decide e bloqueia; LIVE continua fora.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from tradingagents.desk.maestro import MaestroCentral, MaestroError
from tradingagents.desk.salas import ContextoDaSala, RegistroDeSalas


class ArmazemFake:
    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _cenario():
    armazem = ArmazemFake()
    registro = RegistroDeSalas(armazem=armazem)
    binance = registro.abrir_sala(ContextoDaSala("binance", "paper-1"))
    okx = registro.abrir_sala(ContextoDaSala("okx", "paper-1"))
    return armazem, registro, binance, okx


class TestConflitos:
    def test_segunda_sala_no_mesmo_ativo_e_negada(self):
        _, registro, binance, okx = _cenario()
        binance.registrar_entrada("BTC/USDT", 1, Decimal("100"),
                                  agente="t1", estrategia="trend")
        maestro = MaestroCentral(registro)
        [d] = maestro.resolver_entradas([
            {"sala": okx, "instrumento": "BTC/USDT", "agente": "t2"}])
        assert d.concedido is False
        assert "já pertence à sala binance|paper-1|spot|paper" in d.motivo
        assert "um dono por instrumento" in d.motivo

    def test_mesma_sala_nao_duplica_posicao_via_maestro(self):
        _, registro, binance, _ = _cenario()
        binance.registrar_entrada("BTC/USDT", 1, Decimal("100"), agente="t1")
        maestro = MaestroCentral(registro)
        [d] = maestro.resolver_entradas([
            {"sala": binance, "instrumento": "BTC/USDT", "agente": "t1"}])
        assert d.concedido is False
        assert "já tem posição aberta" in d.motivo

    def test_instrumento_livre_passa(self):
        _, registro, _, okx = _cenario()
        maestro = MaestroCentral(registro)
        [d] = maestro.resolver_entradas([
            {"sala": okx, "instrumento": "ETH/USDT", "agente": "t2"}])
        assert d.concedido is True
        assert "livre" in d.motivo

    def test_conflito_entre_candidatas_novas_por_prioridade(self):
        _, registro, binance, okx = _cenario()
        maestro = MaestroCentral(
            registro,
            prioridades={"okx|paper-1|spot|paper": 5})
        decisoes = maestro.resolver_entradas([
            {"sala": binance, "instrumento": "ETH/USDT", "agente": "t1"},
            {"sala": okx, "instrumento": "ETH/USDT", "agente": "t2"}])
        por_sala = {d.sala: d for d in decisoes}
        assert por_sala["okx|paper-1|spot|paper"].concedido is True
        perdedora = por_sala["binance|paper-1|spot|paper"]
        assert perdedora.concedido is False
        assert "prioridade" in perdedora.motivo

        # Determinístico: repetir a decisão dá exatamente o mesmo resultado.
        denovo = maestro.resolver_entradas([
            {"sala": binance, "instrumento": "ETH/USDT", "agente": "t1"},
            {"sala": okx, "instrumento": "ETH/USDT", "agente": "t2"}])
        assert [(d.sala, d.concedido) for d in denovo] == \
            [(d.sala, d.concedido) for d in decisoes]

    def test_conflitos_lista_ativos_em_mais_de_uma_sala(self):
        _, registro, binance, okx = _cenario()
        binance.registrar_entrada("BTC/USDT", 1, Decimal("100"))
        okx.registrar_entrada("BTC/USDT", 2, Decimal("50"))
        maestro = MaestroCentral(registro)
        conflitos = maestro.conflitos()
        assert len(conflitos) == 1
        assert conflitos[0]["instrumento"] == "BTC/USDT"
        assert set(conflitos[0]["salas"]) == {"binance|paper-1|spot|paper",
                                              "okx|paper-1|spot|paper"}


class TestPausaGlobal:
    def test_pausa_exige_motivo_e_bloqueia_tudo(self):
        _, registro, binance, okx = _cenario()
        maestro = MaestroCentral(registro)
        with pytest.raises(MaestroError, match="motivo"):
            maestro.pausar_global("")
        maestro.pausar_global("incidente de risco no feed")
        assert maestro.pausado is True

        decisoes = maestro.resolver_entradas([
            {"sala": binance, "instrumento": "ETH/USDT", "agente": "t1"},
            {"sala": okx, "instrumento": "SOL/USDT", "agente": "t2"}])
        assert all(not d.concedido for d in decisoes)
        assert all("pausa global" in d.motivo for d in decisoes)

        # Vigia pronto para o orquestrador da sala.
        vigia = maestro.vigia_de(binance, ("trader-trend", "trader-mom"))
        bloqueios = vigia()
        assert set(bloqueios) == {"trader-trend", "trader-mom"}
        assert "Maestro Central: pausa global" in bloqueios["trader-trend"]

        maestro.retomar_global("incidente resolvido")
        assert maestro.pausado is False
        assert vigia() == {}

    def test_pausa_persiste_no_reinicio_com_historico(self):
        armazem, registro, _, _ = _cenario()
        maestro = MaestroCentral(registro)
        maestro.pausar_global("manutenção no conector")
        novo = MaestroCentral(RegistroDeSalas(armazem=armazem))
        assert novo.pausado is True
        assert "manutenção" in novo.relatorio()["pausa_global"]["motivo"]
        assert [e["evento"] for e in
                novo.relatorio()["pausa_global"]["eventos"]] == ["pausada"]


class TestExposicaoGlobal:
    def test_teto_soma_as_salas_e_bloqueia_ao_alcancar(self):
        _, registro, binance, okx = _cenario()
        binance.registrar_entrada("BTC/USDT", 1, Decimal("100"))
        okx.registrar_entrada("ETH/USDT", 1, Decimal("80"))
        maestro = MaestroCentral(registro,
                                 teto_exposicao_global=Decimal("180"))
        exp = maestro.exposicao_global()
        assert exp["total"] == "180"
        assert exp["excedido"] is True

        [d] = maestro.resolver_entradas([
            {"sala": okx, "instrumento": "SOL/USDT", "agente": "t2"}])
        assert d.concedido is False
        assert "exposição global" in d.motivo

        _, motivo = maestro.pode_entrar(binance)
        assert "teto" in motivo

    def test_teto_invalido_recusa_e_sem_teto_sempre_libera(self):
        _, registro, _, _ = _cenario()
        with pytest.raises(MaestroError, match="positivo"):
            MaestroCentral(registro, teto_exposicao_global=Decimal("0"))
        maestro = MaestroCentral(registro)
        assert maestro.exposicao_global()["teto"] is None
        assert maestro.exposicao_global()["excedido"] is False
