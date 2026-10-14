"""Registro completo do experimento (FQ-1/C, §3 da missão).

Todo experimento precisa registrar fonte de dados, timeframe, custos,
modelo de execução e limitações — e essas declarações fazem parte da
hipótese CONGELADA: mudar qualquer uma muda a assinatura. O resultado, por
sua vez, carrega o hash reprodutível do dado usado e os custos
efetivamente aplicados — sem isso o experimento não é reexecutável nem
auditável.
"""
from __future__ import annotations

import json
from dataclasses import replace

from tradingagents.desk.laboratorio import Hipotese, Laboratorio


class ArmazemFake:
    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _saw(n: int) -> list[float]:
    padrao = [100.0, 99.5, 99.0, 98.5, 98.0, 98.5, 99.0, 99.5]
    return [padrao[i % 8] for i in range(n)]


def _h(**kw) -> Hipotese:
    base = {
        "id": "H-001", "titulo": "reversão no dip", "familia": "reversao",
        "tipo_de_regra": "reversao_z",
        "parametros": (("janela", 10), ("z_entrada", 1.0)),
        "instrumentos": ("BTC/USDT",),
        "janela_treino_barras": 250, "janela_teste_barras": 144,
        "horizonte_barras": 3,
        "fonte_dados": "binance-spot-klines-ccxt",
        "timeframe": "1h",
        "custos": "fee_bps=5;slippage_bps=2",
        "modelo_de_execucao": "close_a_close",
        "limitacoes": "serie histórica de 2 mil barras; sem book",
    }
    base.update(kw)
    return Hipotese(**base)


def test_campos_do_registro_entram_na_assinatura():
    h = _h()
    for alteracao in (
        {"fonte_dados": "outra-fonte"},
        {"timeframe": "4h"},
        {"custos": "fee_bps=10"},
        {"modelo_de_execucao": "next_open"},
        {"limitacoes": "outra limitação"},
    ):
        assert replace(h, **alteracao).assinatura() != h.assinatura(), (
            f"mudar {alteracao} precisa mudar a assinatura")


def test_registro_persiste_os_campos_do_experimento():
    arm = ArmazemFake()
    lab = Laboratorio(arm)
    lab.registrar(_h())
    rel = Laboratorio(arm).relatorio()
    item = rel[0]
    assert item["fonte_dados"] == "binance-spot-klines-ccxt"
    assert item["timeframe"] == "1h"
    assert item["custos"] == "fee_bps=5;slippage_bps=2"
    assert item["modelo_de_execucao"] == "close_a_close"
    assert "book" in item["limitacoes"]


def test_execucao_registra_hash_reprodutivel_do_dado():
    series = {"BTC/USDT": _saw(400)}
    lab = Laboratorio()
    lab.registrar(_h())
    r = lab.executar(_h(), series)
    assert r.hash_das_series, "o resultado precisa do hash do dado usado"
    # mesmo dado ⇒ mesmo hash; dado diferente ⇒ hash diferente
    outro = Laboratorio()
    outro.registrar(_h())
    r2 = outro.executar(_h(), {"BTC/USDT": _saw(400)})
    assert r2.hash_das_series == r.hash_das_series
    alterado = {"BTC/USDT": _saw(400)[:-1] + [99.0]}
    r3 = Laboratorio()
    r3.registrar(_h())
    r3r = r3.executar(_h(), alterado)
    assert r3r.hash_das_series != r.hash_das_series


def test_execucao_registra_custos_aplicados():
    lab = Laboratorio()
    lab.registrar(_h())
    r = lab.executar(_h(), {"BTC/USDT": _saw(400)})
    assert "BTC/USDT:" in r.custos_aplicados
    valor = float(r.custos_aplicados.split(":", 1)[1])
    assert valor > 0


def test_resultado_persistido_carrega_hash_e_custos():
    arm = ArmazemFake()
    lab = Laboratorio(arm)
    lab.registrar(_h())
    lab.executar(_h(), {"BTC/USDT": _saw(400)})
    rel = Laboratorio(arm).relatorio()
    resultado = rel[0]["resultado"]
    assert resultado["hash_das_series"]
    assert "BTC/USDT:" in resultado["custos_aplicados"]
