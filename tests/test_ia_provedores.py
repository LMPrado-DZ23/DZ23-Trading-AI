"""V8 §6 — configuração completa de IA: consulta correta, fallback, uso.

Contexto do defeito (RED original): os dois caminhos de consulta de IA do
serviço (`_consultar` da IA de contexto e `_revisor_de_ia` do diário manual)
criavam o cliente passando um DICT como `model` e chamavam
`cliente.complete()`/`cliente.invoke()` — métodos que NÃO existem no
`BaseLLMClient`. Probe no próprio repositório:

    create_llm_client('openai_compatible', {...}) -> OpenAIClient
    hasattr(cliente, 'complete') == False; hasattr(cliente, 'invoke') == False

Toda consulta degenerava em "falha na consulta (AttributeError)". O teste
estático abaixo é o guarda de regressão do padrão, e os testes de unidade
provam o caminho correto: `get_llm().invoke()` com credencial explícita.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


class _RespostaFalsa:
    """Imita o AIMessage do LangChain (content + usage_metadata)."""

    def __init__(self, texto: str, uso: dict | None = None):
        self.content = texto
        self.usage_metadata = uso


class _LLMFalso:
    def __init__(self, dono: _ClienteFalso):
        self._dono = dono

    def invoke(self, prompt, config=None, **kw):
        self._dono.chamadas.append(prompt)
        if self._dono.erro:
            raise self._dono.erro
        return _RespostaFalsa(self._dono.texto, self._dono.uso)


class _ClienteFalso:
    def __init__(self, texto="ok", uso=None, erro=None, ao_construir=None):
        self.texto, self.uso, self.erro = texto, uso, erro
        self.chamadas: list[str] = []
        self._ao_construir = ao_construir

    def get_llm(self):
        if self._ao_construir:
            self._ao_construir()
        return _LLMFalso(self)


class TestEstaticoDefeitoConsulta:
    """Guarda de regressão: o padrão quebrado não pode voltar ao service."""

    def test_app_nao_chama_complete_nem_invoke_no_cliente(self):
        fonte = (RAIZ / "service" / "app.py").read_text(encoding="utf-8")
        proibidos = ["cliente.complete(", "cliente.invoke(", "str(cliente.invoke"]
        presentes = [p for p in proibidos if p in fonte]
        assert presentes == [], (
            "service/app.py voltou a chamar o cliente fora de get_llm().invoke(): "
            f"{presentes}. O BaseLLMClient não expõe complete()/invoke(); use "
            "consultar_ia() de service/api/ia_provedores.py.")


class TestCatalogoProvedores:
    def test_primeira_classe_do_prompt_v8(self):
        from service.api.ia_provedores import CATALOGO

        ids = [p["id"] for p in CATALOGO]
        for exigido in ("openai", "anthropic", "google", "deepseek",
                        "groq", "mistral", "openrouter"):
            assert exigido in ids, f"provedor {exigido} ausente do catálogo"

    def test_espelha_free_providers_sem_drift(self):
        from service import app as m
        from service.api.ia_provedores import CATALOGO

        por_id = {p["id"]: p for p in CATALOGO}
        for prov in ("groq", "mistral", "openrouter", "ollama"):
            assert por_id[prov]["quick_padrao"] == m.FREE_PROVIDERS[prov]["quick"], (
                f"quick_padrao de {prov} divergiu de FREE_PROVIDERS — "
                "as duas tabelas precisam dizer o mesmo modelo")

    def test_segredo_nunca_no_catalogo(self):
        from service.api.ia_provedores import CATALOGO

        for p in CATALOGO:
            assert not any("sk-" in str(v) for v in p.values())


class TestCandidatos:
    def _cfg(self, **kw):
        base = {"TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "k-groq"}
        base.update(kw)
        return base

    def test_principal_primeiro_e_usa_modelo_do_painel(self):
        from service.api.ia_provedores import candidatos_llm

        candidatos = candidatos_llm(self._cfg(
            TRADINGAGENTS_QUICK_THINK_LLM="llama-custom"))
        assert candidatos[0]["painel"] == "groq"
        assert candidatos[0]["modelo"] == "llama-custom"
        assert candidatos[0]["chave"] == "k-groq"

    def test_fallback_ordem_dedupe_e_modelo_padrao(self):
        from service.api.ia_provedores import candidatos_llm

        candidatos = candidatos_llm(self._cfg(
            TRADINGAGENTS_LLM_FALLBACK="mistral, groq, mistral, desconhecido",
            MISTRAL_API_KEY="k-mistral"))
        paineis = [c["painel"] for c in candidatos]
        # principal uma vez, mistral uma vez, desconhecido ignorado
        assert paineis == ["groq", "mistral"]
        assert candidatos[1]["modelo"]  # padrão do provedor, não o do painel
        assert candidatos[1]["chave"] == "k-mistral"

    def test_sem_provedor_configurado_lista_vazia(self):
        from service.api.ia_provedores import candidatos_llm

        assert candidatos_llm({}) == []

    def test_ollama_dispensa_chave(self):
        from service.api.ia_provedores import candidatos_llm

        candidatos = candidatos_llm({
            "TRADINGAGENTS_LLM_PROVIDER": "ollama",
            "TRADINGAGENTS_BACKEND_URL": "http://localhost:11434/v1"})
        assert candidatos and candidatos[0]["chave"] == ""
        assert candidatos[0]["exige_chave"] is False


class TestConsultarIa:
    def _cfg(self, **kw):
        base = {"TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "k-groq"}
        base.update(kw)
        return base

    def test_caminho_correto_get_llm_invoke_com_credencial_explicita(self):
        from service.api import ia_provedores

        capturado = {}

        def criar(**kw):
            capturado.update(kw)
            return _ClienteFalso(texto='{"veredito": "seguir"}',
                                 uso={"input_tokens": 10, "output_tokens": 5})

        texto, meta = ia_provedores.consultar_ia(
            self._cfg(), "prompt", criar_cliente=criar)

        assert texto == '{"veredito": "seguir"}'
        # O modelo vai como STRING (o defeito antigo mandava o dict de conf).
        assert isinstance(capturado["model"], str) and capturado["model"]
        assert capturado["provider"]
        # Credencial explícita: o caminho NÃO depende de os.environ ficar sujo.
        assert capturado["api_key"] == "k-groq"
        assert meta["provedor"] == "groq"
        assert meta["tokens"] == {"entrada": 10, "saida": 5}

    def test_ambiente_limpo_depois_da_construcao(self):
        from service.api import ia_provedores

        self._cfg()
        vistos = {}

        def criar(**kw):
            vistos["env_na_construcao"] = os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            return _ClienteFalso(texto="ok")

        ia_provedores.consultar_ia(self._cfg(), "p", criar_cliente=criar)
        # A janela de ambiente existiu para a construção (família compatível)…
        assert vistos["env_na_construcao"] == "k-groq"
        # …e foi fechada de volta.
        assert os.environ.get("OPENAI_COMPATIBLE_API_KEY") is None

    def test_fallback_quando_o_principal_falha(self):
        from service.api import ia_provedores

        tentados = []

        def criar(**kw):
            tentados.append(kw["provider"])
            if kw["provider"] == "openai_compatible" and len(tentados) == 1:
                return _ClienteFalso(erro=RuntimeError("provedor fora"))
            return _ClienteFalso(texto="do-fallback")

        texto, meta = ia_provedores.consultar_ia(
            self._cfg(TRADINGAGENTS_LLM_FALLBACK="mistral",
                      MISTRAL_API_KEY="k-mistral"),
            "p", criar_cliente=criar)

        assert texto == "do-fallback"
        assert meta["provedor"] == "mistral"
        estados = {t["provedor"]: t["estado"] for t in meta["tentativas"]}
        assert estados == {"groq": "erro", "mistral": "ok"}

    def test_fallback_sem_chave_e_pulado_sem_mentir(self):
        from service.api import ia_provedores

        def criar(**kw):
            raise AssertionError("não deveria construir sem chave")

        with pytest.raises(RuntimeError) as erro:
            ia_provedores.consultar_ia(
                self._cfg(TRADINGAGENTS_LLM_FALLBACK="mistral"), "p",
                criar_cliente=criar)
        msg = str(erro.value)
        assert "mistral" in msg and "sem chave" in msg
        assert "k-groq" not in msg  # segredo nunca na mensagem

    def test_falha_total_agrega_tentativas_sem_vazar_segredo(self):
        from service.api import ia_provedores

        def criar(**kw):
            return _ClienteFalso(erro=RuntimeError("401 unauthorized"))

        with pytest.raises(RuntimeError) as erro:
            ia_provedores.consultar_ia(self._cfg(), "p", criar_cliente=criar)
        msg = str(erro.value)
        assert "nenhum provedor" in msg
        assert "k-groq" not in msg
        assert "401" not in msg  # detalhe técnico não vaza credencial/corpo

    def test_resposta_sem_usage_nao_inventa_tokens(self):
        from service.api import ia_provedores

        texto, meta = ia_provedores.consultar_ia(
            self._cfg(), "p", criar_cliente=lambda **kw: _ClienteFalso(texto="só texto"))
        assert texto == "só texto"
        assert meta["tokens"] is None
