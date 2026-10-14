"""DZ23 Smart Setup (missão V8 complementar) — provas do núcleo e das rotas.

Fixam o que a configuração inteligente PROMETE: pré-configuração segura só
em campos vazios (atualizar o app nunca sobrescreve preferência do dono),
descoberta do que já está configurado sem expor chave, sugestão de modelos
a partir da descoberta real (não de nomes fixos), perfis, roteamento por
custo em tarefa simples e o bloqueio de orçamento OPCIONAL — que por padrão
não existe e nunca toca travas de risco.
"""
from __future__ import annotations

import importlib

import pytest

# O ambiente do dono TEM chaves reais (Groq/Mistral): testes que leem
# load_cfg precisam do ambiente limpo, senão o resultado depende da máquina.
# Nunca imprimir valores — só remover.
_VARS_IA = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY", "OPENAI_COMPATIBLE_API_KEY",
    "TRADINGAGENTS_LLM_PROVIDER", "TRADINGAGENTS_LLM_FALLBACK",
    "TRADINGAGENTS_QUICK_THINK_LLM", "TRADINGAGENTS_DEEP_THINK_LLM",
    "TRADINGAGENTS_BACKEND_URL", "TRADINGAGENTS_IA_ORCAMENTO_USD",
    "TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO",
    "TRADINGAGENTS_OUTPUT_LANGUAGE", "TRADINGAGENTS_DESK_CONTROLE",
)


def _limpar_ambiente_ia(monkeypatch):
    for var in _VARS_IA:
        monkeypatch.delenv(var, raising=False)


class TestClassificarModelo:
    """R3: classificação por token do nome — sem lista fixa de modelos."""

    def test_tokens_rapidos(self):
        from service.api.smart_setup import classificar_modelo

        for nome in ("gpt-4o-mini", "claude-3-5-haiku-latest",
                     "gemini-2.0-flash", "mistral-small-latest",
                     "llama-3.1-8b-instant", "meta-llama/llama-3.3-8b:free"):
            assert classificar_modelo(nome) == "rapido", nome

    def test_tokens_profundos(self):
        from service.api.smart_setup import classificar_modelo

        for nome in ("claude-opus-4", "claude-3-7-sonnet-latest",
                     "llama-3.3-70b-versatile", "deepseek-reasoner",
                     "gemini-2.5-pro", "mistral-large-latest"):
            assert classificar_modelo(nome) == "profundo", nome

    def test_neutro_quando_nao_sabe(self):
        from service.api.smart_setup import classificar_modelo

        # "deepseek-chat" contém a marca do provedor, não o token "deep";
        # MoE "8x7b" é ambíguo de propósito — neutro é resposta honesta.
        for nome in ("", "gpt-4o", "deepseek-chat", "mixtral-8x7b-32768"):
            assert classificar_modelo(nome) == "neutro", nome


class TestSugerirModelos:
    def test_escolhe_rapido_mais_barato_e_profundo_maior_referencia(self):
        from service.api.smart_setup import sugerir_modelos

        sug = sugerir_modelos("openai", ["gpt-4o", "gpt-4o-mini",
                                         "o3-mini", "gpt-4.1"])
        assert sug["rapido"] == "gpt-4o-mini"    # 0.15 < 1.10 (o3-mini)
        assert sug["profundo"] == "gpt-4o"       # 2.50 > 2.00 (gpt-4.1)
        assert sug["fonte"] == "descoberta"

    def test_sem_candidato_devolve_vazio_sem_inventar(self):
        from service.api.smart_setup import sugerir_modelos

        sug = sugerir_modelos("openai", ["gpt-4o", "gpt-4.1"])
        assert sug["rapido"] == ""
        assert sug["profundo"] == "gpt-4o"


