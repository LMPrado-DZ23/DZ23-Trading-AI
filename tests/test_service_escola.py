"""Rotas da Escola Central, salas, conhecimento e pilotos no serviço.

Contrato de segurança da superfície: tudo exige auth; sala LIVE é recusada
com o motivo do fluxo de piloto; certificação não atravessa contexto; o
controle aleatório entra só por opção explícita do dono.
"""
import importlib
from decimal import Decimal


def _load_app(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import service.app as m
    importlib.reload(m)
    return m


def _client(tmp_path, monkeypatch, **env):
    from fastapi.testclient import TestClient
    m = _load_app(tmp_path, monkeypatch, **env)
    m._FAILS.clear()
    m._DESKS.clear()
    return TestClient(m.app), m


H = {"Authorization": "Bearer s3nh4"}


def _abrir(c, corretora="binance", conta="paper-1", mercado="spot",
           modo="paper", payout=None):
    body = {"corretora": corretora, "conta": conta, "mercado": mercado,
            "modo": modo}
    if payout is not None:
        body["payout"] = payout
    return c.post("/v1/escola/salas", headers=H, json=body)


class TestRotasRegistradas:
    def test_paths_exists(self, tmp_path, monkeypatch):
        m = _load_app(tmp_path, monkeypatch)
        paths = {r.path for r in m.app.routes if getattr(r, "path", None)}
        assert {"/v1/escola/central", "/v1/escola/salas", "/v1/escola/salas/detalhe",
                "/v1/escola/salas/pacote", "/v1/escola/salas/conhecimento",
                "/v1/escola/certificacao", "/v1/escola/maestro",
                "/v1/escola/maestro/{acao}", "/v1/escola/pilotos",
                "/v1/escola/pilotos/solicitar",
                "/v1/escola/pilotos/{agent_id}/autorizar",
                "/v1/escola/pilotos/{agent_id}/revogar"} <= paths

    def test_tudo_exige_auth(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        assert c.get("/v1/escola/central").status_code == 401
        assert c.get("/v1/escola/salas").status_code == 401
        assert c.get("/v1/escola/maestro").status_code == 401
        assert c.get("/v1/escola/pilotos").status_code == 401
        assert c.post("/v1/escola/salas", json={}).status_code == 401


class TestEscolaCentralEBandeja:
    def test_central_vazia_e_honesta(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/escola/central", headers=H).json()
        assert d["total_contextos"] == 0 and d["certificados"] == []
        assert "proprietário" in d["aviso"]

    def test_abrir_sala_idempotente_e_isolada(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = _abrir(c)
        assert r.status_code == 200 and r.json()["ok"] is True
        sala = r.json()["sala"]
        assert sala["chave"] == "binance|paper-1|spot|paper"
        assert sala["saldo_realizado"] == "0"
        assert sala["laboratorio"] is False
        assert _abrir(c).status_code == 200          # idempotente
        salas = c.get("/v1/escola/salas", headers=H).json()
        assert [s["chave"] for s in salas["salas"]] == ["binance|paper-1|spot|paper"]
        assert {c_["nome"] for c_ in salas["corretoras"]} >= {"binance", "okx",
                                                             "iq_option"}

    def test_duas_corretoras_duas_salas_zeradas(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        _abrir(c, corretora="binance", conta="paper-1")
        _abrir(c, corretora="okx", conta="paper-2")
        salas = c.get("/v1/escola/salas", headers=H).json()["salas"]
        assert len(salas) == 2
        assert all(s["saldo_realizado"] == "0" for s in salas)

    def test_recusas_com_motivo(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        assert "desconhecido" in _abrir(c, mercado="cripto").json()["detail"]
        assert "laboratório" in _abrir(
            c, corretora="iq_option", mercado="opcoes_binarias",
            modo="shadow", payout="0.85").json()["detail"]
        assert "piloto" in _abrir(c, modo="live").json()["detail"]
        assert "payout" in _abrir(
            c, corretora="iq_option", mercado="opcoes_binarias").json()["detail"]
        assert "conta" in _abrir(c, conta="").json()["detail"]

    def test_sala_inexistente_404(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.get("/v1/escola/salas/detalhe",
                  headers=H, params={"chave": "binance|x|spot|paper"})
        assert r.status_code == 404


class TestConhecimento:
    def test_pacote_sem_certificacao_e_adocao(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        _abrir(c, corretora="binance", conta="paper-1")
        _abrir(c, corretora="okx", conta="paper-2")
        pacote = c.get("/v1/escola/salas/pacote", headers=H,
                       params={"chave": "binance|paper-1|spot|paper"}).json()
        assert pacote["schema"] == "dz23.conhecimento/1"
        assert "estado" not in pacote and "formado_em" not in pacote
        r = c.post("/v1/escola/salas/conhecimento", headers=H,
                   json={"chave": "okx|paper-2|spot|paper", "pacote": pacote})
        assert r.status_code == 200 and r.json()["ok"] is True
        det = c.get("/v1/escola/salas/detalhe", headers=H,
                    params={"chave": "okx|paper-2|spot|paper"}).json()
        assert len(det["priors"]) == 1
        assert det["sala"]["priors_conhecimento"] == 1

    def test_eco_da_propria_sala_recusado(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        _abrir(c)
        pacote = c.get("/v1/escola/salas/pacote", headers=H,
                       params={"chave": "binance|paper-1|spot|paper"}).json()
        r = c.post("/v1/escola/salas/conhecimento", headers=H,
                   json={"chave": "binance|paper-1|spot|paper", "pacote": pacote})
        assert r.status_code == 409 and "eco" in r.json()["detail"]

    def test_certificacao_escondida_no_pacote_recusada(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        _abrir(c, corretora="okx", conta="paper-2")
        for contaminado in (
            {"origem": "binance|paper-1|spot|paper", "corretora": "binance",
             "mercado": "spot", "gerado_em": "2026-10-13T00:00:00+00:00",
             "estatisticas": [], "estado": "formada"},
            {"origem": "binance|paper-1|spot|paper", "corretora": "binance",
             "mercado": "spot", "gerado_em": "2026-10-13T00:00:00+00:00",
             "estatisticas": [{"estrategia": "x", "eventos": ["contrabando"]}]},
        ):
            r = c.post("/v1/escola/salas/conhecimento", headers=H,
                       json={"chave": "okx|paper-2|spot|paper",
                             "pacote": contaminado})
            assert r.status_code == 409
            assert "certificação" in r.json()["detail"]


class TestMaestroRotas:
    def test_pausa_exige_motivo_e_persiste(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        assert c.post("/v1/escola/maestro/pausar", headers=H,
                      json={}).status_code == 422
        r = c.post("/v1/escola/maestro/pausar", headers=H,
                   json={"motivo": "manutenção no conector"})
        assert r.status_code == 200 and r.json()["pausa_global"]["ativo"] is True
        g = c.get("/v1/escola/maestro", headers=H).json()
        assert "manutenção" in g["pausa_global"]["motivo"]
        assert c.post("/v1/escola/maestro/retomar", headers=H,
                      json={"motivo": "conector estável"}).status_code == 200
        assert c.get("/v1/escola/maestro", headers=H).json(
        )["pausa_global"]["ativo"] is False

    def test_acao_invalida_422(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        assert c.post("/v1/escola/maestro/reiniciar", headers=H,
                      json={"motivo": "x"}).status_code == 422


class TestCertificacaoEPilotos:
    def test_sem_evidencia_nao_finge_formacao(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        _abrir(c)
        d = c.get("/v1/escola/certificacao", headers=H,
                  params={"estrategia": "trend_following", "corretora": "binance",
                          "mercado": "spot", "ativo": "BTC/USDT"}).json()
        assert d["elegivel"] is False and d["estado"] == "coletando"
        assert "nenhuma evidência" in d["motivo"]

    def test_laboratorio_jamais_elegivel(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/escola/certificacao", headers=H,
                  params={"estrategia": "trend_following",
                          "corretora": "iq_option",
                          "mercado": "opcoes_binarias",
                          "ativo": "EUR/USD"}).json()
        assert d["elegivel"] is False and "laboratório" in d["motivo"]

    def test_pilotos_vazio_e_cadeia_guardada(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/escola/pilotos", headers=H).json()
        assert d["modo"] == "paper (simulado)"
        assert d["autorizacoes"] == []
        assert set(d["gates_esperados"]) == {"segurança", "integração",
                                             "risco", "execução"}
        # pedido sem formado no contexto => recusado com o motivo da escola
        r = c.post("/v1/escola/pilotos/solicitar", headers=H, json={
            "agent_id": "trader-trend", "estrategia": "trend_following",
            "corretora": "binance", "conta": "paper-1", "ativo": "BTC/USDT",
            "capital_maximo": "100", "perda_diaria_maxima": "10",
            "perda_total_maxima": "30", "prazo_dias": 30})
        assert r.status_code == 409 and "não elegível" in r.json()["detail"]
        # autorizar sem pedido registrado => 409
        assert c.post("/v1/escola/pilotos/trader-trend/autorizar",
                      headers=H).status_code == 409
        # revogar sem motivo => 422
        assert c.post("/v1/escola/pilotos/trader-trend/revogar", headers=H,
                      json={}).status_code == 422

    def test_solicitar_exige_numeros(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/escola/pilotos/solicitar", headers=H, json={
            "agent_id": "trader-trend", "estrategia": "trend_following",
            "corretora": "binance", "conta": "paper-1"})
        assert r.status_code == 422 and "capital_maximo" in r.json()["detail"]


class TestControleOptIn:
    def test_controle_desligado_por_padrao(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        st = c.get("/v1/desk/status", headers=H).json()
        ids = [a["id"] for a in st["agents"]]
        assert len(ids) == 10 and "trader-controle" not in ids

    def test_controle_liga_por_configuracao(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch, TRADINGAGENTS_DESK_CONTROLE="1")
        st = c.get("/v1/desk/status", headers=H).json()
        ids = [a["id"] for a in st["agents"]]
        assert len(ids) == 11 and "trader-controle" in ids


def _fechar_posicao(m, instrumento):
    """Saída pelo caminho de produção: OMS fecha na corretora e o livro da
    mesa encerra — é o mesmo par de chamadas que a operação usa."""
    tid = next(iter(m._DESKS))
    desk = m._DESKS[tid]
    pos = desk["orchestrator"].book[instrumento]
    saida = desk["coordinator"].close_position(
        instrument=instrumento, quantity=pos.quantity,
        price=pos.entry_price, cost_basis=pos.cost_basis,
        agent_id=pos.agent_id)
    assert saida["estado"] == "filled", saida
    assert desk["orchestrator"].close_position(
        instrumento, Decimal(str(saida["preco"]))) is not None
    return saida


def _solicitar_piloto(c, agent_id, estrategia, ativo):
    return c.post("/v1/escola/pilotos/solicitar", headers=H, json={
        "agent_id": agent_id, "estrategia": estrategia, "versao": "1",
        "corretora": "binance", "conta": "paper-legacy", "ativo": ativo,
        "horizonte": "1h", "capital_maximo": "100",
        "perda_diaria_maxima": "10", "perda_total_maxima": "30",
        "prazo_dias": 30})


class TestMesaEscolaE2E:
    """A MESA do app integrada à ESCOLA pelo caminho de produção:

    - o ciclo da mesa espelha entradas na sala da escola;
    - a saída realizada move saldo/taxa e vira desfecho na sala;
    - POST /v1/escola/avaliar avança a formação DAQUELA sala, e a
      certificação continua isolada por corretora;
    - o VIGIA corta entrada no ciclo de quem não está autorizado — e
      devolve a entrada quando autorizado; revogado corta de novo.
    """

    CHAVE = "binance|paper-legacy|spot|paper"

    @staticmethod
    def _semear_formada(m, *, estrategia, ativo):
        """Formação pelo caminho real (parcela → prova independente),
        gravada na MESMA sala que a mesa espelha."""
        from datetime import datetime, timedelta, timezone

        from tradingagents.desk.learning import Desfecho
        from tradingagents.desk.salas import EscolaCentral

        tid = next(iter(m._DESKS))
        desk = m._DESKS[tid]
        central = EscolaCentral(armazem=desk["armazem"], tenant_id=tid)
        sala = desk["sala"]
        base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

        def lote(inicio, n):
            for i in range(n):
                sala.registrar_desfecho(Desfecho(
                    estrategia=estrategia, regime="tendencia",
                    instrumento=ativo,
                    retorno=Decimal("0.010" if i % 2 == 0 else "0.012"),
                    fechado_em=inicio + timedelta(minutes=i),
                    versao="1", horizonte="1h"))

        lote(base, 220)
        av = central.avaliar(sala=sala, estrategia=estrategia, ativo=ativo,
                             agora=base + timedelta(days=30))
        assert av.estado.value == "candidata", av.motivo
        lote(base + timedelta(days=31), 300)
        av = central.avaliar(sala=sala, estrategia=estrategia, ativo=ativo,
                             agora=base + timedelta(days=120))
        assert av.estado.value == "formada", av.motivo

    def test_ciclo_espelha_na_sala_e_saida_realiza(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/cycle", headers=H).json()
        assert len(r["ordens"]) == 2

        det = c.get("/v1/escola/salas/detalhe", headers=H,
                    params={"chave": self.CHAVE}).json()
        posicoes = det["sala"]["posicoes_abertas"]
        assert set(posicoes) == {"BTC/USDT", "ETH/USDT"}
        assert posicoes["BTC/USDT"]["agente"] == "trader-momentum"
        assert posicoes["ETH/USDT"]["agente"] == "trader-statistical"

        _fechar_posicao(m, "BTC/USDT")

        det = c.get("/v1/escola/salas/detalhe", headers=H,
                    params={"chave": self.CHAVE}).json()
        sala = det["sala"]
        assert Decimal(sala["saldo_realizado"]) != 0
        assert Decimal(sala["taxas_acumuladas"]) > 0
        assert sala["desfechos"] == 1
        assert set(sala["posicoes_abertas"]) == {"ETH/USDT"}

    def test_avaliacao_avanca_formacao_e_isola_contexto(self, tmp_path,
                                                        monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        assert c.get("/v1/desk/status", headers=H).status_code == 200
        self._semear_formada(m, estrategia="momentum", ativo="BTC/USDT")

        r = c.post("/v1/escola/avaliar", headers=H, json={
            "chave": self.CHAVE, "estrategia": "momentum",
            "ativo": "BTC/USDT"})
        assert r.status_code == 200, r.text
        av = r.json()["avaliacao"]
        assert av["estado"] == "formada" and av["aprovada"] is True
        assert av["amostras"] >= 500

        bin_ = c.get("/v1/escola/certificacao", headers=H, params={
            "estrategia": "momentum", "corretora": "binance",
            "conta": "paper-legacy", "mercado": "spot", "modo": "paper",
            "ativo": "BTC/USDT"}).json()
        assert bin_["elegivel"] is True and bin_["estado"] == "formada"
        okx = c.get("/v1/escola/certificacao", headers=H, params={
            "estrategia": "momentum", "corretora": "okx",
            "conta": "paper-legacy", "mercado": "spot", "modo": "paper",
            "ativo": "BTC/USDT"}).json()
        assert okx["elegivel"] is False and okx["estado"] == "coletando"

        # Outra CONTA da mesma corretora não herda a formação: conta e modo
        # são dimensões de isolamento, como corretora/ativo/horizonte.
        outra = c.get("/v1/escola/certificacao", headers=H, params={
            "estrategia": "momentum", "corretora": "binance",
            "conta": "outra-conta", "mercado": "spot", "modo": "paper",
            "ativo": "BTC/USDT"}).json()
        assert outra["elegivel"] is False and outra["estado"] == "coletando"
        assert "nenhuma evidência" in outra["motivo"]

        assert c.post("/v1/escola/avaliar", headers=H, json={
            "chave": "binance|nao-existe|spot|paper",
            "estrategia": "momentum", "ativo": "BTC/USDT"}).status_code == 404
        assert c.post("/v1/escola/avaliar", headers=H, json={
            "chave": self.CHAVE}).status_code == 422

    def test_piloto_aguardando_corta_entrada_no_ciclo(self, tmp_path,
                                                      monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        assert c.get("/v1/desk/status", headers=H).status_code == 200
        self._semear_formada(m, estrategia="momentum", ativo="BTC/USDT")
        ped = _solicitar_piloto(c, "trader-momentum", "momentum", "BTC/USDT")
        assert ped.status_code == 200, ped.text
        assert ped.json()["autorizacao"]["estado"] == "aguardando_autorizacao"

        ciclo = c.post("/v1/desk/cycle", headers=H).json()
        assert len(ciclo["ordens"]) == 1
        st = c.get("/v1/desk/status", headers=H).json()
        assert [o["instrumento"] for o in st["orders"]] == ["ETH/USDT"]
        assert any("trader-momentum" in a and "BTC/USDT" in a
                   and "Maestro/piloto" in a for a in ciclo["abstencoes"]), \
            ciclo["abstencoes"]

    def test_autorizado_passa_e_revogado_corta_de_novo(self, tmp_path,
                                                       monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        assert c.get("/v1/desk/status", headers=H).status_code == 200
        self._semear_formada(m, estrategia="momentum", ativo="BTC/USDT")
        assert _solicitar_piloto(c, "trader-momentum", "momentum",
                                 "BTC/USDT").status_code == 200

        # Ciclo com o piloto pendente: entrada do momentum cortada (e o
        # pedido real assume a posse desta máquina — gate do ambiente).
        assert len(c.post("/v1/desk/cycle", headers=H).json()["ordens"]) == 1
        _fechar_posicao(m, "ETH/USDT")

        aut = c.post("/v1/escola/pilotos/trader-momentum/autorizar",
                     headers=H)
        assert aut.status_code == 200, aut.text
        assert aut.json()["autorizacao"]["estado"] == "piloto_autorizado"

        ciclo2 = c.post("/v1/desk/cycle", headers=H).json()
        assert len(ciclo2["ordens"]) == 2
        assert not any("trader-momentum" in a and "Maestro/piloto" in a
                       for a in ciclo2["abstencoes"])

        rv = c.post("/v1/escola/pilotos/trader-momentum/revogar", headers=H,
                    json={"motivo": "ensaio de revogação"})
        assert rv.status_code == 200
        tid = next(iter(m._DESKS))
        for instr in list(m._DESKS[tid]["orchestrator"].book):
            _fechar_posicao(m, instr)

        ciclo3 = c.post("/v1/desk/cycle", headers=H).json()
        assert len(ciclo3["ordens"]) == 1
        assert any("trader-momentum" in a
                   and "Maestro/piloto" in a for a in ciclo3["abstencoes"]), \
            ciclo3["abstencoes"]
