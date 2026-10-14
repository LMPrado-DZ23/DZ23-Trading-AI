"""E6 — Fundação Multimercado (task #61).

Contrato testado:
- Registro de classes de ativo do escopo (cripto spot/futuros; B3 ações/FIIs/
  ETFs; US ações/ETFs) com metadados de política (moeda de cotação, lote,
  tick, sessão/calendário, liquidação, taxas) — SEM prometer execução: classe
  sem homologação não abre sala nem migra histórico.
- Sessões/calendários fail-closed: fim de semana, feriado e fora da janela
  fecham; classe desconhecida fecha; cripto 24/7 não muda.
- Horário de verão dos EUA por regra (2º domingo de março → 1º domingo de
  novembro), sem depender de tzdata; Brasil sem DST (UTC-3 fixo).
- Salas: classe é dimensão nova preservando as chaves atuais (4 partes);
  migração da sala legada é EXPLÍCITA, copia a prova viva (saldo, taxas,
  desfechos, priors, livro-razão) e a chave antiga redireciona para a nova
  — nada de estado duplicado nem resultado em duas contas.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.costs import TAXAS_TAKER
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.mercados import (
    CLASSES,
    ClasseDeAtivo,
    MercadoError,
    SessaoDeMercado,
    classe_de,
    lote_valido,
    mercado_aberto,
    registrar_classe,
)
from tradingagents.desk.salas import (
    ADAPTADORES,
    AdaptadorDeCorretora,
    ContextoDaSala,
    RegistroDeSalas,
    SalaError,
    registrar_adaptador,
)


class ArmazemFake:
    """Armazém em memória com ida-e-volta por JSON, como o real."""

    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _utc(ano: int, mes: int, dia: int, hora: int = 0,
         minuto: int = 0) -> datetime:
    return datetime(ano, mes, dia, hora, minuto, tzinfo=timezone.utc)


def _classe(**kw) -> ClasseDeAtivo:
    base = {
        "nome": "classe_teste", "mercado": "spot", "moeda": "USDT",
        "lote_minimo": Decimal("1"), "passo_lote": Decimal("1"),
        "tick": Decimal("0.01"), "liquidacao": "D+0",
        "taxas_por_lado": Decimal("0.001"), "spread_tipico": Decimal("0.0002"),
        "sessao": SessaoDeMercado(fuso="UTC"),
    }
    base.update(kw)
    return ClasseDeAtivo(**base)


# ------------------------------------------------------------ registro
def test_registro_cobre_as_sete_classes_do_escopo():
    esperadas = {"cripto_spot", "cripto_futuros", "b3_acoes", "b3_fiis",
                 "b3_etfs", "us_acoes", "us_etfs"}
    assert esperadas <= set(CLASSES)
    for cl in CLASSES.values():
        assert cl.moeda, f"{cl.nome} sem moeda de cotação"
        assert cl.liquidacao, f"{cl.nome} sem liquidação"
        assert cl.lote_minimo > 0 and cl.passo_lote > 0 and cl.tick > 0
        assert cl.custo_ida_e_volta >= 0


def test_registrar_classe_duplicada_exige_substituir():
    with pytest.raises(MercadoError, match="substituir"):
        registrar_classe(_classe(nome="cripto_spot"))


def test_registrar_classe_com_mercado_ou_metadados_invalidos_e_recusado():
    with pytest.raises(MercadoError, match="desconhecido"):
        registrar_classe(_classe(nome="doge_spot", mercado="dogecoin"))
    with pytest.raises(MercadoError, match="lote"):
        registrar_classe(_classe(nome="x_lote", lote_minimo=Decimal("0")))
    assert "x_lote" not in CLASSES and "doge_spot" not in CLASSES


def test_classe_desconhecida_e_erro_e_mercado_aberto_falha_fechado():
    with pytest.raises(MercadoError, match="desconhecida"):
        classe_de("doge_spot")
    aberto, motivo = mercado_aberto("doge_spot", _utc(2026, 1, 5, 12, 0))
    assert aberto is False
    assert "desconhecida" in motivo


# ------------------------------------------------------------ sessões
def test_cripto_e_24x7_mesmo_no_fim_de_semana():
    sabado = _utc(2026, 1, 3, 12, 0)          # sábado
    domingo_madrugada = _utc(2026, 1, 4, 3, 0)
    for nome in ("cripto_spot", "cripto_futuros"):
        assert mercado_aberto(nome, sabado)[0] is True
        assert mercado_aberto(nome, domingo_madrugada)[0] is True


def test_b3_bloqueia_fim_de_semana_feriado_e_fora_da_janela():
    # sábado 2026-01-03 11:00 BRT — mercado fechado
    assert mercado_aberto("b3_acoes", _utc(2026, 1, 3, 14, 0))[0] is False
    # segunda 2026-01-05: 09:00 BRT fechado, 10:00 BRT aberto, 17:30 fechado
    assert mercado_aberto("b3_acoes", _utc(2026, 1, 5, 12, 0))[0] is False
    assert mercado_aberto("b3_acoes", _utc(2026, 1, 5, 13, 0))[0] is True
    assert mercado_aberto("b3_acoes", _utc(2026, 1, 5, 20, 30))[0] is False
    # 2026-12-25 é sexta-feira — feriado: fechado com motivo explícito
    aberto, motivo = mercado_aberto("b3_acoes", _utc(2026, 12, 25, 15, 0))
    assert aberto is False and "feriado" in motivo


def test_us_est_versus_edt_por_regra_de_horario_de_verao():
    # inverno (EST, UTC-5): 09:00 fecha, 10:00 abre
    assert mercado_aberto("us_acoes", _utc(2026, 1, 15, 14, 0))[0] is False
    assert mercado_aberto("us_acoes", _utc(2026, 1, 15, 15, 0))[0] is True
    # verão (EDT, UTC-4): 09:00 fecha, 09:30 abre
    assert mercado_aberto("us_acoes", _utc(2026, 7, 15, 13, 0))[0] is False
    assert mercado_aberto("us_acoes", _utc(2026, 7, 15, 13, 30))[0] is True
    # fronteira de março: 2º domingo de 2026 é 08/03 (07:00 UTC)
    # sexta 06/03 ainda EST (09:30 = 14:30 UTC); segunda 09/03 já EDT
    assert mercado_aberto("us_acoes", _utc(2026, 3, 6, 14, 30))[0] is True
    assert mercado_aberto("us_acoes", _utc(2026, 3, 9, 13, 30))[0] is True
    # fronteira de novembro: 1º domingo de 2026 é 01/11 (06:00 UTC)
    # segunda 02/11 já EST: 09:30 EDT seria 13:30 UTC, mas agora fecha
    assert mercado_aberto("us_acoes", _utc(2026, 11, 2, 13, 30))[0] is False
    assert mercado_aberto("us_acoes", _utc(2026, 11, 2, 14, 30))[0] is True
    # feriado dos EUA: MLK, segunda 2026-01-19, fechado com motivo
    aberto, motivo = mercado_aberto("us_acoes", _utc(2026, 1, 19, 15, 0))
    assert aberto is False and "feriado" in motivo


# ------------------------------------------------------------ lote e custo
def test_lote_valido_por_classe():
    assert lote_valido("b3_acoes", Decimal("100")) is True
    assert lote_valido("b3_acoes", Decimal("150")) is False   # fora do passo
    assert lote_valido("b3_acoes", Decimal("50")) is False    # abaixo do mínimo
    assert lote_valido("us_acoes", Decimal("1")) is True
    assert lote_valido("cripto_spot", Decimal("0.5")) is True
    assert lote_valido("cripto_spot", Decimal("0.000005")) is False
    with pytest.raises(MercadoError, match="desconhecida"):
        lote_valido("nao_existe", Decimal("1"))


def test_custo_da_classe_e_duas_taxas_mais_spread():
    cl = classe_de("cripto_spot")
    assert cl.taxas_por_lado == TAXAS_TAKER["binance"]
    assert cl.custo_ida_e_volta == cl.taxas_por_lado * 2 + cl.spread_tipico
    assert cl.custo_ida_e_volta == Decimal("0.0022")
    assert classe_de("us_acoes").custo_ida_e_volta == Decimal("0.0002")


# ------------------------------------------------------------ salas
def test_chave_legada_preservada_e_chave_com_classe_estendida():
    legada = ContextoDaSala("binance", "conta-1", "spot", "paper")
    assert legada.chave == "binance|conta-1|spot|paper"
    com_classe = ContextoDaSala("binance", "conta-1", "spot", "paper",
                                classe="cripto_spot")
    assert com_classe.chave == "binance|conta-1|spot|paper|cripto_spot"
    assert ContextoDaSala.de_dict(legada.como_dict()).chave == legada.chave
    assert (ContextoDaSala.de_dict(com_classe.como_dict()).chave
            == com_classe.chave)


def test_sala_com_classe_homologada_abre_e_mantem_24x7():
    reg = RegistroDeSalas(ArmazemFake())
    sala = reg.abrir_sala(ContextoDaSala("binance", "conta-1", "spot",
                                         "paper", classe="cripto_spot"))
    assert sala.contexto.classe == "cripto_spot"
    assert sala.resumo()["classe"] == "cripto_spot"
    aberto, _ = sala.pode_operar(_utc(2026, 1, 3, 12, 0))   # sábado
    assert aberto is True
    legada = reg.abrir_sala(ContextoDaSala("binance", "conta-2", "spot",
                                           "paper"))
    assert legada.resumo()["classe"] is None
    assert legada.pode_operar(_utc(2026, 1, 3, 12, 0))[0] is True


def test_sala_recusa_classe_incoerente_ou_desconhecida():
    reg = RegistroDeSalas(ArmazemFake())
    with pytest.raises(SalaError, match="mercado"):
        reg.abrir_sala(ContextoDaSala("binance", "conta-1", "spot", "paper",
                                      classe="b3_acoes"))
    with pytest.raises(SalaError, match="desconhecida"):
        reg.abrir_sala(ContextoDaSala("binance", "conta-1", "spot", "paper",
                                      classe="lua_spot"))


def test_classe_nao_homologada_e_recusada_mesmo_com_adaptador():
    # O adaptador de teste suporta 'acoes', mas a classe b3_acoes não tem
    # execução homologada — camada de política não promete execução.
    registrar_adaptador(AdaptadorDeCorretora("b3_teste", ("acoes",)))
    try:
        reg = RegistroDeSalas(ArmazemFake())
        with pytest.raises(SalaError, match="homologada"):
            reg.abrir_sala(ContextoDaSala("b3_teste", "conta-1", "acoes",
                                          "paper", classe="b3_acoes"))
    finally:
        ADAPTADORES.pop("b3_teste", None)


def test_migracao_explicita_copia_prova_viva_e_redireciona_legado():
    armazem = ArmazemFake()
    reg = RegistroDeSalas(armazem)
    legada_ctx = ContextoDaSala("binance", "conta-1", "spot", "paper")
    livro = Ledger(opening_cash=Decimal("1000"))
    sala = reg.abrir_sala(legada_ctx, ledger=livro)
    sala.registrar_entrada("BTC/USDT", Decimal("1"), Decimal("100"),
                           agente="trader-trend", estrategia="trend")
    sala.registrar_saida("BTC/USDT", Decimal("110"))
    saldo_antes = sala.saldo_realizado
    taxas_antes = sala.taxas_acumuladas

    nova = reg.migrar_sala(legada_ctx, "cripto_spot")

    assert nova.contexto.chave == "binance|conta-1|spot|paper|cripto_spot"
    assert nova.saldo_realizado == saldo_antes
    assert nova.taxas_acumuladas == taxas_antes
    assert len(nova.desfechos) == 1
    assert nova.resumo()["classe"] == "cripto_spot"
    assert nova.ledger is not None
    assert (len(nova.ledger.export_transactions())
            == len(livro.export_transactions()))
    # a chave antiga resolve para a MESMA sala viva (redireciono, sem
    # estado duplicado) e o catálogo só lista a sala viva
    assert reg.abrir_sala(legada_ctx).contexto.chave == nova.contexto.chave
    assert set(reg.salas) == {nova.contexto.chave}
    # recarrega do armazém: migração é persistente e idempotente na leitura
    reg_novo = RegistroDeSalas(armazem)
    assert set(reg_novo.salas) == {nova.contexto.chave}
    retomada = reg_novo.salas[nova.contexto.chave]
    assert retomada.saldo_realizado == saldo_antes
    assert len(retomada.desfechos) == 1


def test_migracao_recusa_repeticao_posicao_aberta_e_classe_invalida():
    armazem = ArmazemFake()
    reg = RegistroDeSalas(armazem)
    legada = ContextoDaSala("binance", "conta-1", "spot", "paper")
    reg.abrir_sala(legada)
    reg.migrar_sala(legada, "cripto_spot")
    with pytest.raises(SalaError, match="já foi migrada"):
        reg.migrar_sala(legada, "cripto_spot")

    # posição aberta impede a migração: uma posição viva tem um dono só
    com_posicao = ContextoDaSala("binance", "conta-2", "spot", "paper")
    sala = reg.abrir_sala(com_posicao)
    sala.registrar_entrada("ETH/USDT", Decimal("1"), Decimal("100"))
    with pytest.raises(SalaError, match="posições fechadas"):
        reg.migrar_sala(com_posicao, "cripto_spot")

    # classe incoerente / desconhecida / origem inexistente
    limpa = ContextoDaSala("binance", "conta-3", "spot", "paper")
    reg.abrir_sala(limpa)
    with pytest.raises(SalaError, match="mercado"):
        reg.migrar_sala(limpa, "b3_acoes")
    with pytest.raises(SalaError, match="desconhecida"):
        reg.migrar_sala(limpa, "classe_fantasma")
    fantasma = ContextoDaSala("binance", "conta-9", "spot", "paper")
    with pytest.raises(SalaError, match="não existe"):
        reg.migrar_sala(fantasma, "cripto_spot")
