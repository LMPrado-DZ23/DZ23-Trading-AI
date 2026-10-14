"""Compartilhamento controlado de conhecimento entre salas (BT-7).

O que atravessa salas é INFORMAÇÃO, nunca certificação. Uma estatística de
outra corretora entra como **prior rotulado**: origem, contexto e aviso
explícitos. Ela não muda estado de escola, não forma ninguém e não habilita
operação real — quem certifica continua sendo a evidência PRÓPRIA da sala,
no contexto de certificação dela.

Controles, cada um porque o oposto acontece no mundo real:

- Pacote é montado apenas com estatísticas (amostras, retorno médio, taxa de
  acerto). Campos de certificação são recusados NA ADOÇÃO: mesmo que alguém
  tente embutir um "estado: formada" para promover um trader de contrabando,
  o pacote não passa. A formação não viaja por anexo.
- Prior da própria sala é recusado: não é conhecimento externo, é eco.
- A adoção é explícita e auditável (registro com origem e instante). Nada
  de sincronização automática entre salas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import utcnow
from .salas import Sala, SalaError

AVISO_CONHECIMENTO = (
    "prior rotulado de outro contexto: não certifica, não substitui a "
    "evidência própria da sala e não habilita operação real")
SCHEMA_CONHECIMENTO = "dz23.conhecimento/1"

# Campos que representam CERTIFICAÇÃO — nunca atravessam salas.
CAMPOS_DE_CERTIFICACAO = ("estado", "formado_em", "candidatura_em",
                          "aprovada", "eventos")


@dataclass(frozen=True)
class PacoteDeConhecimento:
    """Estatísticas rotuladas de uma sala de origem."""
    origem: str                        # chave da sala de origem
    corretora: str
    mercado: str
    gerado_em: datetime
    estatisticas: tuple[dict, ...] = ()
    aviso: str = AVISO_CONHECIMENTO
    schema: str = SCHEMA_CONHECIMENTO

    def como_dict(self) -> dict:
        return {"schema": self.schema, "origem": self.origem,
                "corretora": self.corretora, "mercado": self.mercado,
                "gerado_em": self.gerado_em.isoformat(),
                "estatisticas": [dict(e) for e in self.estatisticas],
                "aviso": self.aviso}


def campos_de_certificacao(doc: dict) -> list[str]:
    """Quais campos proibidos o documento carrega (lista vazia = limpo)."""
    achados: list[str] = []

    def _varrer(valor) -> None:
        if isinstance(valor, dict):
            for k, v in valor.items():
                if k in CAMPOS_DE_CERTIFICACAO:
                    achados.append(k)
                _varrer(v)
        elif isinstance(valor, (list, tuple)):
            for item in valor:
                _varrer(item)

    _varrer(doc)
    return achados


def montar_pacote(sala: Sala, *,
                  estrategias: tuple[str, ...] | None = None,
                  agora: datetime | None = None) -> PacoteDeConhecimento:
    """Resumo estatístico da sala para compartilhar — SEM certificação.

    Por (estratégia × versão): amostras, retorno médio e taxa de acerto.
    `estrategias` limita o que sai; sem ele, sai tudo o que a sala sabe.
    """
    por_chave: dict[str, list] = {}
    for d in sala.desfechos:
        if estrategias is not None and d.estrategia not in estrategias:
            continue
        por_chave.setdefault(f"{d.estrategia}@{d.versao or '1'}",
                             []).append(d)
    binaria = sala.contexto.mercado == "opcoes_binarias"
    estatisticas = []
    for chave in sorted(por_chave):
        estrategia, _, versao = chave.partition("@")
        retornos = [d.retorno for d in por_chave[chave]]
        n = len(retornos)
        ganhos = sum(1 for r in retornos if r > 0)
        perdas = sum(1 for r in retornos if r < 0)
        estatisticas.append({
            "estrategia": estrategia, "versao": versao, "amostras": n,
            "retorno_medio": float(sum(retornos) / n) if n else 0.0,
            "taxa_acerto": (ganhos / (ganhos + perdas)
                            if (ganhos + perdas) else 0.0),
            "unidade": "taxa_de_acerto" if binaria else "retorno_liquido",
        })
    return PacoteDeConhecimento(
        origem=sala.contexto.chave, corretora=sala.contexto.corretora,
        mercado=sala.contexto.mercado, gerado_em=agora or utcnow(),
        estatisticas=tuple(estatisticas))


def adotar_conhecimento(sala: Sala, pacote: PacoteDeConhecimento) -> dict:
    """Registra o pacote como PRIOR na sala destino. Explícito e auditável.

    Recusa: pacote da própria sala e qualquer pacote que carregue campos de
    certificação — formação não se transfere por anexo.
    """
    doc = pacote.como_dict()
    if pacote.origem == sala.contexto.chave:
        raise SalaError(
            f"pacote da própria sala ({pacote.origem}) não é conhecimento "
            "externo — é eco, e eco não ensina nada")
    proibidos = campos_de_certificacao(doc)
    if proibidos:
        raise SalaError(
            "pacote traz campos de certificação "
            f"({', '.join(sorted(set(proibidos)))}): certificação não "
            "atravessa salas — a formação exige evidência própria no "
            "contexto de destino")
    registro = {**doc, "recebido_em": utcnow().isoformat()}
    sala.priors_conhecimento.append(registro)
    sala.salvar()
    return registro


def resumo_dos_priors(sala: Sala) -> list[dict]:
    """Visão para o painel: de onde veio cada prior e o que ele afirma."""
    return [{"origem": p.get("origem"), "corretora": p.get("corretora"),
             "mercado": p.get("mercado"), "gerado_em": p.get("gerado_em"),
             "recebido_em": p.get("recebido_em"),
             "amostras_totais": sum(int(e.get("amostras", 0))
                                    for e in p.get("estatisticas", [])),
             "aviso": p.get("aviso")}
            for p in sala.priors_conhecimento]


__all__ = ["PacoteDeConhecimento", "AVISO_CONHECIMENTO",
           "CAMPOS_DE_CERTIFICACAO", "SCHEMA_CONHECIMENTO",
           "adotar_conhecimento", "campos_de_certificacao", "montar_pacote",
           "resumo_dos_priors"]
