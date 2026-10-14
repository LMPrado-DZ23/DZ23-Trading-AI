"""Governança da camada de IA/quant: conselho, nunca comando (item 9).

A regra é estrutural, não uma promessa de documentação: um conselheiro que
exponha QUALQUER método com cara de execução (enviar ordem, reservar capital,
mexer em limite, promover para LIVE) é rejeitado na construção.
"""
from __future__ import annotations

# Radicais de nome que denunciam capacidade de comando. Comparação em
# substring minúscula — cobre variações em português e inglês.
RADICAIS_DE_COMANDO: tuple[str, ...] = (
    "enviar", "send", "executar", "execute", "reservar", "reserve",
    "ordem", "order", "limite", "limit", "live", "promover",
)


class ViolacaoDeGovernanca(RuntimeError):
    """Conselheiro com capacidade de comando — recusado por construção."""


def auditar_aconselhador(obj) -> list[str]:
    """Lista métodos públicos que violam o papel consultivo; vazio = ok."""
    violacoes: list[str] = []
    for nome in dir(obj):
        if nome.startswith("_"):
            continue
        try:
            atributo = getattr(obj, nome)
        except Exception:
            # Atributo que explode ao ser lido não é comando; o próprio
            # conselheiro trata isso como indisponibilidade.
            continue
        if callable(atributo) and any(r in nome.lower() for r in RADICAIS_DE_COMANDO):
            violacoes.append(nome)
    return sorted(violacoes)


def exigir_aconselhador(obj) -> None:
    """Lança se `obj` puder comandar; retorno silencioso = apto a aconselhar."""
    violacoes = auditar_aconselhador(obj)
    if violacoes:
        raise ViolacaoDeGovernanca(
            f"{type(obj).__name__} expõe método(s) de comando: {violacoes} — "
            "IA/quant só aconselha (item 9)")
