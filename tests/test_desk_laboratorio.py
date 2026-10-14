"""Laboratório de hipóteses pré-registradas (BT-2): pré-registro imutável com
assinatura, execução única (anti p-hacking), sem look-ahead, controle
aleatório determinístico, Bonferroni por família — e NUNCA promove ninguém.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tradingagents.desk.laboratorio import (
    ALPHA,
    EXECUTORES,
    Hipotese,
    Laboratorio,
)


class ArmazemFake:
    def __init__(self):
        self.dados: dict = {}

    def carregar(self, tenant_id, chave):
        valor = self.dados.get((tenant_id, chave))
        return json.loads(json.dumps(valor)) if valor is not None else None

    def salvar(self, tenant_id, chave, payload):
        self.dados[(tenant_id, chave)] = json.loads(json.dumps(payload))


def _saw(n: int) -> list[float]:
    """Serie periódica [100..98..100] que volta ao centro — reversão clara."""
    padrao = [100.0, 99.5, 99.0, 98.5, 98.0, 98.5, 99.0, 99.5]
    return [padrao[i % 8] for i in range(n)]


def _declinio(n: int) -> list[float]:
    return [100.0 - 0.05 * i for i in range(n)]


def _h(**kw) -> Hipotese:
    base = {
        "id": "H-001", "titulo": "reversão no dip", "familia": "reversao",
        "tipo_de_regra": "reversao_z",
        "parametros": (("janela", 10), ("z_entrada", 1.0)),
        "instrumentos": ("BTC/USDT",),
        "janela_treino_barras": 250, "janela_teste_barras": 144,
        "horizonte_barras": 3,
    }
    base.update(kw)
    return Hipotese(**base)


class TestPreRegistro:
    def test_executar_sem_preregistro_recusa(self):
        lab = Laboratorio()
        with pytest.raises(ValueError, match="não pré-registrada"):
            lab.executar(_h(), {"BTC/USDT": _saw(400)})

    def test_assinatura_estavel_e_sensivel_a_mudanca(self):
        h = _h()
        assert h.assinatura() == _h().assinatura()
        assert replace(h, registrada_em="2026-01-01T00:00:00+00:00").assinatura() == h.assinatura()
        alterada = replace(h, parametros=(("janela", 10), ("z_entrada", 2.5)))
        assert alterada.assinatura() != h.assinatura()

    def test_registro_idempotente_por_assinatura(self):
        lab = Laboratorio()
        a1 = lab.registrar(_h())
        a2 = lab.registrar(_h())
        assert a1 == a2
        assert lab.familia_n("reversao") == 1

    def test_hipotese_alterada_apos_registro_e_recusada(self):
        """Caso adversarial: registrar uma coisa e executar outra (parâmetro
        trocado depois de espiar os dados) tem de ser impossível."""
        lab = Laboratorio()
        lab.registrar(_h())
        trapaceira = replace(_h(), parametros=(("janela", 10), ("z_entrada", 0.5)))
        with pytest.raises(ValueError, match="não pré-registrada"):
            lab.executar(trapaceira, {"BTC/USDT": _saw(400)})

    def test_reexecucao_bloqueada_sem_forcar(self):
        lab = Laboratorio()
        lab.registrar(_h())
        lab.executar(_h(), {"BTC/USDT": _saw(400)})
        with pytest.raises(RuntimeError, match="já executada"):
            lab.executar(_h(), {"BTC/USDT": _saw(400)})
        lab.executar(_h(), {"BTC/USDT": _saw(400)}, forcar=True)
        rel = lab.relatorio()
        assert rel[0]["resultado"]["forcada"] is True


class TestExecucao:
    def test_sem_look_ahead_no_executor(self):
        """O sinal só pode depender do passado: mexer no futuro não muda nada."""
        serie = [float(i) for i in range(50)]
        out1 = EXECUTORES["rompimento"](serie, 20, {"janela": 5})
        futuro_alterado = serie[:21] + [99999.0] * 29
        out2 = EXECUTORES["rompimento"](futuro_alterado, 20, {"janela": 5})
        assert out1 == out2
        truncada = serie[:21]
        assert EXECUTORES["rompimento"](truncada, 20, {"janela": 5}) == out1

    def test_controle_deterministico_e_mesmo_numero_de_entradas(self):
        lab = Laboratorio()
        lab.registrar(_h())
        r1 = lab.executar(_h(), {"BTC/USDT": _saw(400)})
        r2 = lab.executar(_h(), {"BTC/USDT": _saw(400)}, forcar=True)
        assert r1.operacoes_controle == r1.operacoes_estrategia
        assert r1.media_controle == r2.media_controle
        assert r1.p_valor == r2.p_valor

    def test_hipotese_com_vantagem_aprova(self):
        lab = Laboratorio()
        lab.registrar(_h())
        r = lab.executar(_h(), {"BTC/USDT": _saw(400)})
        assert r.operacoes_estrategia >= 10
        assert r.media_estrategia > 0
        assert r.p_valor < r.alpha_ajustado
        assert r.aprovada is True
        assert "escola" in r.motivo
        assert "NÃO promove" in r.aviso

    def test_estragia_sem_vantagem_reprova(self):
        """Sempre-comprado em série que só cai: custos e mercado derrubam."""
        ruim = _h(id="H-002", titulo="sempre dentro", familia="momentum-neg",
                  tipo_de_regra="momentum",
                  parametros=(("janela", 1), ("limiar", -1.0)))
        lab = Laboratorio()
        lab.registrar(ruim)
        r = lab.executar(ruim, {"BTC/USDT": _declinio(400)})
        assert r.aprovada is False
        assert r.media_estrategia < 0
        assert "não paga os custos" in r.motivo

    def test_serie_curta_da_sem_operacoes(self):
        lab = Laboratorio()
        lab.registrar(_h())
        r = lab.executar(_h(), {"BTC/USDT": _saw(100)})
        assert r.operacoes_estrategia == 0
        assert r.aprovada is False
        assert "sem operações" in r.motivo


class TestBonferroni:
    def test_alpha_cai_com_o_tamanho_da_familia(self):
        lab = Laboratorio()
        for i in range(3):
            lab.registrar(_h(id=f"H-{i:03d}", titulo=f"variante {i}"))
        r = lab.executar(_h(id="H-000", titulo="variante 0"),
                         {"BTC/USDT": _saw(400)})
        assert r.familia_n == 3
        assert r.alpha_ajustado == pytest.approx(ALPHA / 3)

    def test_familia_pequena_mantem_alpha(self):
        lab = Laboratorio()
        lab.registrar(_h(familia="sozinha"))
        r = lab.executar(_h(familia="sozinha"), {"BTC/USDT": _saw(400)})
        assert r.familia_n == 1
        assert r.alpha_ajustado == pytest.approx(ALPHA)


class TestPersistencia:
    def test_historico_sobrevive_ao_reinicio_e_bloqueio_persiste(self):
        arm = ArmazemFake()
        lab1 = Laboratorio(arm)
        lab1.registrar(_h())
        r1 = lab1.executar(_h(), {"BTC/USDT": _saw(400)})

        lab2 = Laboratorio(arm)
        rel = lab2.relatorio()
        assert len(rel) == 1
        assert rel[0]["executada"] is True
        assert rel[0]["resultado"]["p_valor"] == r1.p_valor
        assert rel[0]["registrada_em"] is not None
        with pytest.raises(RuntimeError, match="já executada"):
            lab2.executar(_h(), {"BTC/USDT": _saw(400)})
