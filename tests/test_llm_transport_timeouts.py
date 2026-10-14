"""Rodada-8 (auditoria pós-V8): timeouts finitos no transporte LLM.

Achado (verificado in loco contra os SDKs instalados — langchain-openai
1.6.6 + openai 3.19.2): a langchain passa ``timeout=None`` EXPLICITAMENTE
ao SDK; o SDK o trata como valor "dado" (``is_given(None)`` é True) e
carimba esse None em TODA requisição (``timeout = self.timeout`` em
``openai/_base_client.py``), sobrescrevendo o default do ``httpx.Client``
fornecido. Postura efetiva até esta rodada: connect/read/write/pool
desabilitados em cada chamada de LLM — uma conexão travada pendura o ciclo
sem limite algum.

Este arquivo pina a correção: timeouts finitos e generosos nos clientes
endurecidos E no caminho real de construção (``timeout=`` explícito, que o
SDK consome por requisição), com precedência do usuário preservada.
"""
from __future__ import annotations

import os
from unittest import mock

from openai._base_client import normalize_legacy_httpx_timeout

# connect, read, write, pool — contrato pinado (valores finitos e generosos)
ESPERADO = (10.0, 300.0, 30.0, 10.0)


def _intervalos(t) -> tuple:
    """(connect, read, write, pool) do timeout, normalizado como o SDK faz."""
    t = normalize_legacy_httpx_timeout(t)
    if t is None:
        return (None, None, None, None)
    return (
        getattr(t, "connect", t),
        getattr(t, "read", t),
        getattr(t, "write", t),
        getattr(t, "pool", t),
    )


class TestTransporteEndurecido:
    def test_sync_declara_timeouts_finitos(self):
        from tradingagents.llm_clients.transport import build_hardened_http_client

        assert _intervalos(build_hardened_http_client().timeout) == ESPERADO

    def test_async_declara_timeouts_finitos(self):
        from tradingagents.llm_clients.transport import (
            build_hardened_async_http_client,
        )

        assert _intervalos(build_hardened_async_http_client().timeout) == ESPERADO


class TestConstrucaoOpenAI:
    @staticmethod
    def _llm(**kwargs):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123456789"}):
            from tradingagents.llm_clients.openai_client import OpenAIClient

            return OpenAIClient("gpt-5.6", provider="openai", **kwargs).get_llm()

    def test_sync_aplica_timeout_finito_por_requisicao(self):
        # root_client.timeout é EXATAMENTE o valor que o SDK carimba em cada
        # requisição — hoje chega None (todos os timeouts desabilitados).
        assert _intervalos(self._llm().root_client.timeout) == ESPERADO

    def test_async_aplica_timeout_finito_por_requisicao(self):
        assert _intervalos(self._llm().root_async_client.timeout) == ESPERADO

    def test_timeout_do_usuario_prevalece(self):
        llm = self._llm(timeout=5.0)
        assert _intervalos(llm.root_client.timeout) == (5.0, 5.0, 5.0, 5.0)


class TestConstrucaoAzure:
    def test_aplica_timeout_finito_por_requisicao(self):
        env = {"AZURE_OPENAI_ENDPOINT": "https://myres.openai.azure.com",
               "AZURE_OPENAI_API_KEY": "azkey-123456789",
               "OPENAI_API_VERSION": "2025-03-01-preview"}
        with mock.patch.dict(os.environ, env):
            from tradingagents.llm_clients.azure_client import AzureOpenAIClient

            llm = AzureOpenAIClient("gpt-5.6").get_llm()
        assert _intervalos(llm.root_client.timeout) == ESPERADO


def _escalar(t):
    """Timeout como escalar em segundos (float do SDK ou httpx.Timeout)."""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return float(t)
    return float(getattr(t, "read", t))


class TestConstrucaoAnthropic:
    """Mesma classe de achado no SDK Anthropic: sem timeout explícito o
    campo ``default_request_timeout`` fica None e o SDK o carimba em cada
    requisição (nenhum limite). O pydantic da ChatAnthropic aceita apenas
    float — um httpx.Timeout seria rejeitado."""

    @staticmethod
    def _llm(**kwargs):
        env = {"ANTHROPIC_API_KEY": "sk-ant-test-123456789"}
        with mock.patch.dict(os.environ, env):
            from tradingagents.llm_clients.anthropic_client import AnthropicClient

            return AnthropicClient("claude-sonnet-5", **kwargs).get_llm()

    def test_cliente_carrega_timeout_finito(self):
        assert _escalar(self._llm()._client.timeout) == 300.0

    def test_timeout_do_usuario_prevalece(self):
        assert _escalar(self._llm(timeout=5.0)._client.timeout) == 5.0


class TestConstrucaoGoogle:
    """Mesma classe de achado no caminho Google: ``self.timeout`` None vira
    ``http_options.timeout`` None. O campo é consumido POR REQUISIÇÃO
    (``int(self.timeout * 1000)`` ms em chat_models.py)."""

    @staticmethod
    def _llm(**kwargs):
        from tradingagents.llm_clients.google_client import GoogleClient

        return GoogleClient("gemini-3-pro", api_key="gkey-test-123456789",
                            **kwargs).get_llm()

    def test_modelo_carrega_timeout_finito(self):
        assert _escalar(self._llm().timeout) == 300.0

    def test_timeout_do_usuario_prevalece(self):
        assert _escalar(self._llm(timeout=7.5).timeout) == 7.5
