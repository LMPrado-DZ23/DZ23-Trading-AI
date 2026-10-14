"""Armazenamento de segredos em REPOUSO, cifrado e portátil (SEC-1).

Problema (achado SEC-1): as chaves de API do dono viviam em TEXTO CLARO no
`config.json`, protegidas apenas por `chmod 600` — fraco no Windows/NTFS e
exposto a backup, suporte remoto ou print de tela. Diferente da senha de login
(hash Argon2id, irreversível), uma chave de API precisa ser LIDA de volta para
chamar a corretora/LLM; logo, exige CIFRAGEM reversível, não hash.

Solução: Fernet (AES-128-CBC + HMAC, da `cryptography`) cifra os segredos em
`secrets.<namespace>.enc`. A chave-mestra fica em `secret.key` com permissão
restritiva; no Windows ela é ENVELOPADA por DPAPI (CryptProtectData, atada à
conta do usuário) quando `win32crypt` estiver disponível — sem dependência
obrigatória nem quebra de portabilidade: fora do Windows, ou sem pywin32, a
chave é gravada diretamente com `chmod 600` (equivalente ao que o Oracle Linux
oferece para um arquivo de serviço de um único dono).

Fail-closed: se a chave ou o arquivo cifrado não puderem ser lidos/decifrados,
`get` devolve None (segredo ausente) em vez de derrubar o processo — o dono vê
o campo como "não configurado", nunca um 500.

Nenhum valor de segredo é registrado em log, exceção ou repr.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_DPAPI_MAGIC = b"DZ23-DPAPI1\n"
_DPAPI_DESC = "DZ23 Trading AI secret-store master key"


def _dpapi_protect(raw: bytes) -> bytes | None:
    """Envelopa `raw` com DPAPI (Windows). None se indisponível."""
    try:
        import win32crypt  # type: ignore
    except Exception:
        return None
    with contextlib.suppress(Exception):
        return win32crypt.CryptProtectData(raw, _DPAPI_DESC, None, None, None, 0)
    return None


def _dpapi_unprotect(blob: bytes) -> bytes | None:
    try:
        import win32crypt  # type: ignore
    except Exception:
        return None
    with contextlib.suppress(Exception):
        _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data
    return None


def _escrever_bytes_atomico(caminho: Path, dados: bytes, modo: int = 0o600) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(caminho.parent), prefix=".tmp-", suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(dados)
            fh.flush()
            os.fsync(fh.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp, modo)
        os.replace(tmp, caminho)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.remove(tmp)
    with contextlib.suppress(OSError):
        os.chmod(caminho, modo)


class SecretStore:
    """Segredos cifrados em repouso para um namespace (ex.: o inquilino).

    Uso:
        store = SecretStore(DATA, namespace="legacy")
        store.set("OPENAI_API_KEY", "sk-...")
        store.get("OPENAI_API_KEY")   # -> "sk-..." (ou None)
        store.all()                   # -> {nome: valor} decifrado
    """

    def __init__(self, data_dir, namespace: str = "legacy"):
        self.dir = Path(data_dir)
        self.key_path = self.dir / "secret.key"
        safe_ns = "".join(c for c in str(namespace) if c.isalnum() or c in "-_") or "legacy"
        self.store_path = self.dir / f"secrets.{safe_ns}.enc"

    # ---------------------------------------------------------------- chave
    def _carregar_ou_criar_chave(self) -> bytes:
        if self.key_path.exists():
            bruto = self.key_path.read_bytes()
            if bruto.startswith(_DPAPI_MAGIC):
                desprotegido = _dpapi_unprotect(bruto[len(_DPAPI_MAGIC):])
                if desprotegido is not None:
                    return desprotegido
                # Chave envelopada por DPAPI mas indecifrável nesta conta:
                # fail-closed — não inventa chave nova (destruiria os segredos).
                raise InvalidToken("secret.key DPAPI ilegível nesta conta")
            return bruto
        # Primeira vez: gera a chave-mestra Fernet e a protege.
        chave = Fernet.generate_key()
        protegido = _dpapi_protect(chave)
        if protegido is not None:
            _escrever_bytes_atomico(self.key_path, _DPAPI_MAGIC + protegido)
        else:
            _escrever_bytes_atomico(self.key_path, chave)
        return chave

    def _fernet(self) -> Fernet:
        return Fernet(self._carregar_ou_criar_chave())

    # -------------------------------------------------------------- dados
    def _ler_tudo(self) -> dict:
        if not self.store_path.exists():
            return {}
        try:
            texto = self._fernet().decrypt(self.store_path.read_bytes())
            doc = json.loads(texto.decode("utf-8"))
            return doc if isinstance(doc, dict) else {}
        except (InvalidToken, ValueError, OSError):
            # Fail-closed: ilegível = sem segredos (o dono reconfigura),
            # nunca um crash nem o valor cru.
            return {}

    def _gravar_tudo(self, doc: dict) -> None:
        texto = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        _escrever_bytes_atomico(self.store_path, self._fernet().encrypt(texto))

    # -------------------------------------------------------------- API
    def get(self, nome: str) -> str | None:
        valor = self._ler_tudo().get(nome)
        return valor if isinstance(valor, str) else None

    def all(self) -> dict:
        return {k: v for k, v in self._ler_tudo().items() if isinstance(v, str)}

    def set(self, nome: str, valor: str) -> None:
        doc = self._ler_tudo()
        doc[str(nome)] = str(valor)
        self._gravar_tudo(doc)

    def set_many(self, itens: dict) -> None:
        if not itens:
            return
        doc = self._ler_tudo()
        for k, v in itens.items():
            doc[str(k)] = str(v)
        self._gravar_tudo(doc)

    def delete(self, nome: str) -> None:
        doc = self._ler_tudo()
        if nome in doc:
            del doc[nome]
            self._gravar_tudo(doc)

    def has_any(self) -> bool:
        return bool(self._ler_tudo())

    @property
    def dpapi_ativo(self) -> bool:
        """True se a chave em disco está envelopada por DPAPI (Windows)."""
        try:
            return self.key_path.exists() and self.key_path.read_bytes().startswith(_DPAPI_MAGIC)
        except OSError:
            return False


def mascarar(valor: str | None) -> str:
    """Máscara para exibição/diagnóstico — nunca o valor inteiro."""
    if not valor:
        return ""
    s = str(valor)
    if len(s) <= 4:
        return "•" * len(s)
    return s[:2] + "•" * max(4, len(s) - 4) + s[-2:]
