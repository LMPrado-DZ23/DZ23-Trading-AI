"""Prova fail-closed do backup/restore lógico PostgreSQL."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import tradingagents.desk.postgres_recovery as recovery

SOURCE_ID = "a" * 64
TARGET_ID = "b" * 64
SOURCE_DSN = "source-dsn-secret"
TARGET_DSN = "target-dsn-secret"
MANIFEST = {
    "tables": ["dz23_desk_state", "jobs"],
    "details": {
        "dz23_desk_state": {
            "schema_sha256": "1" * 64,
            "rows_sha256": "2" * 64,
            "row_count": 2,
        },
        "jobs": {
            "schema_sha256": "3" * 64,
            "rows_sha256": "4" * 64,
            "row_count": 1,
        },
    },
}


def _successful_dependencies(monkeypatch):
    monkeypatch.setattr(
        recovery,
        "_database_identity",
        lambda dsn: SOURCE_ID if dsn == SOURCE_DSN else TARGET_ID,
    )
    monkeypatch.setattr(recovery, "_existing_public_tables", lambda _dsn: [])
    monkeypatch.setattr(recovery, "_database_manifest", lambda _dsn: MANIFEST)

    calls = []

    def fake_tool(executable, arguments, **kwargs):
        calls.append((executable, arguments, kwargs))
        if executable == "pg_dump":
            output = arguments[arguments.index("--file") + 1]
            recovery.Path(output).write_bytes(b"custom-format-dump")

    monkeypatch.setattr(recovery, "_run_pg_tool", fake_tool)
    return calls


def test_rehearsal_uses_empty_distinct_target_and_writes_redacted_proof(
    tmp_path, monkeypatch
):
    calls = _successful_dependencies(monkeypatch)
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)

    proof = recovery.rehearse_postgres_restore(
        tmp_path, SOURCE_DSN, TARGET_DSN, now=now
    )

    assert [call[0] for call in calls] == ["pg_dump", "pg_restore"]
    assert proof["resultado"] == "restaurado_e_verificado"
    assert proof["envio_de_ordens"] is False
    assert proof["source_fingerprint"] == SOURCE_ID
    assert proof["target_fingerprint"] == TARGET_ID
    assert set(proof["tabelas"]) == recovery.REQUIRED_TABLES
    serialized = json.dumps(proof)
    assert SOURCE_DSN not in serialized
    assert TARGET_DSN not in serialized
    assert (tmp_path / recovery.PROOF_NAME).is_file()
    assert (tmp_path / "backups" / proof["backup_arquivo"]).is_file()


def test_same_database_or_nonempty_target_is_refused_before_dump(tmp_path, monkeypatch):
    called = False

    monkeypatch.setattr(recovery, "_database_identity", lambda _dsn: SOURCE_ID)

    def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(recovery, "_run_pg_tool", should_not_run)
    with pytest.raises(recovery.PostgresRecoveryError, match="mesmo banco"):
        recovery.rehearse_postgres_restore(tmp_path, SOURCE_DSN, TARGET_DSN)
    assert called is False

    monkeypatch.setattr(
        recovery,
        "_database_identity",
        lambda dsn: SOURCE_ID if dsn == SOURCE_DSN else TARGET_ID,
    )
    monkeypatch.setattr(recovery, "_existing_public_tables", lambda _dsn: ["foreign"])
    with pytest.raises(recovery.PostgresRecoveryError, match="não está vazio"):
        recovery.rehearse_postgres_restore(tmp_path, SOURCE_DSN, TARGET_DSN)
    assert called is False


def test_source_change_during_dump_refuses_proof_and_removes_temporary_dump(
    tmp_path, monkeypatch
):
    _successful_dependencies(monkeypatch)
    changed = {**MANIFEST, "details": {**MANIFEST["details"]}}
    changed["details"]["jobs"] = {**MANIFEST["details"]["jobs"], "row_count": 2}
    manifests = iter((MANIFEST, changed))
    monkeypatch.setattr(recovery, "_database_manifest", lambda _dsn: next(manifests))

    with pytest.raises(recovery.PostgresRecoveryError, match="origem mudou"):
        recovery.rehearse_postgres_restore(tmp_path, SOURCE_DSN, TARGET_DSN)

    assert not (tmp_path / recovery.PROOF_NAME).exists()
    assert list((tmp_path / "backups").iterdir()) == []


def test_validation_binds_database_age_and_retained_dump(tmp_path, monkeypatch):
    _successful_dependencies(monkeypatch)
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    proof = recovery.rehearse_postgres_restore(
        tmp_path, SOURCE_DSN, TARGET_DSN, now=now
    )
    monkeypatch.setattr(recovery, "_database_identity", lambda _dsn: SOURCE_ID)

    assert recovery.validate_postgres_restore_proof(
        tmp_path, "current", now=now
    )["valida"] is True

    wrong = recovery.validate_postgres_restore_proof(
        tmp_path, "current", now=now + timedelta(days=31)
    )
    assert wrong["valida"] is False
    assert "expirado" in wrong["motivo"]

    archive = tmp_path / "backups" / proof["backup_arquivo"]
    archive.write_bytes(archive.read_bytes() + b"tampered")
    assert "hash" in recovery.validate_postgres_restore_proof(
        tmp_path, "current", now=now
    )["motivo"]


def test_pg_tool_keeps_password_out_of_process_arguments(monkeypatch):
    captured = {}
    monkeypatch.setenv("UNRELATED_API_KEY", "must-not-reach-child")
    monkeypatch.setattr(recovery.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(recovery.subprocess, "run", fake_run)
    secret = "senha-super-secreta"
    dsn = f"postgresql://dz23:{secret}@db.example:5432/dz23"
    recovery._run_pg_tool(
        "pg_dump", ["--format=custom"], dsn=dsn, timeout_seconds=30
    )

    assert secret not in " ".join(captured["command"])
    assert captured["env"]["PGPASSWORD"] == secret
    assert captured["env"]["PGHOST"] == "db.example"
    assert "UNRELATED_API_KEY" not in captured["env"]
