"""Analytics de conversão: conta eventos, sem PII, com allowlist."""
from tradingagents.analytics import ConversionCounter


def test_conta_evento_permitido_por_origem():
    c = ConversionCounter()
    assert c.record("beta_request", "pricing")
    assert c.record("beta_request", "pricing")
    assert c.record("beta_request", "hero")
    s = c.summary()
    assert s["por_evento"]["beta_request"] == 3
    assert s["detalhado"]["beta_request:pricing"] == 2
    assert s["detalhado"]["beta_request:hero"] == 1
    assert s["total"] == 3


def test_evento_fora_da_allowlist_e_ignorado():
    c = ConversionCounter()
    assert c.record("roubar_pii", "x") is False
    assert c.summary()["total"] == 0


def test_origem_e_sanitizada_e_limitada():
    c = ConversionCounter()
    # caracteres suspeitos (possivel PII/HTML) sao removidos
    c.record("cta_click", "user@example.com <script>")
    det = c.summary()["detalhado"]
    chave = next(iter(det))
    assert "@" not in chave and "<" not in chave and " " not in chave


def test_teto_de_chaves_dobra_origens_novas_em_other():
    from tradingagents.analytics import _MAX_CHAVES
    c = ConversionCounter()
    # inunda com origens sempre novas para o MESMO evento
    for i in range(_MAX_CHAVES + 50):
        c.record("page_view", f"o{i}")
    det = c.summary()["detalhado"]
    # memória finita: no máximo o teto + a chave de transbordo 'other'
    assert len(det) <= _MAX_CHAVES + 1
    assert "page_view:other" in det
    # nenhum evento foi perdido na contagem total
    assert c.summary()["por_evento"]["page_view"] == _MAX_CHAVES + 50
