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


def _limpar_ambiente_ia(monkeypatch):
    """Hermeticidade: a máquina do dono pode ter chaves REAIS no ambiente
    do sistema (ele as usa de verdade). Teste NUNCA deve tocar a rede do
    dono por acidente nem ver credencial real: remove as env vars de IA do
    processo de teste (restauradas no fim pelo monkeypatch)."""
    from service.api.ia_provedores import CATALOGO

    for p in CATALOGO:
        if p["chave_env"]:
            monkeypatch.delenv(p["chave_env"], raising=False)


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


class TestSondagemDeProvedores:
    """Teste de autenticação/descoberta com transporte falso: nada de rede,
    mas o caminho real (headers, URL, classificação de erro) é exercitado."""

    def _cfg(self, **kw):
        base = {"TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "k-groq"}
        base.update(kw)
        return base

    def _transport(self, handler):
        import httpx

        return httpx.MockTransport(handler)

    def test_sem_chave_nao_sai_rede(self):
        from service.api import ia_provedores

        def handler(_req):
            raise AssertionError("sondagem não pode tocar a rede sem chave")

        r = ia_provedores.testar_provedor(
            {"TRADINGAGENTS_LLM_PROVIDER": "groq"}, "groq",
            transport=self._transport(handler))
        assert r["ok"] is False and r["estado"] == "sem_chave"
        assert "k-groq" not in str(r)

    def test_ok_captura_latencia_e_limites(self):
        from service.api import ia_provedores

        visto = {}

        def handler(req):
            visto["url"] = str(req.url)
            visto["auth"] = req.headers.get("authorization")
            import httpx

            return httpx.Response(
                200, json={"data": [{"id": f"m{i}"} for i in range(20)]},
                headers={"x-ratelimit-remaining-requests": "9",
                         "retry-after": "3"})

        r = ia_provedores.testar_provedor(self._cfg(), "groq",
                                          transport=self._transport(handler))
        assert r["ok"] is True and r["estado"] == "ok"
        assert r["total_modelos"] == 20
        assert len(r["modelos"]) <= 12          # amostra, não despejo
        assert r["limites"]["x-ratelimit-remaining-requests"] == "9"
        assert r["limites"]["retry-after"] == "3"
        assert r["latencia_ms"] >= 0
        assert visto["url"] == "https://api.groq.com/openai/v1/models"
        assert visto["auth"] == "Bearer k-groq"
        assert "k-groq" not in str(r)           # resposta nunca ecoa a chave

    def test_401_vira_chave_rejeitada(self):
        from service.api import ia_provedores

        r = ia_provedores.testar_provedor(
            self._cfg(), "groq",
            transport=self._transport(lambda _req: __import__("httpx").Response(
                401, json={"error": {"message": "Invalid API Key k-groq"}})))
        assert r["ok"] is False and r["estado"] == "chave_rejeitada"
        assert r["status"] == 401

    def test_429_traz_limites_e_nao_vaza_corpo(self):
        import httpx

        from service.api import ia_provedores

        r = ia_provedores.testar_provedor(
            self._cfg(), "groq",
            transport=self._transport(lambda _req: httpx.Response(
                429, text="quota exceeded for key k-groq",
                headers={"retry-after": "60"})))
        assert r["estado"] == "limite_de_taxa"
        assert r["limites"]["retry-after"] == "60"
        assert "k-groq" not in str(r)

    def test_tempo_esgotado(self):
        import httpx

        from service.api import ia_provedores

        def handler(req):
            raise httpx.ConnectTimeout("estourou", request=req)

        r = ia_provedores.testar_provedor(self._cfg(), "groq",
                                          transport=self._transport(handler))
        assert r["ok"] is False and r["estado"] == "tempo_esgotado"
        assert "k-groq" not in str(r)

    def test_anthropic_usa_headers_proprios(self):
        from service.api import ia_provedores

        visto = {}

        def handler(req):
            visto["url"] = str(req.url)
            visto["x-api-key"] = req.headers.get("x-api-key")
            visto["versao"] = req.headers.get("anthropic-version")
            import httpx

            return httpx.Response(200, json={"data": [{"id": "claude-3-5-haiku-latest"}]})

        r = ia_provedores.descobrir_modelos(
            {"TRADINGAGENTS_LLM_PROVIDER": "anthropic",
             "ANTHROPIC_API_KEY": "k-ant"}, "anthropic",
            transport=self._transport(handler))
        assert r["fonte"] == "api" and r["modelos"] == ["claude-3-5-haiku-latest"]
        assert visto["url"] == "https://api.anthropic.com/v1/models"
        assert visto["x-api-key"] == "k-ant"
        assert visto["versao"]
        assert "k-ant" not in str(r)

    def test_google_usa_v1beta_e_tira_prefixo(self):
        from service.api import ia_provedores

        visto = {}

        def handler(req):
            visto["url"] = str(req.url)
            import httpx

            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-2.0-flash"},
                {"name": "models/gemini-2.5-pro"}]})

        r = ia_provedores.descobrir_modelos(
            {"TRADINGAGENTS_LLM_PROVIDER": "google",
             "GOOGLE_API_KEY": "k-goog"}, "google",
            transport=self._transport(handler))
        assert r["modelos"] == ["gemini-2.0-flash", "gemini-2.5-pro"]
        assert visto["url"] == "https://generativelanguage.googleapis.com/v1beta/models"

    def test_descobrir_sem_chave_cai_no_catalogo_com_fonte_honesta(self):
        from service.api import ia_provedores

        r = ia_provedores.descobrir_modelos(
            {"TRADINGAGENTS_LLM_PROVIDER": "mistral"}, "mistral")
        assert r["ok"] is False and r["fonte"] == "catalogo"
        assert r["estado"] == "sem_chave"
        assert r["modelos"] == ["mistral-small-latest"]

    def test_resposta_ilegivel_nao_finge_descoberta(self):
        import httpx

        from service.api import ia_provedores

        r = ia_provedores.descobrir_modelos(
            self._cfg(), "groq",
            transport=self._transport(lambda _req: httpx.Response(200, text="<html>")))
        assert r["ok"] is False and r["fonte"] == "catalogo"
        assert r["estado"] == "resposta_ilegivel"
        assert r["modelos"] == ["llama-3.1-8b-instant"]

    def test_endereco_nao_confiavel_e_recusado_antes_da_rede(self):
        from service.api import ia_provedores

        def handler(_req):
            raise AssertionError("trava anti-SSRF deveria barrar antes da rede")

        r = ia_provedores.testar_provedor(
            {"TRADINGAGENTS_LLM_PROVIDER": "openai_compatible",
             "TRADINGAGENTS_BACKEND_URL": "http://relay.exemplo.com/v1",
             "OPENAI_COMPATIBLE_API_KEY": "k-relay"}, "openai_compatible",
            transport=self._transport(handler))
        assert r["ok"] is False and r["estado"] == "endereco_recusado"
        assert "k-relay" not in str(r)


