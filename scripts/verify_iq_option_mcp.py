#!/usr/bin/env python3
"""Prova passiva dos sete MCPs IQ Option com token somente via ambiente."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from service.iq_option_mcp import (  # noqa: E402
    IQ_OPTION_MCP_SERVERS,
    IQ_OPTION_TOKEN_ENV,
    probe_iq_option_mcp,
)
from tradingagents.research.backtest import revisao_do_codigo  # noqa: E402

Probe = Callable[[], dict[str, object]]


class _SafeArgumentParser(argparse.ArgumentParser):
    """Recusa argumentos sem reproduzir seus valores no terminal."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: argumento inválido; use --help\n")


def build_parser() -> argparse.ArgumentParser:
    """Cria a CLI sem qualquer opção que aceite credenciais."""
    parser = _SafeArgumentParser(
        description=__doc__,
        epilog=(
            f"Defina {IQ_OPTION_TOKEN_ENV} no ambiente local. "
            "O token nunca é aceito na linha de comando nem salvo na evidência."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _probe_summary(item: object) -> dict[str, object]:
    """Mantém apenas campos locais necessários para auditar o handshake."""
    row = item if isinstance(item, dict) else {}
    return {
        "server": str(row.get("server", ""))[:120],
        "endpoint": str(row.get("endpoint", ""))[:300],
        "status": str(row.get("status", ""))[:80],
        "ok": row.get("ok") is True,
        "tool_count": (
            row.get("tool_count")
            if isinstance(row.get("tool_count"), int)
            and not isinstance(row.get("tool_count"), bool)
            and row.get("tool_count") >= 0
            else None
        ),
        "callable_tool_count": (
            row.get("callable_tool_count")
            if isinstance(row.get("callable_tool_count"), int)
            and not isinstance(row.get("callable_tool_count"), bool)
            and row.get("callable_tool_count") >= 0
            else None
        ),
        "all_tools_blocked": row.get("all_tools_blocked") is True,
        "contract_validated": row.get("contract_validated") is True,
        "read_only_tool_count": (
            row.get("read_only_tool_count")
            if isinstance(row.get("read_only_tool_count"), int)
            and not isinstance(row.get("read_only_tool_count"), bool)
            and row.get("read_only_tool_count") >= 0
            else None
        ),
        "mutating_tool_count": (
            row.get("mutating_tool_count")
            if isinstance(row.get("mutating_tool_count"), int)
            and not isinstance(row.get("mutating_tool_count"), bool)
            and row.get("mutating_tool_count") >= 0
            else None
        ),
        "unknown_tool_count": (
            row.get("unknown_tool_count")
            if isinstance(row.get("unknown_tool_count"), int)
            and not isinstance(row.get("unknown_tool_count"), bool)
            and row.get("unknown_tool_count") >= 0
            else None
        ),
        "missing_tool_count": (
            row.get("missing_tool_count")
            if isinstance(row.get("missing_tool_count"), int)
            and not isinstance(row.get("missing_tool_count"), bool)
            and row.get("missing_tool_count") >= 0
            else None
        ),
    }


def _all_pass(raw: dict[str, object], probes: Sequence[dict[str, object]]) -> bool:
    """Exige os sete contratos esperados e zero superfície executável."""
    expected = set(IQ_OPTION_MCP_SERVERS.items())
    observed = {(str(row["server"]), str(row["endpoint"])) for row in probes}
    return (
        raw.get("status") == "MCP_METADATA_CONNECTED"
        and raw.get("ok") is True
        and raw.get("credential_present") is True
        and raw.get("metadata_only") is True
        and raw.get("tool_execution_enabled") is False
        and raw.get("orders_enabled") is False
        and raw.get("connected") == len(expected)
        and raw.get("expected") == len(expected)
        and len(probes) == len(expected)
        and observed == expected
        and all(
            row["status"] == "MCP_METADATA_CONNECTED"
            and row["ok"] is True
            and row["contract_validated"] is True
            and isinstance(row["tool_count"], int)
            and isinstance(row["read_only_tool_count"], int)
            and isinstance(row["mutating_tool_count"], int)
            and row["unknown_tool_count"] == 0
            and row["missing_tool_count"] == 0
            and (row["read_only_tool_count"] + row["mutating_tool_count"]
                 == row["tool_count"])
            and row["callable_tool_count"] == 0
            and row["all_tools_blocked"] is True
            for row in probes
        )
    )


def main(argv: list[str] | None = None, *, probe: Probe = probe_iq_option_mcp
         ) -> int:
    """Executa somente a sonda passiva e grava um relatório sanitizado."""
    args = build_parser().parse_args(argv)
    try:
        candidate = probe()
        raw = candidate if isinstance(candidate, dict) else {}
    except Exception as exc:  # defesa da CLI; não serializa resposta/segredo
        raw = {
            "status": "MCP_UNAVAILABLE",
            "ok": False,
            "credential_present": True,
            "metadata_only": True,
            "tool_execution_enabled": False,
            "orders_enabled": False,
            "error_type": type(exc).__name__,
            "probes": [],
        }

    raw_probes = raw.get("probes", [])
    if not isinstance(raw_probes, (list, tuple)):
        raw_probes = []
    probes = [_probe_summary(item) for item in raw_probes]
    credential_present = raw.get("credential_present") is True
    passed = _all_pass(raw, probes)
    if not credential_present:
        result = "CREDENTIAL_REQUIRED"
        exit_code = 2
    elif passed:
        result = "PASS"
        exit_code = 0
    else:
        result = "FAIL"
        exit_code = 1

    evidence = {
        "schema": "dz23-iq-option-mcp-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_revision": revisao_do_codigo(),
        "resultado": result,
        "status": str(raw.get("status", "MCP_UNAVAILABLE"))[:80],
        "connected": sum(row["ok"] is True for row in probes),
        "expected": len(IQ_OPTION_MCP_SERVERS),
        "credential_present": credential_present,
        "metadata_only": raw.get("metadata_only") is True,
        "tool_execution_enabled": False,
        "orders_enabled": False,
        "ordens_enviadas": False,
        "probes": probes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "schema": evidence["schema"],
        "resultado": result,
        "connected": evidence["connected"],
        "expected": evidence["expected"],
        "tool_execution_enabled": False,
        "orders_enabled": False,
        "ordens_enviadas": False,
    }, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
