"""SS-3 (R14): matriz de cenários de homologação do Smart Setup em instância
isolada, no nível HTTP — os fluxos reais que o dono vive no aplicativo.

Cenários: primeira instalação, idempotência/migração (sem sobrescrever
escolhas), sem internet, falha de API (chave rejeitada), autenticado →
operacional, troca de provedor e recuperação/restart.

Nenhum teste toca a rede: as chaves reais do ambiente do dono são removidas
pelo fixture e qualquer sonda externa é substituída por padrão."""
from __future__ import annotations

import importlib

import pytest

from tests.test_smart_setup import _limpar_ambiente_ia

# O dono pode ter estas variáveis no ambiente de verdade — no modo legacy
# load_cfg herda o ambiente, e um valor real faria o perfil "manter" campos
# que o cenário espera vazios. Nunca imprimir valores — só remover.
_VARS_EXTRA = ("TRADINGAGENTS_MAX_DEBATE_ROUNDS",
               "TRADINGAGENTS_MAX_RISK_ROUNDS")


def _app(tmp_path, monkeypatch):
    _limpar_ambiente_ia(monkeypatch)
    for var in _VARS_EXTRA:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import service.app as m

    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    m._ULTIMO_TESTE.clear()
    from fastapi.testclient import TestClient

    return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}


def _sonda_falsa(monkeypatch, resultado: dict):
    import service.api.ia_provedores as iap

    monkeypatch.setattr(
        iap, "_sondar",
        lambda cfg, entrada, *, transport=None: dict(resultado))


def _sonar_ok(monkeypatch, modelos=None):
    _sonda_falsa(monkeypatch, {
        "ok": True, "estado": "ok", "latencia_ms": 9.0,
        "limites": {}, "modelos": modelos or ["llama-3.1-8b-instant"]})


def _por_id(lista):
    return {p["id"]: p for p in lista}


def _cfg_valores(c, h) -> dict:
    """GET /v1/config devolve {config: {chave: {set, value?, hint?}}} —
    reduz para o valor canônico ('' quando não definido)."""
    dados = c.get("/v1/config", headers=h).json()["config"]
    return {k: (v.get("value", "") if v.get("set") else "")
            for k, v in dados.items() if not k.endswith("_API_KEY")}


class TestPrimeiraInstalacao:
    """Dono novo: baixa o app, abre o painel, aplica um perfil — e só."""

    def test_do_zero_ao_pronto_sem_criar_chave_nem_servico_pago(
            self, tmp_path, monkeypatch):
        _, c, h = _app(tmp_path, monkeypatch)

        e = c.get("/v1/setup/estado", headers=h,
                  params={"perfil": "pronto"}).json()
        assert e["primeira_execucao"] is True
        ids = [p["id"] for p in e["pendencias"]]
        assert "provedor" in ids and "idioma" in ids

        r = c.post("/v1/setup/aplicar", headers=h, json={"perfil": "pronto"})
        assert r.status_code == 200, r.text
        a = r.json()["aplicados"]
        assert a["TRADINGAGENTS_OUTPUT_LANGUAGE"] == "Portuguese"
        assert a["TRADINGAGENTS_DESK_CONTROLE"] == "0"
        assert a["TRADINGAGENTS_MAX_DEBATE_ROUNDS"] == "1"
        # Sem chave colada nenhum provedor é escolhido — e nenhuma chave nasce.
        assert "TRADINGAGENTS_LLM_PROVIDER" not in a

        e = c.get("/v1/setup/estado", headers=h,
                  params={"perfil": "pronto"}).json()
        assert e["primeira_execucao"] is False
        ids = [p["id"] for p in e["pendencias"]]
        assert "idioma" not in ids
        assert "provedor" in ids  # ainda falta a chave — dito com honestidade
        assert e["sugestao"]["provedor"] == ""

    def test_chave_colada_destranca_o_provedor_e_o_resto(
            self, tmp_path, monkeypatch):
        _, c, h = _app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=h, json={"vars": {
            "GROQ_API_KEY": "k-groq-secreto"}})
        assert r.status_code == 200, r.text

        r = c.post("/v1/setup/aplicar", headers=h, json={"perfil": "pronto"})
        a = r.json()["aplicados"]
        assert a["TRADINGAGENTS_LLM_PROVIDER"] == "groq"
        assert a["TRADINGAGENTS_QUICK_THINK_LLM"]  # padrão do catálogo

        e = c.get("/v1/setup/estado", headers=h).json()
        assert e["pendencias"] == []
        assert "k-groq-secreto" not in str(e)


