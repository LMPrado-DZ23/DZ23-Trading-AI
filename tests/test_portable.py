"""Envelope cifrado do export (§2/§4): AES-256-GCM + scrypt + AAD. Round-trip
funciona; frase fraca, KDF/params inválidos, salt/nonce errados, base64 não
estrito, adulteração e AAD trocado FALHAM (fail-closed, anti-DoS)."""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.exceptions import InvalidTag

from tradingagents.portable import decrypt_package, encrypt_package, envelope_aad

AAD = {"v": 2, "tenant": "tena", "user": "a@a.com", "purpose": "export", "ts": "2026-01-01"}
DADOS = {"formato": "dz23.mudanca/1", "configuracao": {"GROQ_API_KEY": "gsk-secreto"}}
FRASE = "frase-forte-123"          # >= 12


def test_round_trip():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    assert env["formato"].endswith("/2") and "salt" in env and "nonce" in env
    assert "gsk-secreto" not in json.dumps(env)          # segredo não vaza em claro
    assert decrypt_package(env, FRASE)["configuracao"]["GROQ_API_KEY"] == "gsk-secreto"


def test_frase_errada_falha():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    with pytest.raises(InvalidTag):
        decrypt_package(env, "frase-forte-OUTRA")


def test_adulteracao_do_ciphertext_falha():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    b = bytearray(base64.b64decode(env["ciphertext"]))
    b[0] ^= 0x01
    env["ciphertext"] = base64.b64encode(bytes(b)).decode()
    with pytest.raises(InvalidTag):
        decrypt_package(env, FRASE)


def test_aad_trocado_falha():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    env["aad"] = {**AAD, "tenant": "tenb"}
    with pytest.raises(InvalidTag):
        decrypt_package(env, FRASE)


def test_envelope_aad_expoe_o_escopo():
    assert envelope_aad(encrypt_package(DADOS, FRASE, aad=AAD))["tenant"] == "tena"


def test_frase_fraca_recusada_no_encrypt():
    with pytest.raises(ValueError, match="fraca"):
        encrypt_package(DADOS, "curta", aad=AAD)


def test_kdf_desconhecido_recusado():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    env["kdf"] = "pbkdf2"
    with pytest.raises(ValueError, match="kdf"):
        decrypt_package(env, FRASE)


def test_params_kdf_fora_do_limite_recusado():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    env["n"] = 3                                          # não é potência de 2
    with pytest.raises(ValueError, match="KDF"):
        decrypt_package(env, FRASE)


def test_salt_tamanho_errado_recusado():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    env["salt"] = base64.b64encode(b"curto").decode()
    with pytest.raises(ValueError, match="salt"):
        decrypt_package(env, FRASE)


def test_base64_nao_estrito_recusado():
    env = encrypt_package(DADOS, FRASE, aad=AAD)
    env["ciphertext"] = "!!!nao-base64!!!"
    with pytest.raises(ValueError):
        decrypt_package(env, FRASE)
