"""Contratos do LIVRO VIVO e da FIDELIDADE NO REINÍCIO (defeitos reais
encontrados na homologação de crash do bundle — item 22).

Defeito A (processo vivo): o risco só ganhava a referência econômica do
livro (carteira) no RESTORE — um boot limpo ficava cego: depois dos fills,
`disponivel` voltava ao capital inteiro e `instrumentos_abertos` esquecia as
posições preenchidas (o mesmo dinheiro poderia ser alocado de novo até o
próximo reinício).

Defeito B (pós-reinício): o painel (`/v1/desk/status`.contabilidade) e o
orquestrador continuavam lendo o livro de ABERTURA do boot; o restore troca
`coord.ledger` pelo livro reconstruído dos lançamentos, mas o dicionário da
mesa e o `positions_provider` ficavam presos no objeto antigo — o painel
mostrava o estado de abertura (patrimônio 1000) com o dinheiro já investido.

Contrato: o caixa vem do livro em QUALQUER boot, e reabrir do disco mostra
exatamente o mesmo estado financeiro que o processo vivo mostrava.
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


def test_risco_enxerga_o_livro_vivo_desde_o_primeiro_boot(tmp_path, monkeypatch):
    """Defeito A: um boot limpo precisa ver o caixa REAL após os fills —
    não o capital nominal (medido no bundle: disponível voltava a 1000)."""
    c, _ = _client(tmp_path, monkeypatch)
    antes = c.get("/v1/desk/status", headers=H).json()
    assert Decimal(antes["risk"]["disponivel"]) == Decimal("1000")

    r = c.post("/v1/desk/cycle", headers=H).json()
    assert len(r["ordens"]) == 2

    st = c.get("/v1/desk/status", headers=H).json()
    disp = Decimal(st["risk"]["disponivel"])
    assert Decimal("790") < disp < Decimal("810"), (
        f"disponível cego: {disp} — o risco precisa ler o livro, não o "
        "capital nominal")
    assert set(st["risk"]["instrumentos_abertos"]) == {"BTC/USDT", "ETH/USDT"}, (
        "posições preenchidas sumiram do risco: "
        f'{st["risk"]["instrumentos_abertos"]}')


def test_reinicio_mostra_o_mesmo_estado_financeiro_do_processo_vivo(
        tmp_path, monkeypatch):
    """Defeito B: reabrir do disco não pode exibir o livro de ABERTURA —
    patrimônio, saldos, disponível e exposição têm de bater com o vivo."""
    c, m = _client(tmp_path, monkeypatch)
    r = c.post("/v1/desk/cycle", headers=H).json()
    assert len(r["ordens"]) == 2
    vivo = c.get("/v1/desk/status", headers=H).json()

    # REINÍCIO: o cache em memória morre; a próxima chamada reidrata do disco
    m._DESKS.clear()
    reaberto = c.get("/v1/desk/status", headers=H).json()

    assert reaberto["contabilidade"]["patrimonio"] == \
        vivo["contabilidade"]["patrimonio"], (
        "patrimônio pós-reinício diverge do vivo (livro de abertura no lugar "
        "do reconstruído)")
    assert reaberto["contabilidade"]["saldos"] == vivo["contabilidade"]["saldos"]
    assert reaberto["risk"]["disponivel"] == vivo["risk"]["disponivel"]
    assert set(reaberto["risk"]["instrumentos_abertos"]) == \
        set(vivo["risk"]["instrumentos_abertos"]) == {"BTC/USDT", "ETH/USDT"}
    assert [o["id"] for o in reaberto["orders"]] == \
        [o["id"] for o in vivo["orders"]]


def test_posicoes_sobrevivem_ao_reinicio_sem_entrada_duplicada(
        tmp_path, monkeypatch):
    """Gestão de posições após reinício: um ciclo novo na mesa reaberta não
    compra de novo o que já está no livro (nada de entrada duplicada)."""
    c, m = _client(tmp_path, monkeypatch)
    c.post("/v1/desk/cycle", headers=H)
    m._DESKS.clear()
    reaberto = c.get("/v1/desk/status", headers=H).json()
    assert set(reaberto["risk"]["instrumentos_abertos"]) == {"BTC/USDT",
                                                             "ETH/USDT"}

    ciclo2 = c.post("/v1/desk/cycle", headers=H).json()
    assert ciclo2["ordens"] == [], (
        f"entrada duplicada após reinício: {ciclo2['ordens']}")
    st = c.get("/v1/desk/status", headers=H).json()
    assert len(st["orders"]) == 2
    assert set(st["risk"]["instrumentos_abertos"]) == {"BTC/USDT", "ETH/USDT"}
