"""CLI sanitizada para provar os sete MCPs oficiais da IQ Option."""
from __future__ import annotations

import json

import pytest

from scripts.verify_iq_option_mcp import build_parser, main
from service.iq_option_mcp import IQ_OPTION_MCP_SERVERS


def _resultado_ok() -> dict[str, object]:
    return {
        "id": "iq_option",
        "status": "MCP_METADATA_CONNECTED",
        "ok": True,
        "connected": 7,
        "expected": 7,
        "credential_present": True,
        "metadata_only": True,
        "tool_execution_enabled": False,
        "orders_enabled": False,
        "probes": [
            {
                "server": server,
                "endpoint": endpoint,
                "status": "MCP_METADATA_CONNECTED",
                "ok": True,
                "tool_count": 3,
                "callable_tool_count": 0,
                "all_tools_blocked": True,
                "tools": [{"name": "place_order", "description": "SEGREDO"}],
                "remote_payload": "SEGREDO",
            }
            for server, endpoint in IQ_OPTION_MCP_SERVERS.items()
        ],
    }


def test_cli_nao_aceita_token_como_argumento():
    ajuda = build_parser().format_help()
    assert "--token" not in ajuda
    assert "--bearer" not in ajuda
    assert "IQ_OPTION_MCP_BEARER_TOKEN" in ajuda


def test_argumento_invalido_nao_ecoa_valor_sensivel(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--token", "SEGREDO-NAO-ECOAR"])

    saida = capsys.readouterr()
    assert "SEGREDO-NAO-ECOAR" not in saida.err
    assert "SEGREDO-NAO-ECOAR" not in saida.out


def test_sucesso_salva_somente_evidencia_sanitizada(tmp_path, capsys):
    destino = tmp_path / "iq-option.json"

    codigo = main(["--output", str(destino)], probe=_resultado_ok)

    assert codigo == 0
    serializado = destino.read_text(encoding="utf-8")
    evidencia = json.loads(serializado)
    assert evidencia["schema"] == "dz23-iq-option-mcp-v1"
    assert evidencia["resultado"] == "PASS"
    assert evidencia["connected"] == 7
    assert evidencia["expected"] == 7
    assert evidencia["metadata_only"] is True
    assert evidencia["tool_execution_enabled"] is False
    assert evidencia["orders_enabled"] is False
    assert all(item["all_tools_blocked"] is True
               for item in evidencia["probes"])
    assert "tools" not in evidencia["probes"][0]
    assert "remote_payload" not in evidencia["probes"][0]
    assert "SEGREDO" not in serializado
    assert "PASS" in capsys.readouterr().out


def test_contradicao_em_um_servidor_falha_fechada(tmp_path):
    resultado = _resultado_ok()
    resultado["probes"][3]["all_tools_blocked"] = False
    resultado["probes"][3]["callable_tool_count"] = 1

    codigo = main(
        ["--output", str(tmp_path / "iq-option.json")],
        probe=lambda: resultado,
    )

    assert codigo == 1
    evidencia = json.loads(
        (tmp_path / "iq-option.json").read_text(encoding="utf-8"))
    assert evidencia["resultado"] == "FAIL"
    assert evidencia["ordens_enviadas"] is False


def test_credencial_ausente_retorna_codigo_distinto_sem_rede(tmp_path):
    def sem_credencial():
        return {
            "status": "CREDENTIAL_REQUIRED",
            "ok": False,
            "credential_present": False,
            "metadata_only": True,
            "tool_execution_enabled": False,
            "orders_enabled": False,
            "probes": [],
        }

    codigo = main(
        ["--output", str(tmp_path / "iq-option.json")],
        probe=sem_credencial,
    )

    assert codigo == 2
    evidencia = json.loads(
        (tmp_path / "iq-option.json").read_text(encoding="utf-8"))
    assert evidencia["resultado"] == "CREDENTIAL_REQUIRED"
    assert evidencia["credential_present"] is False


def test_excecao_fatal_nao_serializa_mensagem_remota(tmp_path, capsys):
    destino = tmp_path / "iq-option.json"

    def falhar():
        raise RuntimeError("Authorization: Bearer SEGREDO-NAO-VAZAR")

    codigo = main(["--output", str(destino)], probe=falhar)

    assert codigo == 1
    combinado = destino.read_text(encoding="utf-8") + capsys.readouterr().out
    assert "SEGREDO-NAO-VAZAR" not in combinado
    assert "Authorization" not in combinado
    assert json.loads(destino.read_text(encoding="utf-8"))["resultado"] == "FAIL"
