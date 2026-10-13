"""Escola de Traders (BT-1): estados obrigatórios, IC 95% contra o ponto de
equilíbrio pós-custos, prova final INDEPENDENTE e revogação automática.

Referências do Batalhão adaptadas: parcela preliminar de 200 operações,
prova final de 300 novas, revogação a partir de 50 evidências contrárias —
sempre sujeitas ao intervalo de confiança (poder estatístico), nunca à
contagem crua.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.escola import (
    AMOSTRA_CANDIDATA,
    AMOSTRA_PROVA,
    AMOSTRA_REVOGACAO,
    AMOSTRA_SEM_VANTAGEM,
    EscolaDeTraders,
    EstadoDaEscola,
    PerfilDeMercado,
    comparar_com_controle,
)
from tradingagents.desk.learning import Desfecho

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SPOT = PerfilDeMercado("spot")
CANDIDATA = EstadoDaEscola.CANDIDATA
SEM_VANTAGEM = EstadoDaEscola.SEM_VANTAGEM
COLETANDO = EstadoDaEscola.COLETANDO
FORMADA = EstadoDaEscola.FORMADA
REPROVADA = EstadoDaEscola.REPROVADA
REVOGADA = EstadoDaEscola.FORMATURA_REVOGADA
PROVA_FINAL = EstadoDaEscola.PROVA_FINAL


class ArmazemFake:
    """Armazém em memória com ida-e-volta por JSON, como o real."""

    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _desfechos(estrategia, retornos, *, inicio=BASE, versao="1",
               regime="tendencia", instrumento="BTC/USDT"):
    return [
        Desfecho(estrategia=estrategia, regime=regime,
                 instrumento=instrumento, retorno=Decimal(str(r)),
                 fechado_em=inicio + timedelta(minutes=i), versao=versao)
        for i, r in enumerate(retornos)
    ]


def _alternando(a, b, n):
    return [a if i % 2 == 0 else b for i in range(n)]


def _positivos(n):      # média +0.011, desvio 0.001 — IC inferior > 0
    return _alternando(0.010, 0.012, n)


def _mistos(n):         # média ~0 — IC cruza o zero
    return _alternando(0.010, -0.010, n)


def _ruins(n):          # média -0.011 — IC superior < 0
    return _alternando(-0.010, -0.012, n)


def _avaliar(esc, estrategia, desfechos, agora, perfil=SPOT, versao="1"):
    return esc.avaliar(estrategia=estrategia, versao=versao,
                       desfechos=desfechos, perfil=perfil, agora=agora)


def _formar(esc, estrategia="trader-x"):
    """Percorre o caminho completo até FORMADA e devolve (desfechos, T2)."""
    d1 = _desfechos(estrategia, _positivos(220), inicio=BASE)
    t1 = BASE + timedelta(days=30)
    av = _avaliar(esc, estrategia, d1, t1)
    assert av.estado is CANDIDATA
    prova = _desfechos(estrategia, _positivos(AMOSTRA_PROVA),
                       inicio=t1 + timedelta(hours=1))
    t2 = t1 + timedelta(days=90)
    av = _avaliar(esc, estrategia, d1 + prova, t2)
    assert av.estado is FORMADA
    return d1 + prova, t2


class TestPerfilDeMercado:
    def test_spot_alvo_zero_e_metrica_retorno(self):
        d = _desfechos("e1", [0.01, -0.005, 0.02])
        r = SPOT.metrica(d, agora=BASE + timedelta(days=10))
        assert SPOT.alvo == 0.0
        assert r["unidade"] == "retorno_liquido"
        assert r["amostras"] == 3
        assert r["media"] == pytest.approx(0.008333, abs=1e-5)

    def test_binaria_alvo_com_payout_estressado(self):
        p = PerfilDeMercado("binaria", payout=Decimal("0.85"))
        assert p.alvo == pytest.approx(1 / 1.82, abs=1e-9)
        # metro é taxa de ACERTO, não retorno
        d = _desfechos("e1", [0.85, 0.85, -1.0, 0.85])
        r = p.metrica(d, agora=BASE + timedelta(days=10))
        assert r["unidade"] == "taxa_de_acerto"
        assert r["media"] == pytest.approx(0.75, abs=1e-4)

    def test_n_efetivo_menor_que_amostras_com_peso_decaindo(self):
        # metade recente (5 dias) + metade velha (365 dias): só ~metade da
        # contagem crua vira poder estatístico efetivo
        recentes = _desfechos("e1", [0.01] * 50)
        velhos = _desfechos("e1", [0.01] * 50,
                            inicio=BASE - timedelta(days=360))
        r = SPOT.metrica(recentes + velhos, agora=BASE + timedelta(days=5))
        assert r["amostras"] == 100
        assert 40 < r["n_efetivo"] < 60 < r["amostras"]


class TestEscolaEstados:
    def test_coletando_com_amostra_pequena(self):
        esc = EscolaDeTraders()
        av = _avaliar(esc, "trader-x", _desfechos("trader-x", _positivos(50)),
                      BASE + timedelta(days=5))
        assert av.estado is COLETANDO
        assert esc.elegivel_para_live("trader-x")[0] is False

    def test_199_operacoes_ainda_nao_declaram_candidatura(self):
        esc = EscolaDeTraders()
        av = _avaliar(esc, "trader-x",
                      _desfechos("trader-x", _positivos(199)),
                      BASE + timedelta(days=5))
        assert av.estado is COLETANDO
        assert av.candidatura_em is None
        assert AMOSTRA_CANDIDATA == 200

    def test_220_boas_declaram_candidata_sem_promover(self):
        esc = EscolaDeTraders()
        av = _avaliar(esc, "trader-x",
                      _desfechos("trader-x", _positivos(220)),
                      BASE + timedelta(days=5))
        assert av.estado is CANDIDATA
        assert av.candidatura_em is not None
        assert av.ic_inferior > 0
        # candidata NÃO é elegível: só formada entra no processo do dono
        ok, motivo = esc.elegivel_para_live("trader-x")
        assert ok is False and "candidata" in motivo

    def test_sem_vantagem_apos_300_sem_ic_acima_do_alvo(self):
        esc = EscolaDeTraders()
        av = _avaliar(esc, "trader-x",
                      _desfechos("trader-x", _mistos(320)),
                      BASE + timedelta(days=5))
        assert av.estado is SEM_VANTAGEM
        assert av.ic_inferior <= 0
        assert AMOSTRA_SEM_VANTAGEM == 300

    def test_sem_vantagem_pode_melhorar_com_evidencia_nova(self):
        esc = EscolaDeTraders()
        d1 = _desfechos("trader-x", _mistos(320), inicio=BASE)
        _avaliar(esc, "trader-x", d1, BASE + timedelta(days=5))
        assert esc.estado_de("trader-x") is SEM_VANTAGEM
        d2 = _desfechos("trader-x", _positivos(200),
                        inicio=BASE + timedelta(minutes=320))
        av = _avaliar(esc, "trader-x", d1 + d2, BASE + timedelta(days=20))
        assert av.estado is CANDIDATA


class TestProvaIndependente:
    def test_treino_reaproveitado_nao_vale_como_prova(self):
        """Caso adversarial nº 1: datar desfechos antigos como se fossem
        prova. O corte estrito em candidatura_em tem de barrar."""
        esc = EscolaDeTraders()
        d1 = _desfechos("trader-x", _positivos(220), inicio=BASE)
        t1 = BASE + timedelta(days=30)
        assert _avaliar(esc, "trader-x", d1, t1).estado is CANDIDATA
        # fraude: 300 desfechos "novos" com datas ainda ANTERIORES a T1
        fraude = _desfechos("trader-x", _positivos(300),
                            inicio=BASE + timedelta(hours=5))
        av = _avaliar(esc, "trader-x", d1 + fraude, t1 + timedelta(days=60))
        assert av.estado is CANDIDATA
        assert av.amostras_prova == 0

    def test_prova_final_independente_forma_o_trader(self):
        esc = EscolaDeTraders()
        d1 = _desfechos("trader-x", _positivos(220), inicio=BASE)
        t1 = BASE + timedelta(days=30)
        _avaliar(esc, "trader-x", d1, t1)
        prova = _desfechos("trader-x", _positivos(AMOSTRA_PROVA),
                           inicio=t1 + timedelta(hours=1))
        av = _avaliar(esc, "trader-x", d1 + prova, t1 + timedelta(days=90))
        assert av.estado is FORMADA
        assert av.amostras_prova == AMOSTRA_PROVA
        ok, motivo = esc.elegivel_para_live("trader-x")
        assert ok is True and "autorização do proprietário" in motivo

    def test_prova_parcial_mantem_prova_final(self):
        esc = EscolaDeTraders()
        d1 = _desfechos("trader-x", _positivos(220), inicio=BASE)
        t1 = BASE + timedelta(days=30)
        _avaliar(esc, "trader-x", d1, t1)
        prova = _desfechos("trader-x", _positivos(120),
                           inicio=t1 + timedelta(hours=1))
        av = _avaliar(esc, "trader-x", d1 + prova, t1 + timedelta(days=40))
        assert av.estado is PROVA_FINAL
        assert av.amostras_prova == 120

    def test_prova_ruim_reprova_e_e_estado_terminal(self):
        esc = EscolaDeTraders()
        d1 = _desfechos("trader-x", _positivos(220), inicio=BASE)
        t1 = BASE + timedelta(days=30)
        _avaliar(esc, "trader-x", d1, t1)
        prova = _desfechos("trader-x", _ruins(AMOSTRA_PROVA),
                           inicio=t1 + timedelta(hours=1))
        t2 = t1 + timedelta(days=90)
        av = _avaliar(esc, "trader-x", d1 + prova, t2)
        assert av.estado is REPROVADA
        # evidência boa depois NÃO ressuscita: terminal de verdade
        salva = _desfechos("trader-x", _positivos(300),
                           inicio=t2 + timedelta(hours=1))
        av = _avaliar(esc, "trader-x", d1 + prova + salva,
                      t2 + timedelta(days=60))
        assert av.estado is REPROVADA
        assert esc.elegivel_para_live("trader-x")[0] is False


class TestRevogacao:
    def test_revoga_em_50_novas_evidencias_contrarias(self):
        esc = EscolaDeTraders()
        historico, t2 = _formar(esc)
        # 49 novas ainda não bastam
        sinal = _desfechos("trader-x", _ruins(AMOSTRA_REVOGACAO - 1),
                           inicio=t2 + timedelta(hours=1))
        av = _avaliar(esc, "trader-x", historico + sinal, t2 + timedelta(days=20))
        assert av.estado is FORMADA
        # a 50ª fecha a conta — revogação automática
        sinal = _desfechos("trader-x", _ruins(AMOSTRA_REVOGACAO),
                           inicio=t2 + timedelta(hours=1))
        av = _avaliar(esc, "trader-x", historico + sinal, t2 + timedelta(days=20))
        assert av.estado is REVOGADA
        ok, motivo = esc.elegivel_para_live("trader-x")
        assert ok is False and "revogada" in motivo

    def test_revogada_nao_volta_atras(self):
        esc = EscolaDeTraders()
        historico, t2 = _formar(esc)
        sinal = _desfechos("trader-x", _ruins(AMOSTRA_REVOGACAO),
                           inicio=t2 + timedelta(hours=1))
        t3 = t2 + timedelta(days=20)
        assert _avaliar(esc, "trader-x",
                        historico + sinal, t3).estado is REVOGADA
        brilho = _desfechos("trader-x", _positivos(400),
                            inicio=t3 + timedelta(hours=1))
        av = _avaliar(esc, "trader-x", historico + sinal + brilho,
                      t3 + timedelta(days=90))
        assert av.estado is REVOGADA

    def test_versao_nova_recomeca_a_coleta(self):
        esc = EscolaDeTraders()
        historico, _ = _formar(esc)
        sinal = _desfechos("trader-x", _ruins(AMOSTRA_REVOGACAO),
                           inicio=BASE + timedelta(days=200))
        _avaliar(esc, "trader-x", historico + sinal, BASE + timedelta(days=220))
        assert esc.estado_de("trader-x", "1") is REVOGADA
        d2 = _desfechos("trader-x", _positivos(220), versao="2", inicio=BASE)
        av = _avaliar(esc, "trader-x", d2, BASE + timedelta(days=5), versao="2")
        assert av.estado is CANDIDATA
        assert esc.estado_de("trader-x", "1") is REVOGADA


class TestBinaria:
    def test_estresse_de_payout_muda_a_decisao(self):
        """58,86% de acerto em 350 operações: passa no alvo SEM estresse
        (1/1.85=0.5405) e não passa com o payout estressado (1/1.82=0.5495).
        O IC é o juiz — a contagem crua não decide."""
        retornos = [0.85] * 206 + [-1.0] * 144
        d = _desfechos("trader-bin", retornos, inicio=BASE)
        agora = BASE + timedelta(days=10)
        sem_estresse = PerfilDeMercado("binaria", payout=Decimal("0.85"),
                                       estresse_payout=Decimal("0"))
        estressada = PerfilDeMercado("binaria", payout=Decimal("0.85"))
        av_a = _avaliar(EscolaDeTraders(), "trader-bin", d, agora,
                        perfil=sem_estresse)
        assert av_a.estado is CANDIDATA
        av_b = _avaliar(EscolaDeTraders(), "trader-bin", d, agora,
                        perfil=estressada)
        assert av_b.estado is SEM_VANTAGEM
        assert av_b.media == pytest.approx(206 / 350, abs=2e-3)
        assert av_b.unidade == "taxa_de_acerto"


class TestPersistencia:
    def test_estado_historico_e_relatorio_sobrevivem_ao_reinicio(self):
        arm = ArmazemFake()
        esc1 = EscolaDeTraders(arm)
        d1 = _desfechos("trader-x", _positivos(220), inicio=BASE)
        t1 = BASE + timedelta(days=30)
        _avaliar(esc1, "trader-x", d1, t1)

        esc2 = EscolaDeTraders(arm)
        assert esc2.estado_de("trader-x") is CANDIDATA
        rel = esc2.relatorio()
        item = next(r for r in rel if r["chave"] == "trader-x@1")
        assert item["candidatura_em"] is not None
        assert any(ev["para"] == "candidata" for ev in item["eventos"])
        prog = esc2.progresso()
        assert prog["por_estado"]["candidata"] == 1
        assert prog["referencias"]["parcela_preliminar"] == 200

    def test_comparar_com_controle_aleatorio(self):
        boa = _desfechos("trader-x", _positivos(300), inicio=BASE)
        controle = _desfechos("controle_aleatorio", _alternando(0.002, -0.002, 300),
                              inicio=BASE)
        agora = BASE + timedelta(days=10)
        r = comparar_com_controle(boa, controle, SPOT, agora)
        assert r["vantagem"] == "estrategia"
        igual = _desfechos("c", _positivos(300), inicio=BASE)
        r2 = comparar_com_controle(boa, igual, SPOT, agora)
        assert r2["vantagem"] == "inconclusivo"
