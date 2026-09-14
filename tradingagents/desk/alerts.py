"""Alertas que acordam o dono (melhoria ⑥).

Hoje só se descobre um problema abrindo o painel. Os quatro problemas que
não podem esperar alguém abrir o painel:

1. **Ordem incerta** — enviou e não sabe se executou. Cada minuto parado
   é risco não medido.
2. **Contabilidade desbalanceada** — o livro deixou de fechar. Isso nunca é
   normal.
3. **Perda diária perto do limite** — avisar ao chegar perto vale mais do
   que avisar quando já parou.
4. **Mesa parada sem querer** — o laço morreu e ninguém viu.

Decisões que evitam o pior defeito de sistemas de alerta (virar ruído e ser
ignorado):
- Cada alerta tem período de silêncio: o mesmo problema não vira 200
  mensagens.
- Severidade explícita. URGENTE é raro de propósito.
- Mensagem nunca carrega chave, token ou saldo — ela viaja por canal de
  terceiros.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

SILENCIO_PADRAO = 1800          # 30 min sem repetir o mesmo alerta


@dataclass(frozen=True)
class Alerta:
    codigo: str
    severidade: str             # urgente | atencao | informativo
    titulo: str
    detalhe: str
    acao: str

    def texto(self) -> str:
        marca = {"urgente": "🔴 URGENTE", "atencao": "🟡 ATENÇÃO",
                 "informativo": "🔵"}.get(self.severidade, "")
        return f"{marca} {self.titulo}\n{self.detalhe}\n➡️ {self.acao}"


def avaliar_estado(*, pendencias_reconciliacao: list[str],
                   contabilidade_balanceada: bool,
                   perda_hoje: Decimal, limite_perda: Decimal,
                   mesa_deveria_rodar: bool, mesa_rodando: bool,
                   erros_do_ciclo: int = 0) -> list[Alerta]:
    """Traduz o estado da mesa em alertas. Função pura — fácil de testar e
    impossível de mentir."""
    alertas: list[Alerta] = []
    if pendencias_reconciliacao:
        alertas.append(Alerta(
            "ordem_incerta", "urgente",
            f"{len(pendencias_reconciliacao)} ordem(ns) em estado incerto",
            f"Ordens: {', '.join(pendencias_reconciliacao[:5])}. Pode haver "
            "posição aberta na corretora sem registro confirmado.",
            "Abra a Mesa de Agentes e reconcilie antes de operar mais."))
    if not contabilidade_balanceada:
        alertas.append(Alerta(
            "livro_desbalanceado", "urgente", "Contabilidade não fecha",
            "O livro-razão está desbalanceado — isso nunca é normal.",
            "Pare novas entradas e verifique os lançamentos do dia."))
    if limite_perda > 0 and perda_hoje > 0:
        proporcao = perda_hoje / limite_perda
        if proporcao >= 1:
            alertas.append(Alerta(
                "limite_perda_atingido", "urgente",
                "Limite de perda diária atingido",
                "Perda do dia chegou ao limite configurado.",
                "Os envios estão bloqueados. Revise antes de liberar."))
        elif proporcao >= Decimal("0.8"):
            alertas.append(Alerta(
                "limite_perda_proximo", "atencao",
                "Perda do dia perto do limite",
                f"Já usou {proporcao:.0%} do limite de perda diária.",
                "Considere reduzir exposição ou parar por hoje."))
    if mesa_deveria_rodar and not mesa_rodando:
        alertas.append(Alerta(
            "mesa_parada", "atencao", "Mesa autônoma parada",
            "A mesa deveria estar rodando e não está.",
            "Ligue novamente na aba Mesa de Agentes e veja os erros do ciclo."))
    if erros_do_ciclo >= 3:
        alertas.append(Alerta(
            "ciclos_com_erro", "atencao",
            f"{erros_do_ciclo} erros seguidos no ciclo",
            "A mesa segue viva, mas os ciclos estão falhando.",
            "Verifique o feed de dados e as chaves em Configuração."))
    return alertas


class Notificador:
    """Entrega por Telegram ou webhook genérico. Sem canal, guarda em memória
    para o painel mostrar — nunca perde o alerta em silêncio."""

    def __init__(self, *, telegram_token: str = "", telegram_chat: str = "",
                 webhook_url: str = "", silencio: int = SILENCIO_PADRAO,
                 transporte=None):
        self.telegram_token = telegram_token.strip()
        self.telegram_chat = telegram_chat.strip()
        self.webhook_url = webhook_url.strip()
        self.silencio = silencio
        self._transporte = transporte
        self._ultimo_envio: dict[str, float] = {}
        self.historico: list[dict] = []

    @property
    def configurado(self) -> bool:
        return bool((self.telegram_token and self.telegram_chat) or self.webhook_url)

    def _em_silencio(self, codigo: str, agora: float) -> bool:
        ultimo = self._ultimo_envio.get(codigo)
        return ultimo is not None and (agora - ultimo) < self.silencio

    def enviar(self, alertas: list[Alerta], agora: float | None = None) -> dict:
        agora = agora if agora is not None else time.time()
        enviados, silenciados, falhas = [], [], []
        for a in alertas:
            if self._em_silencio(a.codigo, agora):
                silenciados.append(a.codigo)
                continue
            registro = {"em": agora, "codigo": a.codigo,
                        "severidade": a.severidade, "titulo": a.titulo,
                        "detalhe": a.detalhe, "acao": a.acao}
            self.historico.append(registro)
            del self.historico[:-100]
            if not self.configurado:
                self._ultimo_envio[a.codigo] = agora
                silenciados.append(a.codigo)
                continue
            try:
                self._entregar(a)
                self._ultimo_envio[a.codigo] = agora
                enviados.append(a.codigo)
            except Exception as exc:
                falhas.append(f"{a.codigo}: {type(exc).__name__}")
        return {"enviados": enviados, "silenciados": silenciados,
                "falhas": falhas, "canal_configurado": self.configurado}

    def _entregar(self, alerta: Alerta) -> None:
        if self._transporte is not None:
            self._transporte(alerta)
            return
        import httpx
        if self.telegram_token and self.telegram_chat:
            httpx.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat, "text": alerta.texto()},
                timeout=10).raise_for_status()
        elif self.webhook_url:
            httpx.post(self.webhook_url,
                       json={"codigo": alerta.codigo,
                             "severidade": alerta.severidade,
                             "titulo": alerta.titulo, "detalhe": alerta.detalhe,
                             "acao": alerta.acao},
                       timeout=10).raise_for_status()


__all__ = ["Alerta", "Notificador", "avaliar_estado", "SILENCIO_PADRAO"]
