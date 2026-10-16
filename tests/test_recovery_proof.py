"""Ensaio de restore como gate operacional da preparação REAL."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.desk.backup import create_backup
from tradingagents.desk.recovery import (
    PROOF_NAME,
    RecoveryProofError,
    rehearse_local_restore,
    validate_local_restore_proof,
)


def _data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text('{"mode":"paper"}', encoding="utf-8")
    (data / "execution-events.jsonl").write_text(
        '{"event":"paper"}\n', encoding="utf-8")
    (data / "desks" / "legacy").mkdir(parents=True)
    (data / "desks" / "legacy" / "escola.json").write_text(
        '{"formados":[]}', encoding="utf-8")
    with sqlite3.connect(data / "jobs.db") as connection:
        connection.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, mode TEXT)")
        connection.execute("INSERT INTO jobs(mode) VALUES ('paper')")
    archive = data / "backups" / "dz23-test.tar.gz"
    create_backup(data, archive)
    return data, archive


def test_rehearsal_restores_isolated_and_registers_recent_proof(tmp_path):
    data, archive = _data(tmp_path)
    original = (data / "config.json").read_bytes()

    proof = rehearse_local_restore(data, archive)
    status = validate_local_restore_proof(data)

    assert proof["resultado"] == "restaurado_e_verificado"
    assert proof["envio_de_ordens"] is False
    assert proof["validacoes_semanticas"] == {
        "sqlite": 1, "json": 2, "jsonl": 1}
    assert status["valida"] is True
    assert (data / PROOF_NAME).is_file()
    assert (data / "config.json").read_bytes() == original


def test_proof_fails_closed_if_archive_changes_or_disappears(tmp_path):
    data, archive = _data(tmp_path)
    rehearse_local_restore(data, archive)

    archive.write_bytes(archive.read_bytes() + b"tampered")
    assert validate_local_restore_proof(data)["valida"] is False
    archive.unlink()
    status = validate_local_restore_proof(data)
    assert status["valida"] is False
    assert "não está mais disponível" in status["motivo"]


def test_expired_future_and_naive_proofs_are_refused(tmp_path):
    data, archive = _data(tmp_path)
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    rehearse_local_restore(data, archive, now=now - timedelta(days=31))
    assert "expirado" in validate_local_restore_proof(
        data, now=now)["motivo"]

    proof_path = data / PROOF_NAME
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["verificado_em"] = (now + timedelta(minutes=6)).isoformat()
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    assert "futuro" in validate_local_restore_proof(data, now=now)["motivo"]

    proof["verificado_em"] = "2026-10-16T12:00:00"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    assert "fuso" in validate_local_restore_proof(data, now=now)["motivo"]


def test_invalid_json_inside_valid_hash_backup_fails_semantic_check(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text("{broken", encoding="utf-8")
    archive = data / "backups" / "broken.tar.gz"
    create_backup(data, archive)

    with pytest.raises(RecoveryProofError, match="config.json"):
        rehearse_local_restore(data, archive)
    assert not (data / PROOF_NAME).exists()


def test_archive_outside_managed_backup_directory_is_refused(tmp_path):
    data, archive = _data(tmp_path)
    outside = tmp_path / "outside.tar.gz"
    outside.write_bytes(archive.read_bytes())

    with pytest.raises(RecoveryProofError, match="DATA_DIR/backups"):
        rehearse_local_restore(data, outside)
