import pytest
from tradingagents.desk.notifications import NotificationEngine

def test_notification_format():
    engine = NotificationEngine(telegram_token="test", telegram_chat_id="123")
    msg = engine.format_alert("AGENT_SIGNAL", "BUY BTC/USDT @ 65000")
    assert "[DZ23 ALERTA" in msg
    assert "AGENT_SIGNAL" in msg