class TestIdempotenciaEMigracao:
    """Aplicar de novo não muda nada; escolha do dono nunca é sobrescrita."""

    def test_aplicar_duas_vezes_e_estavel(self, tmp_path, monkeypatch):
        _, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k"}})

        r1 = c.post("/v1/setup/aplicar", headers=h,
                    json={"perfil": "economico"})
        cfg1 = _cfg_valores(c, h)
        r2 = c.post("/v1/setup/aplicar", headers=h,
                    json={"perfil": "economico"})
        cfg2 = _cfg_valores(c, h)

        assert r1.json()["aplicados"]  # primeira vez preenche
        assert r2.json()["aplicados"] == {}  # segunda vez não mexe em nada
        assert cfg1 == cfg2

    def test_perfil_nao_sobrescreve_preferencia_do_dono(
            self, tmp_path, monkeypatch):
        _, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "TRADINGAGENTS_MAX_DEBATE_ROUNDS": "3",
            "TRADINGAGENTS_OUTPUT_LANGUAGE": "English"}})

        r = c.post("/v1/setup/aplicar", headers=h, json={"perfil": "avancado"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "TRADINGAGENTS_MAX_DEBATE_ROUNDS" in d["mantidos"]
        assert "TRADINGAGENTS_OUTPUT_LANGUAGE" in d["mantidos"]
        assert "TRADINGAGENTS_MAX_DEBATE_ROUNDS" not in d["aplicados"]
        cfg = _cfg_valores(c, h)
        assert cfg["TRADINGAGENTS_MAX_DEBATE_ROUNDS"] == "3"
        assert cfg["TRADINGAGENTS_OUTPUT_LANGUAGE"] == "English"

    def test_migracao_de_cfg_antigo_nao_quebra(self, tmp_path, monkeypatch):
        """Config legado (só chaves antigas) abre sem erro e recebe o que
        falta — sem perder o que já tinha."""
        _, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "GROQ_API_KEY": "k-velha",
            "TRADINGAGENTS_LLM_PROVIDER": "groq"}})
        r = c.post("/v1/setup/aplicar", headers=h, json={"perfil": "pronto"})
        assert r.status_code == 200
        d = r.json()
        assert "TRADINGAGENTS_LLM_PROVIDER" in d["mantidos"]  # preservado
        assert d["aplicados"]["TRADINGAGENTS_OUTPUT_LANGUAGE"] == "Portuguese"