class TestProvedoresConfigurados:
    def test_so_conta_quem_tem_chave(self):
        from service.api.smart_setup import provedores_configurados

        assert provedores_configurados({}) == []
        assert provedores_configurados({"GROQ_API_KEY": "k"}) == ["groq"]

    def test_sem_chave_local_somente_com_endereco_proprio(self):
        from service.api.smart_setup import provedores_configurados

        # Ollama pode não estar no ar: não conta só por existir no catálogo.
        assert "ollama" not in provedores_configurados({"GROQ_API_KEY": "k"})
        cfg = {"TRADINGAGENTS_BACKEND_URL": "http://localhost:11434/v1"}
        assert "ollama" in provedores_configurados(cfg)

    def test_nunca_devolve_valor_de_credencial(self):
        from service.api.smart_setup import provedores_configurados

        cfg = {"GROQ_API_KEY": "gsk-segredo-nunca-sai"}
        for item in provedores_configurados(cfg):
            assert "gsk" not in item


class TestEscolherProvedor:
    def test_economico_prefere_gratuito_depois_mais_barato(self):
        from service.api.smart_setup import escolher_provedor

        cfg = {"GROQ_API_KEY": "k", "MISTRAL_API_KEY": "k"}
        assert escolher_provedor(cfg, "economico") == "groq"  # 0.05 < 0.10
        cfg["OPENROUTER_API_KEY"] = "k"   # quick ':free' → custo zero
        assert escolher_provedor(cfg, "economico") == "openrouter"

    def test_equilibrado_usa_latencia_conhecida(self):
        from service.api.smart_setup import escolher_provedor

        cfg = {"GROQ_API_KEY": "k", "MISTRAL_API_KEY": "k"}
        testes = {"groq": {"ok": True, "latencia_ms": 900},
                  "mistral": {"ok": True, "latencia_ms": 300}}
        assert escolher_provedor(cfg, "equilibrado", testes) == "mistral"

    def test_avancado_prefere_provedor_com_saude(self):
        from service.api.smart_setup import escolher_provedor

        cfg = {"GROQ_API_KEY": "k", "MISTRAL_API_KEY": "k"}
        testes = {"groq": {"ok": True, "latencia_ms": 900},
                  "mistral": {"estado": "chave_rejeitada", "ok": False,
                              "latencia_ms": 300}}
        assert escolher_provedor(cfg, "avancado", testes) == "groq"

    def test_sem_chave_nenhuma_nao_escolhe(self):
        from service.api.smart_setup import escolher_provedor

        assert escolher_provedor({}, "pronto") is None


