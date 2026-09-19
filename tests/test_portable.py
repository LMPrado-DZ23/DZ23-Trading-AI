"""Envelope cifrado do export (§2): AES-256-GCM + scrypt + AAD (tenant/user/
purpose/ts). Round-trip funciona; adulteração, frase errada e AAD trocado
falham (fail-closed)."""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.exceptions import InvalidTag

from tradingagents.portable import decrypt_package, encrypt_package, envelope_aad

AAD = {"v": 2, "tenant": "tena", "user": "a@a.com", "purpose": "export", "ts": "2026-01-01"}
DADOS = {"formato": "dz23.mudanca/1", "configuracao": {"GROQ_API_KEY": "gsk-secreto"}}


def test_round_trip():
    env = encrypt_package(DADOS, "frase-forte", aad=AAD)
    assert env["formato"].endswith("/2") and "salt" in env and "nonce" in env
    assert "gsk-secreto" not in json.dumps(env)          # segredo não vaza em claro
    assert decrypt_package(env, "frase-forte")["configuracao"]["GROQ_API_KEY"] == "gsk-secreto"


def test_frase_errada_falha():
    env = encrypt_package(DADOS, "certa", aad=AAD)
    with pytest.raises(InvalidTag):
        decrypt_package(env, "ERRADA")


def test_adulteracao_do_ciphertext_falha():
    env = encrypt_package(DADOS, "frase", aad=AAD)
    b = bytearray(base64.b64decode(env["ciphertext"]))
    b[0] ^= 0x01
    env["ciphertext"] = base64.b64encode(bytes(b)).decode()
    with pytest.raises(InvalidTag):
        decrypt_package(env, "frase")


def test_aad_trocado_falha():
    # trocar o tenant no AAD invalida a autenticação (GCM verifica o AAD)
    env = encrypt_package(DADOS, "frase", aad=AAD)
    env["aad"] = {**AAD, "tenant": "tenb"}
    with pytest.raises(InvalidTag):
        decrypt_package(env, "frase")


def test_envelope_aad_expoe_o_escopo():
    env = encrypt_package(DADOS, "frase", aad=AAD)
    assert envelope_aad(env)["tenant"] == "tena"


def test_frase_vazia_recusada():
    with pytest.raises(ValueError, match="vazia"):
        encrypt_package(DADOS, "", aad=AAD)
