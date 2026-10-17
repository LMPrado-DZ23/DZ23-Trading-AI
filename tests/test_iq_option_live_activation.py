"""Ativacao final da IQ Option monta runtime proprio sem enviar ordem."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

H = {"Authorization": "Bearer s3nh4"}


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as module
    importlib.reload(module)
    module._FAILS.clear()
    module._DESKS.clear()
    module._IQ_BINARY_RUNTIMES.clear()
    from fastapi.testclient import TestClient
    return module, TestClient(module.app)


class Store:
    def __init__(self):
        self.documents = {}

    def salvar(self, tenant, key, value):
        self.documents[(tenant, key)] = value


def _configure_ready_activation(module, monkeypatch):
    cfg = {
        "TRADINGAGENTS_DESK_BROKER": "mcp",
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "TRADINGAGENTS_DESK_CAPITAL": "1000",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-token",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "live-token",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "LIVE_MAX_ORDER_VALUE": "25",
        "LIVE_MAX_DAILY_LOSS": "10",
    }
    pilot = SimpleNamespace(
        authorization_id="AUT-IQ-1",
        conta="principal",
        agent_id="trader-trend",
    )
    store = Store()
    monkeypatch.setattr(module, "MESA_LIVE_LIBERADA", True)
    monkeypatch.setattr(module, "load_cfg", lambda _tenant: dict(cfg))
    monkeypatch.setattr(module, "_live_status_payload", lambda _request: {
        "estado_de_liberacao": {
            "pre_requisitos_sem_selo_concluidos": True,
        },
        "certificacoes_estrategia_conta": [{
            "agent_id": "trader-trend",
            "conta": "principal",
            "risco_compativel": True,
        }],
    })
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store,
        "tenant_id": "legacy",
    })
    monkeypatch.setattr(
        module, "_supervisor_of",
        lambda _request: SimpleNamespace(de=lambda _agent: pilot),
    )
    monkeypatch.setattr(
        module, "_prova_leitura_real",
        lambda _request, _cfg: ({}, True),
    )
    monkeypatch.setattr(
        module, "_prova_execucao_live",
        lambda _request, _cfg: ({
            "conta": "principal",
            "verificada_em": "2026-10-17T12:00:00+00:00",
        }, True),
    )
    monkeypatch.setattr(
        module, "_prova_real_serve_piloto", lambda *_args: True)
    saved = {}
    monkeypatch.setattr(
        module, "save_cfg",
        lambda value, tenant: saved.__setitem__(tenant, dict(value)),
    )
    return cfg, pilot, store, saved


def test_ativacao_iq_monta_runtime_sem_usar_desk_ccxt_ou_enviar_ordem(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    cfg, pilot, store, saved = _configure_ready_activation(
        module, monkeypatch)
    runtime = SimpleNamespace(name="binary-runtime")
    calls = []

    async def build(**kwargs):
        calls.append(kwargs)
        return runtime

    monkeypatch.setattr(module, "_build_iq_binary_runtime", build)
    monkeypatch.setattr(
        module, "_desk_boot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("IQ Option nao pode usar desk CCXT")),
    )

    response = client.post(
        "/v1/live/ativar/trader-trend",
        headers=H,
        json={
            "frase": module.LIVE_CONFIRM_PHRASE,
            "conta": "principal",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["runtime"] == "iq_option_binary"
    assert response.json()["envio_real_habilitado"] is False
    assert len(calls) == 1
    assert calls[0]["tenant_id"] == "legacy"
    assert calls[0]["authorization"] is pilot
    assert calls[0]["confirmation_phrase"] == module.LIVE_CONFIRM_PHRASE
    assert calls[0]["cfg"] == cfg
    assert module._IQ_BINARY_RUNTIMES["legacy"] is runtime
    assert module._DESKS == {}
    assert saved["legacy"]["TRADINGAGENTS_BROKER_MODE"] == "live"
    activation = store.documents[("legacy", module._CHAVE_ATIVACAO_LIVE)]
    assert activation["exchange"] == "iq_option"
    assert activation["agent_id"] == "trader-trend"
    assert "frase" not in activation


def test_desativacao_remove_runtime_binario_iq(tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    _, _, store, saved = _configure_ready_activation(module, monkeypatch)
    module._IQ_BINARY_RUNTIMES["legacy"] = object()

    response = client.post("/v1/live/desativar", headers=H)

    assert response.status_code == 200
    assert "legacy" not in module._IQ_BINARY_RUNTIMES
    assert saved["legacy"]["TRADINGAGENTS_BROKER_MODE"] == "testnet"
    revocation = store.documents[("legacy", module._CHAVE_ATIVACAO_LIVE)]
    assert revocation["motivo"] == "desativada explicitamente pelo proprietário"


def test_parada_emergencia_remove_runtime_binario_iq(tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    module._IQ_BINARY_RUNTIMES["legacy"] = object()

    response = client.post("/v1/live/parada-emergencia", headers=H)

    assert response.status_code == 200
    assert response.json()["parado"] is True
    assert "legacy" not in module._IQ_BINARY_RUNTIMES


def test_reset_de_configuracao_remove_runtime_binario_iq(tmp_path, monkeypatch):
    module, _client = _app(tmp_path, monkeypatch)
    module._IQ_BINARY_RUNTIMES["legacy"] = object()

    module._desk_reset("legacy")

    assert "legacy" not in module._IQ_BINARY_RUNTIMES


def test_falha_ao_montar_iq_nao_persiste_ativacao_nem_muda_modo(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    _, _, store, saved = _configure_ready_activation(module, monkeypatch)

    async def fail_closed(**_kwargs):
        raise module.IqOptionBinaryRuntimeError("conta divergente")

    monkeypatch.setattr(module, "_build_iq_binary_runtime", fail_closed)

    response = client.post(
        "/v1/live/ativar/trader-trend",
        headers=H,
        json={
            "frase": module.LIVE_CONFIRM_PHRASE,
            "conta": "principal",
        },
    )

    assert response.status_code == 409
    assert module._IQ_BINARY_RUNTIMES == {}
    assert saved == {}
    assert store.documents == {}


def test_resumo_da_sala_reconhece_runtime_iq_ativo(tmp_path, monkeypatch):
    module, _client = _app(tmp_path, monkeypatch)
    module._IQ_BINARY_RUNTIMES["legacy"] = object()
    monkeypatch.setattr(module, "MESA_LIVE_LIBERADA", True)
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "central": object(),
    })
    monkeypatch.setattr(module, "_supervisor_of", lambda _request:
                        SimpleNamespace(
                            vigiar=lambda **_kwargs: None,
                            encaminhamentos=lambda: [],
                            relatorio=lambda: [],
                        ))
    request = SimpleNamespace(state=SimpleNamespace())

    result = module._resumo_sala_real(request)

    assert result["modo_do_piloto"] == "live (conta real)"
    assert "mesa LIVE construída" in result["aviso"]
    assert result["envio_real_habilitado"] is False