class TestSugestaoEAplicarPerfis:
    def test_sugestao_sem_provedor_explica_em_vez_de_inventar(self):
        from service.api.smart_setup import sugestao

        sug = sugestao({}, "pronto")
        assert sug["provedor"] == ""
        assert any("chave" in n for n in sug["notas"])

    def test_aplicar_pronto_em_instalacao_nova(self):
        from service.api.smart_setup import aplicar

        r = aplicar({}, "pronto")
        ap = r["aplicados"]
        assert ap["TRADINGAGENTS_OUTPUT_LANGUAGE"] == "Portuguese"
        assert ap["TRADINGAGENTS_MAX_DEBATE_ROUNDS"] == "1"
        assert ap["TRADINGAGENTS_DESK_CONTROLE"] == "0"
        # sem chave nenhuma: provedor NÃO é escolhido nem inventado
        assert "TRADINGAGENTS_LLM_PROVIDER" not in ap
        # R10: nenhuma chave de API é criada por um perfil
        assert not any(k.endswith("_KEY") for k in ap)

    def test_aplicar_com_chave_seleciona_provedor_e_modelo_do_catalogo(self):
        from service.api.smart_setup import aplicar

        r = aplicar({"GROQ_API_KEY": "k"}, "pronto")
        ap = r["aplicados"]
        assert ap["TRADINGAGENTS_LLM_PROVIDER"] == "groq"
        assert ap["TRADINGAGENTS_QUICK_THINK_LLM"] == "llama-3.1-8b-instant"
        # sem descoberta não há profundo — nada de nome fixo desatualizado
        assert "TRADINGAGENTS_DEEP_THINK_LLM" not in ap
        assert any("Buscar modelos" in n for n in r["sugestao"]["notas"])

    def test_modelos_descobertos_mandam_na_escolha(self):
        from service.api.smart_setup import aplicar

        r = aplicar(
            {"GROQ_API_KEY": "k"}, "pronto",
            modelos_por_provedor={"groq": ["llama-3.1-8b-instant",
                                           "llama-3.3-70b-versatile",
                                           "gemma2-9b-it"]})
        ap = r["aplicados"]
        assert ap["TRADINGAGENTS_QUICK_THINK_LLM"] == "llama-3.1-8b-instant"
        assert ap["TRADINGAGENTS_DEEP_THINK_LLM"] == "llama-3.3-70b-versatile"

    def test_idempotencia_r11_preferencia_do_dono_nunca_e_sobrescrita(self):
        from service.api.smart_setup import aplicar

        cfg = {"TRADINGAGENTS_OUTPUT_LANGUAGE": "English",
               "TRADINGAGENTS_LLM_PROVIDER": "mistral",
               "TRADINGAGENTS_QUICK_THINK_LLM": "meu-modelo",
               "MISTRAL_API_KEY": "k", "GROQ_API_KEY": "k"}
        r = aplicar(cfg, "avancado")
        assert "TRADINGAGENTS_OUTPUT_LANGUAGE" in r["mantidos"]
        assert "TRADINGAGENTS_LLM_PROVIDER" in r["mantidos"]
        assert "TRADINGAGENTS_QUICK_THINK_LLM" in r["mantidos"]
        assert r["cfg"]["TRADINGAGENTS_OUTPUT_LANGUAGE"] == "English"
        assert r["cfg"]["TRADINGAGENTS_LLM_PROVIDER"] == "mistral"
        assert r["cfg"]["TRADINGAGENTS_QUICK_THINK_LLM"] == "meu-modelo"
        # avancado difere de economico pelas rodadas de debate
        assert r["aplicados"]["TRADINGAGENTS_MAX_DEBATE_ROUNDS"] == "3"
        # fallback vazio ganha o OUTRO provedor com chave
        assert r["aplicados"]["TRADINGAGENTS_LLM_FALLBACK"] == "groq"

    def test_perfil_desconhecido_falha_alto(self):
        from service.api.smart_setup import aplicar

        with pytest.raises(ValueError):
            aplicar({}, "turbo-max")


class TestOrcamentoEfetivo:
    """R7: informativo por padrão; bloqueio SÓ ligado pelo dono."""

    def test_padrao_desligado_nunca_bloqueia(self):
        from service.api.smart_setup import ia_permitida

        orc = {"definido": True, "usd": 5.0, "usado_usd": 50.0}
        assert ia_permitida({}, orc) == (True, "")

    def test_bloqueio_ligado_para_o_que_estourou(self):
        from service.api.smart_setup import ia_permitida

        cfg = {"TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO": "1"}
        orc = {"definido": True, "usd": 5.0, "usado_usd": 5.2}
        permitida, motivo = ia_permitida(cfg, orc)
        assert permitida is False
        assert "orçamento de IA atingido" in motivo
        assert "travas de risco" in motivo   # deixa claro o alcance do bloqueio

    def test_bloqueio_ligado_dentro_do_limite_e_sem_orcamento_nao_bloqueia(self):
        from service.api.smart_setup import ia_permitida

        cfg = {"TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO": "sim"}
        assert ia_permitida(cfg, {"definido": True, "usd": 5.0,
                                  "usado_usd": 1.0})[0] is True
        assert ia_permitida(cfg, {"definido": False})[0] is True

    def test_variacoes_de_ligado_e_desligado(self):
        from service.api.smart_setup import bloqueio_ligado

        for v in ("1", "true", "sim", "on", "ligado", "YES"):
            assert bloqueio_ligado({"TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO": v})
        for v in ("", "0", "nao", "off"):
            assert not bloqueio_ligado({"TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO": v})

    def test_motivo_declara_estimativa_parcial(self):
        from service.api.smart_setup import ia_permitida

        cfg = {"TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO": "1"}
        orc = {"definido": True, "usd": 1.0, "usado_usd": 1.0, "parcial": True}
        _, motivo = ia_permitida(cfg, orc)
        assert "PARCIAL" in motivo


