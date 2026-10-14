"""Copiloto Manual (E3): posições manuais, monitor READ-ONLY e alertas.

O que estes testes protegem (contrato do modo manual):
- Nenhum caminho executa ordem: o exe/módulo não tem broker de escrita,
  e o teste estrutural varre o código-fonte por APIs proibidas.
- Preço atingir ALVO gera alerta — jamais encerra a posição. CLOSED só
  com evento registrado pelo dono.
- Preço executado é obrigatório no evento: cotação de tela não substitui.
- P&L honesto: média ponderada de aumentos, perdas/ganhos por direção,
  custos acumulados descontados.
- Feed velho (FEED_STALE) ou ausente (CONNECTION_LOST) NÃO avaliam
  política; buraco grande de acompanhamento vira RECONCILIATION_REQUIRED.
- Persistência round-trip completa (Decimals como string, UTC ISO).
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tradingagents.desk.manual import (
    SCHEMA,
    ConflitoDeVersao,
    CopilotoManual,
    ErroManual,
    PoliticaManual,
    PosicaoNaoEncontrada,
    QuoteManual,
)
from tradingagents.desk.models import utcnow
from tradingagents.desk.notificacao_viva import FilaDeAlertas

INSTR = "BTC/USDT"


def criar(cop: CopilotoManual, **kw) -> object:
    base = {"instrumento": INSTR, "direcao": "long", "quantidade": 10,
            "preco_entrada": 100, "stop": 90, "alvos": (120,), "intervalo_s": 60}
    base.update(kw)
    pos, _ = cop.criar_posicao(**base)
    return pos


def quote(preco, agora, *, atraso_s=60, timeframe="1h") -> QuoteManual:
    return QuoteManual(preco=Decimal(str(preco)),
                       cutoff=agora - timedelta(seconds=atraso_s),
                       recebido_em=agora, fonte="teste", timeframe=timeframe)


# ---------------------------------------------------------------------------
# criação e validações
# ---------------------------------------------------------------------------

def test_criar_posicao_normaliza_direcao_e_campos():
    cop = CopilotoManual()
    pos, aplicado = cop.criar_posicao(instrumento="btc/usdt", direcao="compra",
                                      quantidade="10", preco_entrada="100")
    assert aplicado is True
    assert pos.instrumento == "BTC/USDT" and pos.direcao == "long"
    assert pos.quantidade == Decimal("10") and pos.quantidade_aberta == Decimal("10")
    assert pos.preco_entrada == Decimal("100")
    assert pos.estado_financeiro == "ABERTA" and pos.estado_monitor == "MONITORING"
    assert pos.version == 1 and pos.high_water == Decimal("100")
    assert pos.posicao_id.startswith("mp_")

    pos2, _ = cop.criar_posicao(instrumento="ETH/USDT", direcao="venda",
                                quantidade=2, preco_entrada=50)
    assert pos2.direcao == "short"


def test_criar_posicao_recusa_entradas_invalidas():
    cop = CopilotoManual()
    with pytest.raises(ErroManual):
        cop.criar_posicao(instrumento="", direcao="long", quantidade=1,
                          preco_entrada=10)
    with pytest.raises(ErroManual):
        cop.criar_posicao(instrumento=INSTR, direcao="lateral", quantidade=1,
                          preco_entrada=10)
    with pytest.raises(ErroManual):
        cop.criar_posicao(instrumento=INSTR, direcao="long", quantidade=0,
                          preco_entrada=10)
    with pytest.raises(ErroManual):
        cop.criar_posicao(instrumento=INSTR, direcao="long", quantidade=1,
                          preco_entrada="abc")
    with pytest.raises(ErroManual):
        cop.criar_posicao(instrumento=INSTR, direcao="long", quantidade=1,
                          preco_entrada=10, origem_preco="chute")


def test_idempotencia_de_criacao():
    cop = CopilotoManual()
    p1, a1 = cop.criar_posicao(instrumento=INSTR, direcao="long", quantidade=1,
                               preco_entrada=10, idempotency_key="k1")
    p2, a2 = cop.criar_posicao(instrumento=INSTR, direcao="long", quantidade=1,
                               preco_entrada=10, idempotency_key="k1")
    assert a1 is True and a2 is False
    assert p1.posicao_id == p2.posicao_id and len(cop.posicoes) == 1


def test_politica_valida_por_direcao():
    cop = CopilotoManual()
    # long: stop acima da entrada é recusado; alvo abaixo, também.
    with pytest.raises(ErroManual):
        criar(cop, stop=101)
    with pytest.raises(ErroManual):
        criar(cop, stop=None, alvos=(99,))
    with pytest.raises(ErroManual):
        criar(cop, stop=None, alvos=(), invalidacao=101)
    # curta: espelho.
    with pytest.raises(ErroManual):
        criar(cop, direcao="short", stop=99)
    with pytest.raises(ErroManual):
        criar(cop, direcao="short", stop=None, alvos=(101,))
    # intervalo fora da faixa segura.
    with pytest.raises(ErroManual):
        criar(cop, intervalo_s=5)
    with pytest.raises(ErroManual):
        criar(cop, intervalo_s=99999)
    with pytest.raises(ErroManual):
        criar(cop, stop=None, alvos=(), trailing_pct=0)


# ---------------------------------------------------------------------------
# eventos e P&L
# ---------------------------------------------------------------------------

def test_pnl_long_e_short_desconta_custos():
    cop = CopilotoManual()
    pos = criar(cop, custos_entrada="2")
    # long: +10 por unidade em 10 unidades = +100; líquido = 100-2.
    assert pos.lucro_bruto(Decimal("110")) == Decimal("100")
    assert pos.lucro_liquido(Decimal("110")) == Decimal("98")

    p2, _ = cop.criar_posicao(instrumento="ETH/USDT", direcao="short",
                              quantidade=5, preco_entrada=100, custos_entrada=1)
    # short: preço cai → lucro.
    assert p2.lucro_bruto(Decimal("90")) == Decimal("50")
    assert p2.lucro_liquido(Decimal("90")) == Decimal("49")


def test_aumento_usa_media_ponderada():
    cop = CopilotoManual()
    pos = criar(cop, quantidade=10, preco_entrada=100, custos_entrada=0)
    cop.registrar_evento(pos.posicao_id, tipo="aumento", quantidade=10, preco=110)
    assert pos.preco_entrada == Decimal("105")
    assert pos.quantidade == Decimal("20") and pos.quantidade_aberta == Decimal("20")
    assert pos.version == 2


def test_reducao_parcial_calcula_realizado_e_encerra_ao_zerar():
    cop = CopilotoManual()
    pos = criar(cop, custos_entrada=0)
    ev, aplicado = cop.registrar_evento(pos.posicao_id, tipo="reducao",
                                        quantidade=4, preco=120, custos=1)
    assert aplicado is True and ev.preco == Decimal("120")
    # (120-100)*4 - 1 = 79
    assert pos.lucro_realizado_liquido == Decimal("79")
    assert pos.quantidade_aberta == Decimal("6")
    assert pos.estado_financeiro == "PARCIAL"
    assert pos.custos_total == Decimal("1")

    cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=6, preco=130)
    assert pos.quantidade_aberta == Decimal("0")
    assert pos.estado_financeiro == "ENCERRADA"
    assert pos.estado_monitor == "MONITORING_STOPPED"


def test_encerramento_fecha_o_resto_e_recusa_parcial():
    cop = CopilotoManual()
    pos = criar(cop)
    with pytest.raises(ErroManual):
        cop.registrar_evento(pos.posicao_id, tipo="encerramento",
                             quantidade=4, preco=110)
    cop.registrar_evento(pos.posicao_id, tipo="encerramento", preco=110)
    assert pos.quantidade_aberta == Decimal("0")
    assert pos.estado_financeiro == "ENCERRADA"
    assert pos.encerrada_em is not None
    # posição encerrada não aceita eventos novos nem nova política.
    with pytest.raises(ErroManual):
        cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=1,
                             preco=110)
    with pytest.raises(ErroManual):
        cop.definir_politica(pos.posicao_id, PoliticaManual(stop=Decimal("90")))


def test_reducao_acima_do_aberto_e_recusada():
    cop = CopilotoManual()
    pos = criar(cop)
    with pytest.raises(ErroManual):
        cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=11,
                             preco=110)


def test_preco_executado_obrigatorio_no_evento():
    cop = CopilotoManual()
    pos = criar(cop)
    with pytest.raises(ErroManual):
        cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=1)


def test_conflito_de_versao_protege_concorrencia():
    cop = CopilotoManual()
    pos = criar(cop)
    cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=1, preco=110)
    with pytest.raises(ConflitoDeVersao):
        cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=1,
                             preco=110, expected_version=1)
    cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=1,
                         preco=110, expected_version=2)
    assert pos.version == 3


def test_idempotencia_de_evento():
    cop = CopilotoManual()
    pos = criar(cop)
    e1, a1 = cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=1,
                                  preco=110, idempotency_key="ev-1")
    e2, a2 = cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=1,
                                  preco=110, idempotency_key="ev-1")
    assert a1 is True and a2 is False and e1.evento_id == e2.evento_id
    assert pos.version == 2 and len(pos.eventos) == 1


def test_posicao_inexistente():
    cop = CopilotoManual()
    with pytest.raises(PosicaoNaoEncontrada):
        cop.obter("mp_nao_existe")


# ---------------------------------------------------------------------------
# monitor READ-ONLY: alertas, estados e reconciliação
# ---------------------------------------------------------------------------

def test_alvo_atingido_alerta_e_nao_encerra():
    cop = CopilotoManual()
    pos = criar(cop, stop=90, alvos=(120,))
    agora = utcnow()
    resumo = cop.avaliar_posicoes({INSTR: quote(125, agora)}, agora=agora,
                                  forcar=True)
    tipos = [a["tipo"] for a in resumo["alertas"]]
    assert "manual_alvo_atingido" in tipos
    # O contrato central: nada foi encerrado por preço.
    assert pos.estado_financeiro == "ABERTA"
    assert pos.quantidade_aberta == Decimal("10")
    assert pos.encerrada_em is None
    assert resumo["financeiras_fechadas"] == 0


def test_stop_acionado_alerta_critico():
    cop = CopilotoManual()
    pos = criar(cop, stop=90)
    agora = utcnow()
    resumo = cop.avaliar_posicoes({INSTR: quote(89, agora)}, agora=agora, forcar=True)
    (alerta,) = [a for a in resumo["alertas"] if a["tipo"] == "manual_stop_atingido"]
    assert alerta["severidade"] == "critico"
    assert pos.estado_financeiro == "ABERTA"  # alerta ≠ execução


def test_stop_short_acionado():
    cop = CopilotoManual()
    pos = criar(cop, direcao="short", stop=110, alvos=(80,))
    agora = utcnow()
    resumo = cop.avaliar_posicoes({INSTR: quote(111, agora)}, agora=agora, forcar=True)
    assert any(a["tipo"] == "manual_stop_atingido" for a in resumo["alertas"])
    assert pos.estado_financeiro == "ABERTA"


def test_invalidacao_e_expiracao():
    cop = CopilotoManual()
    agora = utcnow()
    criar(cop, stop=None, alvos=(), invalidacao=95,
          expira_em=agora - timedelta(hours=1))
    resumo = cop.avaliar_posicoes({INSTR: quote(94, agora)}, agora=agora, forcar=True)
    tipos = {a["tipo"] for a in resumo["alertas"]}
    assert "manual_tese_invalidada" in tipos
    assert "manual_tese_expirada" in tipos


def test_trailing_acionado_com_high_water():
    cop = CopilotoManual()
    pos = criar(cop, stop=None, alvos=(), trailing_pct=5)
    t0 = utcnow()
    cop.avaliar_posicoes({INSTR: quote(120, t0)}, agora=t0, forcar=True)
    assert pos.high_water == Decimal("120")
    t1 = t0 + timedelta(minutes=2)
    resumo = cop.avaliar_posicoes({INSTR: quote(113, t1)}, agora=t1, forcar=True)
    assert any(a["tipo"] == "manual_trailing_acionado" for a in resumo["alertas"])
    assert pos.estado_financeiro == "ABERTA"


def test_feed_stale_nao_avalia_nem_alerta_stop():
    cop = CopilotoManual()
    pos = criar(cop, stop=90)
    agora = utcnow()
    # cutoffs 4h atrás: > 3 passos de 1h — decisão com preço velho proibida.
    resumo = cop.avaliar_posicoes({INSTR: quote(85, agora, atraso_s=14400)},
                                  agora=agora, forcar=True)
    assert resumo["avaliadas"] == 0 and resumo["stale"] == [pos.posicao_id]
    assert pos.estado_monitor == "FEED_STALE"
    assert pos.snapshots == []
    assert all(a["tipo"] != "manual_stop_atingido" for a in resumo["alertas"])


def test_sem_cotacao_vira_connection_lost():
    cop = CopilotoManual()
    pos = criar(cop)
    agora = utcnow()
    resumo = cop.avaliar_posicoes({}, agora=agora, forcar=True)
    assert resumo["sem_preco"] == [pos.posicao_id]
    assert pos.estado_monitor == "CONNECTION_LOST"
    assert any(a["tipo"] == "manual_sem_cotacao" for a in resumo["alertas"])


def test_reconexao_rapida_volta_a_monitoring():
    cop = CopilotoManual()
    pos = criar(cop)
    t0 = utcnow()
    cop.avaliar_posicoes({}, agora=t0, forcar=True)
    t1 = t0 + timedelta(minutes=2)          # < reconciliacao_s (900)
    resumo = cop.avaliar_posicoes({INSTR: quote(105, t1)}, agora=t1, forcar=True)
    assert resumo["reconciliadas"] == [pos.posicao_id]
    assert pos.estado_monitor == "MONITORING"
    assert resumo["avaliadas"] == 1


def test_gap_longo_exige_reconciliacao_antes_de_avaliar():
    cop = CopilotoManual()
    pos = criar(cop)
    t0 = utcnow()
    cop.avaliar_posicoes({INSTR: quote(105, t0)}, agora=t0, forcar=True)
    t1 = t0 + timedelta(seconds=1800)       # > reconciliacao_s (900)
    resumo = cop.avaliar_posicoes({INSTR: quote(105, t1)}, agora=t1, forcar=True)
    assert pos.estado_monitor == "RECONCILIATION_REQUIRED"
    assert resumo["reconciliacao_pendente"] == [pos.posicao_id]
    assert resumo["avaliadas"] == 0         # nada avaliado com o buraco
    # Ainda pendente: outro tick não avalia; retomar() libera.
    t2 = t1 + timedelta(seconds=60)
    resumo2 = cop.avaliar_posicoes({INSTR: quote(105, t2)}, agora=t2, forcar=True)
    assert resumo2["avaliadas"] == 0
    cop.retomar(pos.posicao_id)
    t3 = t2 + timedelta(seconds=60)
    resumo3 = cop.avaliar_posicoes({INSTR: quote(105, t3)}, agora=t3, forcar=True)
    assert resumo3["avaliadas"] == 1 and pos.estado_monitor == "MONITORING"


def test_pausa_mantem_posicao_fora_do_monitor():
    cop = CopilotoManual()
    pos = criar(cop)
    cop.pausar(pos.posicao_id, motivo="revisão manual")
    agora = utcnow()
    resumo = cop.avaliar_posicoes({INSTR: quote(85, agora)}, agora=agora, forcar=True)
    assert resumo["avaliadas"] == 0 and pos.snapshots == []
    assert pos.estado_monitor == "PAUSED"
    cop.retomar(pos.posicao_id)
    resumo2 = cop.avaliar_posicoes({INSTR: quote(105, agora)}, agora=agora, forcar=True)
    assert resumo2["avaliadas"] == 1


def test_intervalo_entre_avaliacoes_e_respeitado():
    cop = CopilotoManual()
    criar(cop, intervalo_s=300)
    t0 = utcnow()
    r1 = cop.avaliar_posicoes({INSTR: quote(105, t0)}, agora=t0)
    assert r1["avaliadas"] == 1
    t1 = t0 + timedelta(seconds=120)        # < 300: pula
    r2 = cop.avaliar_posicoes({INSTR: quote(105, t1)}, agora=t1)
    assert r2["avaliadas"] == 0
    t2 = t0 + timedelta(seconds=301)
    r3 = cop.avaliar_posicoes({INSTR: quote(105, t2)}, agora=t2)
    assert r3["avaliadas"] == 1
    # forcar=True ignora o intervalo (tick manual).
    r4 = cop.avaliar_posicoes({INSTR: quote(105, t2)}, agora=t2, forcar=True)
    assert r4["avaliadas"] == 1


def test_snapshots_limitados():
    cop = CopilotoManual()
    pos = criar(cop)
    t0 = utcnow()
    for i in range(250):
        cop.avaliar_posicoes({INSTR: quote(105 + i * 0.01, t0)},
                             agora=t0 + timedelta(seconds=i), forcar=True)
    assert len(pos.snapshots) <= 240


def test_dedupe_real_na_fila_de_alertas():
    cop = CopilotoManual()
    criar(cop, stop=90)
    fila = FilaDeAlertas(janela_dedupe_s=3600)
    agora = utcnow()
    cop.avaliar_posicoes({INSTR: quote(85, agora)}, agora=agora,
                         enfileirar=fila.enfileirar, forcar=True)
    assert len(fila.alertas) == 1
    assert fila.alertas[0].tipo == "manual_stop_atingido"
    cop.avaliar_posicoes({INSTR: quote(84, agora)}, agora=agora,
                         enfileirar=fila.enfileirar, forcar=True)
    assert len(fila.alertas) == 1          # reincidência não duplica
    assert fila.alertas[0].ocorrencias == 2


def test_preco_invalido_nao_calcula_nada():
    cop = CopilotoManual()
    pos = criar(cop, stop=90)
    agora = utcnow()
    resumo = cop.avaliar_posicoes({INSTR: quote(0, agora)}, agora=agora, forcar=True)
    assert resumo["avaliadas"] == 0 and pos.snapshots == []
    assert any(a["tipo"] == "manual_preco_invalido" for a in resumo["alertas"])


# ---------------------------------------------------------------------------
# diário com revisão por IA
# ---------------------------------------------------------------------------

def test_diario_revisor_ok_e_revisor_que_falha():
    cop = CopilotoManual()
    pos = criar(cop, tese="rompimento de topo")
    agora = utcnow()
    cop.avaliar_posicoes({INSTR: quote(105, agora)}, agora=agora, forcar=True)

    entrada = cop.registrar_revisao(pos.posicao_id, revisor=lambda fatos: "vá bem")
    assert entrada["revisao"] == "vá bem" and entrada["erro"] is None
    assert entrada["fatos"]["instrumento"] == INSTR
    assert entrada["fatos"]["ultimo_preco"] == "105"

    def revisor_quebrado(_):
        raise RuntimeError("IA fora do ar")

    entrada2 = cop.registrar_revisao(pos.posicao_id, revisor=revisor_quebrado)
    assert entrada2["revisao"] is None
    assert entrada2["erro"].startswith("RuntimeError")
    # Nada foi apagado: as duas entradas seguem no diário.
    assert len(cop.revisoes(pos.posicao_id)) == 2


# ---------------------------------------------------------------------------
# persistência e painel
# ---------------------------------------------------------------------------

def test_round_trip_de_persistencia():
    cop = CopilotoManual()
    cop.definir_ativo(True)
    pos = criar(cop, custos_entrada=1, tese="teste")
    cop.registrar_evento(pos.posicao_id, tipo="reducao", quantidade=2,
                         preco=110, custos=0.5, motivo="parcial no alvo")
    agora = utcnow()
    cop.avaliar_posicoes({INSTR: quote(108, agora)}, agora=agora, forcar=True)
    cop.registrar_revisao(pos.posicao_id, revisor=lambda f: "ok")

    doc = cop.to_dict()
    assert doc["schema"] == SCHEMA
    json.dumps(doc)                        # precisa ser serializável puro

    cop2 = CopilotoManual.from_dict(doc)
    assert cop2.ativo is True
    p2 = cop2.obter(pos.posicao_id)
    assert p2.preco_entrada == Decimal("100") and p2.custos_total == Decimal("1.5")
    assert p2.lucro_realizado_liquido == pos.lucro_realizado_liquido
    assert p2.quantidade_aberta == pos.quantidade_aberta
    assert p2.politica.alvos == (Decimal("120"),)
    assert len(p2.eventos) == 1 and len(p2.snapshots) == 1
    assert len(cop2.revisoes(pos.posicao_id)) == 1

    doc2 = cop2.to_dict()
    assert doc2 == doc                        # estável byte a byte


def test_painel_resume_posicao():
    cop = CopilotoManual()
    pos = criar(cop, stop=90, alvos=(120,))
    agora = utcnow()
    cop.avaliar_posicoes({INSTR: quote(105, agora)}, agora=agora, forcar=True)
    painel = pos.para_painel()
    assert painel["instrumento"] == INSTR and painel["ultimo_preco"] == "105"
    assert painel["lucro_liquido"] == "50"     # (105-100)*10 - 0
    assert painel["politica"]["stop"] == "90"
    assert painel["snapshots"] == 1 and painel["eventos"] == 0


def test_listar_e_saude_do_monitor():
    cop = CopilotoManual()
    p1 = criar(cop)
    criar(cop, instrumento="ETH/USDT")
    cop.pausar(p1.posicao_id)
    assert len(cop.listar()) == 2 and len(cop.listar("ABERTA")) == 2
    saude = cop.saude_do_monitor()
    assert saude["posicoes_abertas"] == 2
    assert saude["estados"].get("PAUSED") == 1
    assert saude["intervalo_limites_s"] == [15, 3600]


# ---------------------------------------------------------------------------
# contrato estrutural: este módulo NÃO executa ordem
# ---------------------------------------------------------------------------

def test_modulo_nao_contem_nenhuma_api_de_execucao():
    import ast

    fonte = Path("tradingagents/desk/manual.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    chamadas_proibidas = {"create_order", "cancel_order", "close_position",
                          "submit", "executar_ordem", "taskkill"}
    modulos_proibidos = ("execution", "broker", "corretora")
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Import, ast.ImportFrom)):
            mod = getattr(no, "module", "") or ""
            assert not any(p in mod for p in modulos_proibidos), mod
            for apelido in no.names:
                assert not any(p in apelido.name for p in modulos_proibidos), apelido.name
        if isinstance(no, ast.Call):
            func = no.func
            nome = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert nome not in chamadas_proibidas, f"chamada proibida: {nome}()"
