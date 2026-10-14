"""Credenciais do dono: senha como hash Argon2id + código de recuperação.

Antes: a senha vivia em texto puro no owner_password.txt, era copiada para a
variável de ambiente e comparada byte a byte. Qualquer pessoa (ou programa)
com leitura do arquivo tinha a senha; backup, suporte remoto ou print de tela
a expunham sem querer. Agora o disco guarda apenas o hash Argon2id — a
verificação é lenta de propósito e o arquivo, sozinho, não devolve a senha.

O código de recuperação cobre o "esqueci a senha" sem editar arquivos nem
rodar comandos: é exibido UMA vez, na criação, e a cada uso senha e código
são rotacionados (o antigo morre na hora).

Formato de owner_credentials.json:
  {"versao": 1,
   "senha_hash": "$argon2id$...",
   "codigo_hash": "$argon2id$...",
   "criado_em": "ISO-8601", "atualizado_em": "ISO-8601"}
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from tradingagents.desk.storage import escrever_json_atomico

SENHA_MINIMA = 8
NOME_ARQUIVO = "owner_credentials.json"
# Sem I/L/O/0/1: anotar de cabeça ou ditar por telefone não confunde letra/número.
_ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_GRUPOS, _POR_GRUPO = 6, 5
_TAMANHO_CODIGO = _GRUPOS * _POR_GRUPO

_ph = PasswordHasher()
_LOCK = threading.Lock()


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gerar_codigo_recuperacao() -> str:
    return "-".join(
        "".join(secrets.choice(_ALFABETO) for _ in range(_POR_GRUPO))
        for _ in range(_GRUPOS)
    )


def normalizar_codigo(texto: str) -> str:
    """Caixa, hífens e espaços não importam: sobra só A-Z/2-9."""
    return "".join(c for c in str(texto).upper() if c.isascii() and c.isalnum())


def erro_de_senha(senha: str) -> str | None:
    if len(str(senha)) < SENHA_MINIMA:
        return f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres."
    return None


class CredenciaisDoDono:
    """Leitura/gravação das credenciais em um único arquivo JSON."""

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)

    def _ler(self) -> dict | None:
        try:
            doc = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or not isinstance(doc.get("senha_hash"), str):
            return None
        return doc

    def existe(self) -> bool:
        """Arquivo EM USO (parseável). Corrompido conta como não configurado:
        fail-closed para autenticar, mas o dono não fica trancado fora — o
        assistente de configuração reaparece por cima."""
        return self._ler() is not None

    def criar(self, senha: str) -> str:
        """Define a senha do dono e devolve o código de recuperação (única
        exibição em claro; depois só o hash fica no disco)."""
        erro = erro_de_senha(senha)
        if erro:
            raise ValueError(erro)
        codigo = gerar_codigo_recuperacao()
        agora = _agora()
        with _LOCK:
            escrever_json_atomico(self.caminho, {
                "versao": 1,
                "senha_hash": _ph.hash(senha),
                "codigo_hash": _ph.hash(normalizar_codigo(codigo)),
                "criado_em": agora,
                "atualizado_em": agora,
            })
        return codigo

    def verificar_senha(self, senha: str) -> bool:
        doc = self._ler()
        if doc is None:
            return False
        try:
            return bool(_ph.verify(doc["senha_hash"], senha))
        except VerificationError:
            return False

    def usar_codigo_de_recuperacao(self, codigo: str, nova_senha: str) -> str | None:
        """Troca a senha usando o código de recuperação. Devolve o NOVO código
        ou None se o código estiver errado; senha fraca levanta ValueError."""
        erro = erro_de_senha(nova_senha)
        if erro:
            raise ValueError(erro)
        normalizado = normalizar_codigo(codigo)
        if len(normalizado) != _TAMANHO_CODIGO:
            return None
        with _LOCK:
            doc = self._ler()
            if doc is None:
                return None
            try:
                _ph.verify(doc.get("codigo_hash", ""), normalizado)
            except VerificationError:
                return None
            novo = gerar_codigo_recuperacao()
            doc.update({
                "senha_hash": _ph.hash(nova_senha),
                "codigo_hash": _ph.hash(normalizar_codigo(novo)),
                "atualizado_em": _agora(),
            })
            escrever_json_atomico(self.caminho, doc)
            return novo

    def rotacionar_codigo(self) -> str | None:
        """Gera e grava um código de recuperação NOVO (o anterior morre na
        hora). Devolve o código em claro — única exibição — ou None quando
        não há credenciais de arquivo."""
        with _LOCK:
            doc = self._ler()
            if doc is None:
                return None
            novo = gerar_codigo_recuperacao()
            doc.update({
                "codigo_hash": _ph.hash(normalizar_codigo(novo)),
                "atualizado_em": _agora(),
            })
            escrever_json_atomico(self.caminho, doc)
            return novo
