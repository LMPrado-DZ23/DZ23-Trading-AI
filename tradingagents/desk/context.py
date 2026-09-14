"""IA de contexto e veto (melhoria ④).

A promessa "agência de IA" só se cumpre se a IA agregar onde ela é boa:
**ler contexto que número não captura** — evento macro marcado, notícia de
hack na exchange, anúncio regulatório — e **impedir** a operação.

Princípios inegociáveis (é por isso que esta camada é segura):
- A IA só pode fazer DUAS coisas: **vetar** ou **reduzir**. Nunca aumentar
  tamanho, nunca liberar o que o risco vetou, nunca alterar limites.
- Se a IA estiver fora do ar, a mesa NÃO para: opera com a marca de "sem
  verificação de contexto". Provedor caído não pode virar prejuízo — mas
  também não pode virar silêncio: o ciclo registra que faltou.
- Resposta que não obedece ao formato é tratada como ausente. LLM que
  "inventa" não ganha direito de voto.
- Tudo o que a IA disser fica no registro do ciclo, para você auditar.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

VEREDITOS = ("seguir", "reduzir", "vetar")

PROMPT = """Você é o analista de risco de contexto de uma mesa de trading.
Sua ÚNICA função é dizer se há motivo de CONTEXTO para não operar agora.

Instrumento: {instrumento}
Operação pretendida: {acao} · exposição {exposicao}
Leitura técnica da mesa: {leitura}
Data/hora de referência: {quando}

Considere apenas fatos que você conheça com segurança: eventos macro
marcados, decisões regulatórias, incidentes em exchanges, mudanças de
protocolo. NÃO opine sobre direção de preço, NÃO preveja o mercado, NÃO
sugira tamanho.

Responda SOMENTE com JSON, sem texto em volta:
{{"veredito": "seguir|reduzir|vetar", "motivo": "<uma frase curta>",
  "confianca": <0 a 1>}}

Use "vetar" apenas com motivo concreto e verificável. Na dúvida, "seguir"."""


@dataclass(frozen=True)
class ParecerDeContexto:
    veredito: str                 # seguir | reduzir | vetar
    motivo: str
    confianca: float
    disponivel: bool = True

    @property
    def fator_de_exposicao(self) -> Decimal:
        if self.veredito == "vetar":
            return Decimal("0")
        if self.veredito == "reduzir":
            return Decimal("0.5")
        return Decimal("1")

    def resumo(self) -> str:
        if not self.disponivel:
            return f"sem verificação de contexto ({self.motivo})"
        return f"contexto: {self.veredito} — {self.motivo}"


AUSENTE = ParecerDeContexto("seguir", "analista de contexto indisponível", 0.0, False)


def _parse(bruto: str) -> ParecerDeContexto | None:
    """Aceita só o formato combinado. Texto livre não vira decisão."""
    if not bruto:
        return None
    texto = bruto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto[texto.find("{"):] if "{" in texto else texto
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        dados = json.loads(texto[inicio:fim + 1])
    except ValueError:
        return None
    veredito = str(dados.get("veredito", "")).strip().lower()
    if veredito not in VEREDITOS:
        return None
    try:
        conf = float(dados.get("confianca", 0))
    except (TypeError, ValueError):
        conf = 0.0
    motivo = str(dados.get("motivo", "")).strip()[:200] or "sem motivo declarado"
    return ParecerDeContexto(veredito, motivo, max(0.0, min(1.0, conf)))


class AnalistaDeContexto:
    """Consulta o provedor de IA já configurado pelo cliente.

    `cliente` é qualquer callable que receba um prompt e devolva texto —
    assim o painel escolhe Groq, Gemini, Ollama etc. sem este módulo saber.
    """

    def __init__(self, cliente=None, *, confianca_minima_para_vetar: float = 0.6):
        self.cliente = cliente
        self.confianca_minima = confianca_minima_para_vetar

    def avaliar(self, *, instrumento: str, acao: str, exposicao: Decimal,
                leitura: str, quando: str) -> ParecerDeContexto:
        if self.cliente is None:
            return AUSENTE
        prompt = PROMPT.format(instrumento=instrumento, acao=acao,
                               exposicao=f"{exposicao:.1%}", leitura=leitura,
                               quando=quando)
        try:
            bruto = self.cliente(prompt)
        except Exception as exc:
            return ParecerDeContexto("seguir", f"falha na consulta ({type(exc).__name__})",
                                     0.0, False)
        parecer = _parse(bruto if isinstance(bruto, str) else str(bruto))
        if parecer is None:
            # Resposta fora do formato: a IA não ganha voto por insistência.
            return ParecerDeContexto("seguir", "resposta do modelo ilegível", 0.0, False)
        if parecer.veredito == "vetar" and parecer.confianca < self.confianca_minima:
            # Veto fraco vira redução: evita que um palpite pare a mesa,
            # sem ignorar o alerta.
            return ParecerDeContexto("reduzir",
                                     f"{parecer.motivo} (confiança baixa para vetar)",
                                     parecer.confianca)
        return parecer


def aplicar_parecer(exposicao: Decimal, parecer: ParecerDeContexto
                    ) -> tuple[Decimal, str]:
    """Aplica o parecer. Só encolhe — nunca aumenta."""
    nova = (exposicao * parecer.fator_de_exposicao).quantize(Decimal("0.0001"))
    return min(nova, exposicao), parecer.resumo()


__all__ = ["AnalistaDeContexto", "ParecerDeContexto", "aplicar_parecer",
           "AUSENTE", "PROMPT"]