class TestRotasProvedores:
    """Rotas /v1/ai/* no app real: catálogo, teste de autenticação e
    descoberta de modelos — autenticadas e SEM nunca devolver a chave."""

    def _app(self, tmp_path, monkeypatch):
        _limpar_ambiente_ia(monkeypatch)
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import importlib

        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_rotas_sem_auth_recusam(self, tmp_path, monkeypatch):
        _, c, _ = self._app(tmp_path, monkeypatch)
        assert c.get("/v1/ai/provedores").status_code == 401
        assert c.post("/v1/ai/testar", json={"provedor": "groq"}).status_code == 401
        assert c.get("/v1/ai/modelos").status_code == 401

    def test_lista_traz_os_nove_e_estado_das_chaves(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=h, json={"vars": {
            "GROQ_API_KEY": "k-groq-secreto",
            "TRADINGAGENTS_LLM_PROVIDER": "groq",
            "TRADINGAGENTS_LLM_FALLBACK": "mistral, openrouter"}})
        assert r.status_code == 200, r.text

        r = c.get("/v1/ai/provedores", headers=h)
        assert r.status_code == 200
        d = r.json()
        por_id = {p["id"]: p for p in d["provedores"]}
        for exigido in ("openai", "anthropic", "google", "deepseek",
                        "groq", "mistral", "openrouter", "ollama",
                        "openai_compatible"):
            assert exigido in por_id
        assert por_id["groq"]["tem_chave"] is True
        assert por_id["mistral"]["tem_chave"] is False
        assert por_id["ollama"]["tem_chave"] is None  # não tem chave, por desenho
        assert d["principal"] == "groq"
        assert d["fallback"] == "mistral, openrouter"
        assert "k-groq-secreto" not in r.text  # credencial nunca sai

    def test_testar_sem_chave_e_honesto_sem_rede(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/ai/testar", headers=h, json={"provedor": "groq"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False and d["estado"] == "sem_chave"
        assert "GROQ_API_KEY" in d["detalhe"]

    def test_testar_com_provedor_desconhecido(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/ai/testar", headers=h, json={"provedor": "meu-provedor"})
        assert r.json()["estado"] == "desconhecido"

    def test_testar_usa_a_sonda_real(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k-groq"}})
        import service.api.ia_provedores as iap

        def sonda(cfg, entrada, *, transport=None):
            assert cfg.get("GROQ_API_KEY") == "k-groq"  # sonda recebe a config
            return {"ok": True, "estado": "ok", "latencia_ms": 12.5,
                    "limites": {"retry-after": "1"}, "modelos": ["m1", "m2"]}

        monkeypatch.setattr(iap, "_sondar", sonda)
        r = c.post("/v1/ai/testar", headers=h, json={"provedor": "groq"})
        d = r.json()
        assert d["ok"] is True and d["provedor"] == "groq"
        assert d["total_modelos"] == 2 and d["latencia_ms"] == 12.5
        assert "k-groq" not in r.text

    def test_modelos_com_chave_usa_descoberta_real(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k-groq"}})
        import service.api.ia_provedores as iap

        monkeypatch.setattr(iap, "_sondar", lambda cfg, entrada, *, transport=None: {
            "ok": True, "estado": "ok", "latencia_ms": 8.0,
            "limites": {}, "modelos": ["llama-3.3-70b-versatile",
                                       "llama-3.1-8b-instant"]})
        r = c.get("/v1/ai/modelos", headers=h, params={"provedor": "groq"})
        d = r.json()
        assert d["fonte"] == "api"
        assert d["modelos"] == ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
        assert "k-groq" not in r.text

    def test_modelos_sem_chave_com_fonte_catalogo(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.get("/v1/ai/modelos", headers=h, params={"provedor": "groq"})
        d = r.json()
        assert d["fonte"] == "catalogo" and d["estado"] == "sem_chave"
        assert d["modelos"] == ["llama-3.1-8b-instant"]


class TestRemoverCredenciais:
    """V8 §6: 'registrar E REMOVER'. Antes o Salvar só aceitava campo
    não-vazio — chave errada ficava presa para sempre."""

    def _app(self, tmp_path, monkeypatch):
        _limpar_ambiente_ia(monkeypatch)
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import importlib

        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_remover_apaga_a_chave_salva(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k-groq"}})
        assert c.get("/v1/config", headers=h).json()["config"]["GROQ_API_KEY"]["set"]

        r = c.post("/v1/config", headers=h, json={"remover": ["GROQ_API_KEY"]})
        assert r.status_code == 200
        d = r.json()
        assert d["removidos"] == ["GROQ_API_KEY"]
        assert d["ainda_no_ambiente"] == []
        assert not c.get("/v1/config", headers=h).json()["config"]["GROQ_API_KEY"]["set"]

    def test_remover_com_ambiente_avisa_que_ainda_vem_do_sistema(
            self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "k-do-ambiente")
        r = c.post("/v1/config", headers=h, json={"remover": ["GROQ_API_KEY"]})
        d = r.json()
        assert d["removidos"] == []
        assert d["ainda_no_ambiente"] == ["GROQ_API_KEY"]
        # E a chave do ambiente NUNCA aparece no corpo.
        assert "k-do-ambiente" not in r.text

    def test_remover_sozinho_nao_da_400_e_vazio_da_400(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=h, json={"remover": ["GROQ_API_KEY"]})
        assert r.status_code == 200
        assert c.post("/v1/config", headers=h, json={}).status_code == 400
        assert c.post("/v1/config", headers=h,
                      json={"vars": {"GROQ_API_KEY": "  "}}).status_code == 400

    def test_remover_chave_de_fora_da_lista_e_ignorada(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=h, json={"remover": ["QUALQUER_COISA"]})
        assert r.status_code == 400  # nada pedido de verdade


class TestPrecos:
    def test_preco_conhecido_e_prefixo_mais_longo(self):
        from tradingagents.llm_clients import precos

        assert precos.preco("groq", "llama-3.1-8b-instant") == (0.05, 0.08)
        # versionado casa com o prefixo MAIS longo — não com o curto
        assert precos.preco("openai", "gpt-4o-mini-2024-07-18") == (0.15, 0.60)
        assert precos.preco("google", "gemini-2.0-flash-lite-001") == (0.075, 0.30)

    def test_gratuito_e_diferente_de_desconhecido(self):
        from tradingagents.llm_clients import precos

        assert precos.preco("ollama", "qualquer") == (0.0, 0.0)
        assert precos.preco(
            "openrouter", "meta-llama/llama-3.3-70b-instruct:free") == (0.0, 0.0)
        assert precos.preco("openai_compatible", "meu-modelo") is None
        assert precos.preco("naoexiste", "qualquer") is None
        assert precos.preco("openai", "gpt-999-inexistente") is None

    def test_custo_estimado_matematica_e_none_sem_preco(self):
        from tradingagents.llm_clients import precos

        assert precos.custo_estimado(
            "openai", "gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
        assert precos.custo_estimado(
            "groq", "llama-3.1-8b-instant", 0, 1_000_000) == pytest.approx(0.08)
        assert precos.custo_estimado("openai_compatible", "x", 10, 10) is None

    def test_referencia_datada(self):
        from tradingagents.llm_clients import precos

        assert len(precos.REFERENCIA) == 7 and precos.REFERENCIA[4] == "-"


class TestRegistroDeUso:
    def _reg(self, tmp_path):
        from service.api.ia_uso import RegistroDeUso

        return RegistroDeUso(tmp_path / "ia-uso.json")

    def test_conta_chamada_tokens_e_custo(self, tmp_path):
        r = self._reg(tmp_path)
        r.registrar("t1", {"provedor": "groq", "modelo": "llama-3.1-8b-instant",
                           "tokens": {"entrada": 1000, "saida": 500}})
        resumo = r.resumo("t1")
        assert resumo["total"]["chamadas"] == 1
        assert resumo["total"]["tokens_entrada"] == 1000
        assert resumo["total"]["tokens_saida"] == 500
        # (1000*0.05 + 500*0.08)/1e6 = 0.00009 USD
        assert resumo["total"]["custo_estimado_usd"] == pytest.approx(0.00009)
        assert resumo["por_provedor"]["groq"]["chamadas"] == 1
        assert resumo["custo_parcial"] is False
        assert resumo["orcamento"]["definido"] is False
        assert "informativo" in resumo["orcamento"]["nota"]

    def test_sem_tokens_e_sem_preco_contam_honestamente(self, tmp_path):
        r = self._reg(tmp_path)
        r.registrar("t1", {"provedor": "groq",
                           "modelo": "llama-3.1-8b-instant", "tokens": None})
        r.registrar("t1", {"provedor": "openai_compatible", "modelo": "meu",
                           "tokens": {"entrada": 10, "saida": 10}})
        resumo = r.resumo("t1")
        assert resumo["total"]["chamadas"] == 2
        assert resumo["total"]["chamadas_sem_uso"] == 1
        assert resumo["total"]["chamadas_sem_preco"] == 1
        assert resumo["custo_parcial"] is True
        assert resumo["total"]["custo_estimado_usd"] == 0.0

    def test_persiste_e_rele_do_disco(self, tmp_path):
        self._reg(tmp_path).registrar(
            "t1", {"provedor": "groq", "modelo": "llama-3.1-8b-instant",
                   "tokens": {"entrada": 1, "saida": 1}})
        assert self._reg(tmp_path).resumo("t1")["total"]["chamadas"] == 1

    def test_orcamento_fracao_e_restante(self, tmp_path):
        r = self._reg(tmp_path)
        r.registrar("t1", {"provedor": "openai", "modelo": "gpt-4o-mini",
                           "tokens": {"entrada": 1_000_000, "saida": 0}})
        resumo = r.resumo("t1", orcamento_usd=0.30)
        assert resumo["orcamento"]["usado_usd"] == pytest.approx(0.15)
        assert resumo["orcamento"]["restante_usd"] == pytest.approx(0.15)
        assert resumo["orcamento"]["fracao"] == pytest.approx(0.5)


class TestRotaUso:
    def _app(self, tmp_path, monkeypatch):
        _limpar_ambiente_ia(monkeypatch)
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import importlib

        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_uso_sem_auth_recusa(self, tmp_path, monkeypatch):
        _, c, _ = self._app(tmp_path, monkeypatch)
        assert c.get("/v1/ai/uso").status_code == 401

    def test_uso_zerado_sem_orcamento(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        d = c.get("/v1/ai/uso", headers=h).json()
        assert d["total"]["chamadas"] == 0
        assert d["orcamento"]["definido"] is False
        assert d["moeda"] == "USD" and d["referencia_precos"]

    def test_uso_mostra_o_que_o_app_registrou(self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        m.IA_USO.registrar("legacy", {
            "provedor": "groq", "modelo": "llama-3.1-8b-instant",
            "tokens": {"entrada": 2000, "saida": 1000}})
        d = c.get("/v1/ai/uso", headers=h).json()
        assert d["total"]["chamadas"] == 1
        assert d["total"]["tokens_entrada"] == 2000
        assert d["total"]["custo_estimado_usd"] == pytest.approx(0.00018)

    def test_orcamento_configurado_aceita_virgula(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=h, json={
            "vars": {"TRADINGAGENTS_IA_ORCAMENTO_USD": "5,00"}})
        assert r.status_code == 200
        d = c.get("/v1/ai/uso", headers=h).json()
        assert d["orcamento"]["definido"] is True
        assert d["orcamento"]["usd"] == pytest.approx(5.0)

    def test_orcamento_invalido_avisa_sem_mentir(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={
            "vars": {"TRADINGAGENTS_IA_ORCAMENTO_USD": "abc"}})
        d = c.get("/v1/ai/uso", headers=h).json()
        assert d["orcamento"]["definido"] is False
        assert "inválido" in d["orcamento"]["aviso"]

    def test_os_dois_caminhos_de_consulta_registram_uso(self):
        fonte = (RAIZ / "service" / "app.py").read_text(encoding="utf-8")
        assert fonte.count("IA_USO.registrar(") == 2, (
            "os DOIS caminhos de consulta (IA de contexto e revisor do "
            "diário) precisam registrar consumo — um deles perdeu o registro")
