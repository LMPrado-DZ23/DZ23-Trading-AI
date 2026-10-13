"""Credenciais do dono: Argon2id em disco + código de recuperação.

A senha do dono vivia em texto puro (owner_password.txt) e era comparada
direto contra a variável de ambiente. Estes testes fixam o contrato do módulo
que substitui isso: o disco guarda apenas hashes Argon2id, o código de
recuperação é exibido uma única vez e rotaciona a cada uso, e nenhum texto
puro sobra no arquivo.
"""
from __future__ import annotations

import json

import pytest

from service.api import owner_credentials as oc

SENHA = "senha-forte-do-dono-2026"


def _arquivo(tmp_path):
    return tmp_path / oc.NOME_ARQUIVO


def test_criar_grava_so_hashes_argon2id_nunca_a_senha(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    codigo = creds.criar(SENHA)
    texto = _arquivo(tmp_path).read_text(encoding="utf-8")
    assert SENHA not in texto
    assert oc.normalizar_codigo(codigo) not in texto
    doc = json.loads(texto)
    assert doc["versao"] == 1
    assert doc["senha_hash"].startswith("$argon2id$")
    assert doc["codigo_hash"].startswith("$argon2id$")
    assert doc["criado_em"] and doc["atualizado_em"]


def test_verificar_senha_certa_e_errada(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    creds.criar(SENHA)
    assert creds.verificar_senha(SENHA) is True
    assert creds.verificar_senha(SENHA + "x") is False
    assert creds.verificar_senha("") is False


def test_sem_arquivo_nada_autentica_e_existe_e_falso(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    assert creds.existe() is False
    assert creds.verificar_senha(SENHA) is False


def test_senha_curta_recusada_sem_gravar(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    assert "8" in oc.erro_de_senha("seteabc")
    assert oc.erro_de_senha("senha com 8") is None
    with pytest.raises(ValueError):
        creds.criar("curta")
    assert not _arquivo(tmp_path).exists()


def test_codigo_de_recuperacao_formato_ditavel():
    codigo = oc.gerar_codigo_recuperacao()
    partes = codigo.split("-")
    assert len(partes) == 6 and all(len(p) == 5 for p in partes)
    # Sem I, L, O, 0, 1: letra e número não se confundem ao anotar.
    assert not (set(codigo) & set("ILO01"))
    assert oc.normalizar_codigo(codigo.lower() + "  ") == codigo.replace("-", "")


def test_recuperar_rotaciona_senha_e_codigo(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    codigo = creds.criar(SENHA)
    novo = creds.usar_codigo_de_recuperacao(
        codigo.replace("-", " ").lower(), "nova-senha-2026")
    assert novo and novo != codigo
    assert creds.verificar_senha("nova-senha-2026") is True
    assert creds.verificar_senha(SENHA) is False
    # O código antigo morre no primeiro uso: não serve para reabrir a porta.
    assert creds.usar_codigo_de_recuperacao(codigo, "outra-senha-2027") is None
    assert creds.verificar_senha("outra-senha-2027") is False
    terceiro = creds.usar_codigo_de_recuperacao(novo, "terceira-senha-2028")
    assert terceiro and creds.verificar_senha("terceira-senha-2028") is True


def test_codigo_errado_nao_muda_nada(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    codigo = creds.criar(SENHA)
    errado = "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF"
    assert errado.replace("-", "") != oc.normalizar_codigo(codigo)
    assert creds.usar_codigo_de_recuperacao(errado, "senha-nova-2026") is None
    assert creds.usar_codigo_de_recuperacao("curto", "senha-nova-2026") is None
    assert creds.verificar_senha(SENHA) is True
    # O código legítimo continua valendo (nada foi rotacionado).
    assert creds.usar_codigo_de_recuperacao(codigo, "senha-nova-2026") is not None


def test_recuperar_com_senha_curta_e_recusado_antes_do_codigo(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    codigo = creds.criar(SENHA)
    with pytest.raises(ValueError):
        creds.usar_codigo_de_recuperacao(codigo, "curta")
    assert creds.verificar_senha(SENHA) is True
    assert creds.usar_codigo_de_recuperacao(codigo, "outra-boa-senha") is not None


def test_arquivo_corrompido_conta_como_nao_configurado(tmp_path):
    caminho = _arquivo(tmp_path)
    caminho.write_text("{lixo", encoding="utf-8")
    creds = oc.CredenciaisDoDono(caminho)
    assert creds.existe() is False
    assert creds.verificar_senha(SENHA) is False
    # O dono não fica trancado fora: pode reconfigurar por cima do arquivo.
    creds.criar(SENHA)
    assert creds.verificar_senha(SENHA) is True


def test_rotacionar_codigo_mantem_senha_e_mata_o_antigo(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    antigo = creds.criar(SENHA)
    novo = creds.rotacionar_codigo()
    assert novo and novo != antigo
    assert creds.verificar_senha(SENHA) is True
    assert creds.usar_codigo_de_recuperacao(antigo, "senha-nova-2026") is None
    assert creds.usar_codigo_de_recuperacao(novo, "senha-nova-2026") is not None


def test_rotacionar_sem_credenciais_devolve_none(tmp_path):
    creds = oc.CredenciaisDoDono(_arquivo(tmp_path))
    assert creds.rotacionar_codigo() is None
