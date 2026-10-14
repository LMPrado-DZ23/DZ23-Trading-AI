"""Segredos em repouso — proteção EFETIVA dos valores no arquivo de
configuração (correção do achado SEC-1 da auditoria independente).

O QUE ERA: `save_cfg` gravava o config.json inteiro em texto claro e a
única proteção era `os.chmod(path, 0o600)`. No Windows — plataforma
principal do produto — `os.chmod` altera apenas o atributo SOMENTE-LEITURA;
ACL não é tocada. Na prática, qualquer usuário local lia chaves de IA,
chaves da corretora e tokens de alerta direto do disco.

O QUE PASSA A SER: valores com cara de credencial são gravados cifrados com
a DPAPI do próprio usuário do Windows — `CryptProtectData` via ctypes, sem
dependência nova — com entropia amarrada ao produto. Formato no disco:
`enc:dpapi:v1:<base64>`. A leitura decifra de forma transparente. No POSIX
o módulo não inventa criptografia caseira: o arquivo continua 0600 (ali o
chmod é ACL de verdade) e o status diz isso com honestidade.

SEM PERDA DE CREDENCIAL: valor cifrado que não pode ser decifrado (outro
usuário do Windows, outra máquina, blob corrompido) NÃO é apagado nem
sobrescrito — permanece no arquivo e é reportado; o dono cola a credencial
por cima quando quiser. A migração de config legado (texto claro) acontece
no primeiro uso com verificação de ida-e-volta ANTES de gravar: cifra,
decifra e confere; se não bater, o arquivo não é tocado.

LIMITAÇÃO CONHECIDA (documentada, não escondida): DPAPI protege contra
OUTROS usuários da máquina e contra leitura casual do arquivo; o próprio
usuário (e qualquer processo rodando como ele) decifra. Levar o config
para outra máquina exige re-colar os segredos — o blob continua lá,
ilegível. Proteção contra o próprio usuário exigiria hardware/HSM.
"""
from __future__ import annotations

import base64
import ctypes
import os
import sys

PREFIXO = "enc:dpapi:v1:"

# Entropia amarrada ao produto: sem este valor exato, nem o próprio DPAPI
# devolve o segredo. Não é segredo — é contexto de aplicação.
_ENTROPIA = b"DZ23-Trading-AI/segredos-em-repouso/v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SegredoIlegivel(RuntimeError):
    """O valor cifrado não pôde ser decifrado NESTE contexto (usuário,
    máquina ou blob corrompido). O valor em disco PERMANECE — jamais
    apagar credencial por falha de leitura."""


def e_cifrado(valor) -> bool:
    """O valor gravado está no formato cifrado deste módulo?"""
    return isinstance(valor, str) and valor.startswith(PREFIXO)


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char))]

    # c_uint32 == DWORD do Windows e evita `ctypes.wintypes`, que não existe
    # fora do Windows — este módulo precisa IMPORTAR em qualquer plataforma.


_crypt32_cache = None


def _crypt32():
    global _crypt32_cache
    if _crypt32_cache is None:
        _crypt32_cache = ctypes.WinDLL("crypt32", use_last_error=True)
    return _crypt32_cache


def tem_protecao() -> bool:
    """DPAPI disponível neste processo? (Windows apenas)"""
    if sys.platform != "win32":
        return False
    try:
        _crypt32()
        return True
    except OSError:
        return False


def _blob_de(dados: bytes) -> tuple[_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(dados, len(dados))
    return _BLOB(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(dados: bytes, *, proteger: bool) -> bytes:
    crypt32 = _crypt32()
    blob_in, _buf_in = _blob_de(dados)
    blob_entropia, _buf_ent = _blob_de(_ENTROPIA)
    blob_out = _BLOB()
    if proteger:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), "DZ23-Trading-AI",
            ctypes.byref(blob_entropia), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, ctypes.byref(blob_entropia),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        erro = ctypes.get_last_error()
        raise OSError(erro, f"DPAPI recusou a operação (erro {erro})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def cifrar(valor: str) -> str:
    """Prepara um segredo para o disco.

    Windows: DPAPI do usuário + verificação de ida-e-volta — o que não se
    consegue decifrar de volta NÃO é gravado (o chamador decide o que fazer
    com a falha). POSIX: devolve como está — a proteção ali é o 0600 do
    arquivo, e fingir criptografia caseira seria pior que nenhuma.
    """
    if not tem_protecao():
        return valor
    blob = _dpapi(valor.encode("utf-8"), proteger=True)
    token = PREFIXO + base64.b64encode(blob).decode("ascii")
    if decifrar(token) != valor:
        raise SegredoIlegivel(
            "verificação de ida-e-volta da cifragem falhou — o valor NÃO foi "
            "gravado como cifrado")
    return token


def decifrar(token: str) -> str:
    """Decifra um valor gravado. Valor não-cifrado volta como está.

    Falha aqui significa que o segredo existe mas não é legível NESTE
    contexto — levantar (e nunca devolver vazio) é o que impede tratar
    'ilegível' como 'não configurado' e descartar a credencial no próximo
    save.
    """
    if not e_cifrado(token):
        return token
    if not tem_protecao():
        raise SegredoIlegivel(
            "segredo cifrado com a DPAPI do Windows não é decifrável nesta "
            "plataforma — re-colar o segredo, ou abrir na máquina/usuário "
            "que o cifrou (o valor cifrado continua no arquivo)")
    try:
        bruto = base64.b64decode(token[len(PREFIXO):], validate=True)
        return _dpapi(bruto, proteger=False).decode("utf-8")
    except SegredoIlegivel:
        raise
    except Exception as exc:
        raise SegredoIlegivel(
            f"segredo cifrado ilegível neste contexto ({type(exc).__name__}); "
            "o valor cifrado continua no arquivo — re-colar sobrescreve") from exc


def status() -> dict:
    """Estado HONESTO da proteção, para o painel mostrar o que é verdade."""
    if tem_protecao():
        return {
            "protecao": "dpapi-usuario",
            "efetiva": True,
            "detalhe": ("Segredos gravados cifrados com a DPAPI do usuário "
                        "do Windows: outro usuário da máquina não lê o "
                        "arquivo. O próprio usuário decifra — proteção "
                        "contra o próprio usuário exigiria hardware."),
        }
    if os.name == "posix":
        return {
            "protecao": "arquivo-0600",
            "efetiva": True,
            "detalhe": ("POSIX: o arquivo de configuração é 0600 — permissão "
                        "de verdade neste sistema, aplicada pela atomicidade "
                        "de escrita do módulo de armazenamento."),
        }
    return {
        "protecao": "indisponivel",
        "efetiva": False,
        "detalhe": ("Nem DPAPI nem 0600 efetivo nesta plataforma: os "
                    "segredos ficam apenas no arquivo. Evite gravar chaves "
                    "nesta máquina."),
    }


__all__ = ["PREFIXO", "SegredoIlegivel", "cifrar", "decifrar", "e_cifrado",
           "status", "tem_protecao"]