class TestDiagnostico:
    """R8: configurado → conectado → autenticado → operacional, com evidência."""

    def test_estados_progridem_com_evidencia(self):
        from service.api.smart_setup import diagnostico

        cfg = {"GROQ_API_KEY": "k"}
        testes = {"groq": {"estado": "ok", "ok": True, "latencia_ms": 120,
                           "quando": "2026-10-14T00:00:00+00:00"}}
        uso = {"por_provedor": {"groq": {"chamadas": 3}}}
        por_id = {d["id"]: d for d in diagnostico(cfg, testes, uso)}
        groq = por_id["groq"]
        assert groq["configurado"] and groq["conectado"]
        assert groq["autenticado"] and groq["operacional"]
        assert groq["chamadas_registradas"] == 3
        openai = por_id["openai"]
        assert not openai["configurado"]
        assert not (openai["conectado"] or openai["autenticado"]
                    or openai["operacional"])

    def test_chave_rejeitada_e_conectado_mas_nao_autenticado(self):
        from service.api.smart_setup import diagnostico

        cfg = {"MISTRAL_API_KEY": "k"}
        testes = {"mistral": {"estado": "chave_rejeitada", "ok": False,
                              "latencia_ms": 80}}
        por_id = {d["id"]: d for d in diagnostico(cfg, testes, {})}
        m = por_id["mistral"]
        assert m["conectado"] is True       # a rede respondeu (HTTP 401)
        assert m["autenticado"] is False
        assert m["operacional"] is False

    def test_sem_conexao_nao_e_conectado(self):
        from service.api.smart_setup import diagnostico

        testes = {"groq": {"estado": "sem_conexao", "ok": False}}
        por_id = {d["id"]: d for d in
                  diagnostico({"GROQ_API_KEY": "k"}, testes, {})}
        assert por_id["groq"]["conectado"] is False


class TestEstado:
    def test_primeira_execucao_lista_so_o_necessario(self):
        from service.api.smart_setup import estado

        e = estado({}, tem_config=False)
        assert e["primeira_execucao"] is True
        ids = {p["id"] for p in e["pendencias"]}
        assert "provedor" in ids and "idioma" in ids
        assert set(e["perfis"]) == {"pronto", "economico", "equilibrado",
                                    "avancado"}
        assert len(e["diagnostico"]) == 9   # todo o catálogo, sem esconder

    def test_configuracao_completa_sem_pendencias(self):
        from service.api.smart_setup import estado

        cfg = {"TRADINGAGENTS_LLM_PROVIDER": "groq",
               "TRADINGAGENTS_QUICK_THINK_LLM": "llama-x",
               "TRADINGAGENTS_OUTPUT_LANGUAGE": "Portuguese",
               "GROQ_API_KEY": "k"}
        e = estado(cfg, tem_config=True)
        assert e["primeira_execucao"] is False
        assert e["pendencias"] == []
        assert e["provedores_configurados"] == ["groq"]