class TestSemInternetEFalhaDeAPI:
    """Falha honesta: sem rede e chave rejeitada são estados DIFERENTES."""

    def test_estado_nao_sai_a_rede(self, tmp_path, monkeypatch):
        """GET /v1/setup/estado é puro: qualquer sonda aqui é bug."""
        _, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k"}})
        import service.api.ia_provedores as iap

        def bomba(*a, **kw):
            raise AssertionError("estado não pode tocar a rede")

        monkeypatch.setattr(iap, "_sondar", bomba)
        for perfil in ("pronto", "economico", "equilibrado", "avancado"):
            r = c.get("/v1/setup/estado", headers=h, params={"perfil": perfil})
            assert r.status_code == 200, r.text

    def test_sem_conexao_fica_explicito_no_diagnostico(
            self, tmp_path, monkeypatch):
        _, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k"}})
        _sonda_falsa(monkeypatch, {
            "ok": False, "estado": "sem_conexao",
            "detalhe": "sem conexão com a internet", "latencia_ms": None,
            "limites": {}, "modelos": []})

        r = c.post("/v1/ai/testar", headers=h, json={"provedor": "groq"})
        assert r.json()["estado"] == "sem_conexao"

        d = _por_id(c.get("/v1/setup/diagnostico",
                          headers=h).json()["provedores"])
        assert d["groq"]["configurado"] is True
        assert d["groq"]["conectado"] is False
        assert d["groq"]["autenticado"] is False

    def test_chave_rejeitada_distingue_conectado_de_autenticado(
            self, tmp_path, monkeypatch):
        _, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "GROQ_API_KEY": "k-ruim"}})
        _sonda_falsa(monkeypatch, {
            "ok": False, "estado": "chave_rejeitada",
            "detalhe": "a API recusou a chave (HTTP 401)", "latencia_ms": 30.0,
            "limites": {}, "modelos": []})

        c.post("/v1/ai/testar", headers=h, json={"provedor": "groq"})
        d = _por_id(c.get("/v1/setup/diagnostico",
                          headers=h).json()["provedores"])
        assert d["groq"]["conectado"] is True   # o canal funciona...
        assert d["groq"]["autenticado"] is False  # ...mas a chave não vale

    def test_autenticado_so_vira_operacional_com_uso_registrado(
            self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k"}})
        _sonar_ok(monkeypatch)

        c.post("/v1/ai/testar", headers=h, json={"provedor": "groq"})
        d = _por_id(c.get("/v1/setup/diagnostico",
                          headers=h).json()["provedores"])
        assert d["groq"]["autenticado"] is True
        assert d["groq"]["operacional"] is False  # nunca chamou, ainda

        m.IA_USO.registrar("legacy", {
            "provedor": "groq", "modelo": "llama-3.1-8b-instant",
            "tokens": {"entrada": 100, "saida": 50}})
        d = _por_id(c.get("/v1/setup/diagnostico",
                          headers=h).json()["provedores"])
        assert d["groq"]["operacional"] is True


class TestTrocaDeProvedor:
    def test_trocar_principal_reordena_fallback_sem_perder_chaves(
            self, tmp_path, monkeypatch):
        _, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {
            "GROQ_API_KEY": "k-groq", "OPENROUTER_API_KEY": "k-or",
            "TRADINGAGENTS_LLM_PROVIDER": "groq"}})
        _sonar_ok(monkeypatch)

        e = c.get("/v1/setup/estado", headers=h).json()
        assert e["sugestao"]["provedor"] == "groq"
        assert e["sugestao"]["fallback"] == "openrouter"

        c.post("/v1/config", headers=h, json={"vars": {
            "TRADINGAGENTS_LLM_PROVIDER": "openrouter"}})
        e = c.get("/v1/setup/estado", headers=h).json()
        assert e["sugestao"]["provedor"] == "openrouter"
        assert e["sugestao"]["fallback"] == "groq"

        d = _por_id(c.get("/v1/setup/diagnostico",
                          headers=h).json()["provedores"])
        assert d["groq"]["tem_chave"] is True
        assert d["openrouter"]["tem_chave"] is True


class TestRecuperacao:
    """Reiniciar o app não perde configuração nem mente sobre estado."""

    def test_restart_preserva_config_e_zera_observacao_de_sessao(
            self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k"}})
        _sonar_ok(monkeypatch)
        c.post("/v1/ai/testar", headers=h, json={"provedor": "groq"})
        c.post("/v1/setup/aplicar", headers=h, json={"perfil": "pronto"})
        antes = _cfg_valores(c, h)

        m, c, h = _app(tmp_path, monkeypatch)  # "reinicia" o processo
        depois = _cfg_valores(c, h)
        assert depois == antes

        e = c.get("/v1/setup/estado", headers=h).json()
        assert e["primeira_execucao"] is False
        d = _por_id(c.get("/v1/setup/diagnostico",
                          headers=h).json()["provedores"])
        # _ULTIMO_TESTE é observação da sessão — some no restart, com verdade.
        assert d["groq"]["autenticado"] is False

    def test_cfg_corrompido_nao_derruba_o_app(self, tmp_path, monkeypatch):
        """config.json ilegível após crash: o app abre, o painel informa —
        e o dono consegue configurar de novo (sem tela branca)."""
        m, c, h = _app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h, json={"vars": {"GROQ_API_KEY": "k"}})
        m.CFG_PATH.write_text("{lixo", encoding="utf-8")

        r = c.get("/v1/setup/estado", headers=h)
        assert r.status_code == 200, r.text
        r = c.post("/v1/setup/aplicar", headers=h, json={"perfil": "pronto"})
        assert r.status_code == 200, r.text


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
