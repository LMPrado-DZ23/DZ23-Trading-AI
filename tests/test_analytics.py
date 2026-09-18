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