class TestRoteamentoPorCusto:
    """R5: tarefa simples prioriza os mais econômicos entre os DECLARADOS —
    o principal do dono continua o primeiro."""

    def test_fallback_simples_ordena_por_custo(self):
        from service.api.ia_provedores import candidatos_llm

        cfg = {"TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "k",
               "TRADINGAGENTS_LLM_FALLBACK": "mistral, openrouter, ollama",
               "MISTRAL_API_KEY": "k", "OPENROUTER_API_KEY": "k"}
        paineis = [c["painel"] for c in candidatos_llm(cfg)]
        # principal fixo; openrouter ':free' (0) e ollama (0) na frente
        assert paineis == ["groq", "openrouter", "ollama", "mistral"]

    def test_tarefa_complexa_mantem_ordem_declarada(self):
        from service.api.ia_provedores import candidatos_llm

        cfg = {"TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "k",
               "TRADINGAGENTS_LLM_FALLBACK": "mistral, openrouter, ollama",
               "MISTRAL_API_KEY": "k", "OPENROUTER_API_KEY": "k"}
        paineis = [c["painel"] for c in candidatos_llm(cfg, tarefa="complexo")]
        assert paineis == ["groq", "mistral", "openrouter", "ollama"]

    def test_consulta_simples_tenta_o_mais_barato_primeiro(self):
        from service.api import ia_provedores

        tentativas = []

        def criar(**kw):
            tentativas.append(kw["model"])
            if kw["model"] == "llama-3.1-8b-instant":
                raise RuntimeError("provedor principal fora")
            resposta = type("R", (), {"content": "ok", "usage_metadata": {}})()
            return type("C", (), {"get_llm": lambda self: type(
                "L", (), {"invoke": lambda self, p: resposta})()})()

        cfg = {"TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "k",
               "TRADINGAGENTS_LLM_FALLBACK": "mistral, openrouter",
               "MISTRAL_API_KEY": "k", "OPENROUTER_API_KEY": "k"}
        texto, meta = ia_provedores.consultar_ia(cfg, "p", criar_cliente=criar)
        assert meta["provedor"] == "openrouter"   # o gratuito, não o mistral
        assert tentativas[0] == "llama-3.1-8b-instant"
        assert tentativas[1] == "meta-llama/llama-3.3-70b-instruct:free"


class TestRotasSetup:
    def _app(self, tmp_path, monkeypatch):
        _limpar_ambiente_ia(monkeypatch)
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return m

    def test_estado_e_diagnostico_respondem(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        r = c.get("/v1/setup/estado", headers=h)
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["primeira_execucao"] is True
        assert {p["id"] for p in corpo["pendencias"]} >= {"provedor", "idioma"}
        r = c.get("/v1/setup/diagnostico", headers=h)
        assert r.status_code == 200
        assert len(r.json()["provedores"]) == 9

    def test_aplicar_perfil_salva_somente_o_que_faltava(self, tmp_path,
                                                       monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        # dono já tinha escolhido idioma: não pode ser sobrescrito (R11)
        c.post("/v1/config", headers=h,
               json={"vars": {"TRADINGAGENTS_OUTPUT_LANGUAGE": "English"}})
        r = c.post("/v1/setup/aplicar", headers=h, json={"perfil": "pronto"})
        assert r.status_code == 200
        corpo = r.json()
        assert "TRADINGAGENTS_OUTPUT_LANGUAGE" in corpo["mantidos"]
        assert corpo["aplicados"]["TRADINGAGENTS_MAX_DEBATE_ROUNDS"] == "1"
        cfg = c.get("/v1/config", headers=h).json()["config"]
        assert cfg["TRADINGAGENTS_OUTPUT_LANGUAGE"]["value"] == "English"
        assert cfg["TRADINGAGENTS_DESK_CONTROLE"]["value"] == "0"

    def test_perfil_invalido_400(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        r = c.post("/v1/setup/aplicar", headers=h, json={"perfil": "turbo"})
        assert r.status_code == 400

    def test_bloqueio_de_orcamento_ponta_a_ponta(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        c.post("/v1/config", headers=h, json={"vars": {
            "TRADINGAGENTS_IA_ORCAMENTO_USD": "1,00",
            "TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO": "1"}})
        # sem uso registrado: dentro do limite, permitido
        assert m._ia_bloqueio("legacy") == ""
        # uso real registrado pelo caminho de produção (custo 2.50 > 1.00)
        m.IA_USO.registrar("legacy", {
            "provedor": "openai", "modelo": "gpt-4o",
            "tokens": {"entrada": 1_000_000, "saida": 0}})
        motivo = m._ia_bloqueio("legacy")
        assert "orçamento de IA atingido" in motivo
        # desligar o bloqueio volta a permitir (é opcional por desenho)
        c.post("/v1/config", headers=h, json={
            "remover": ["TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO"]})
        assert m._ia_bloqueio("legacy") == ""
