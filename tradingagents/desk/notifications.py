import requests
import logging

logger = logging.getLogger(__name__)

class NotificationEngine:
    def __init__(self, telegram_token: str = None, telegram_chat_id: str = None, discord_webhook_url: str = None):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.discord_webhook_url = discord_webhook_url

    def format_alert(self, topic: str, content: str) -> str:
        return f"🚨 [DZ23 ALERTA - {topic}]\n{content}"

    def send_telegram(self, message: str) -> bool:
        if not self.telegram_token or not self.telegram_chat_id:
            return False
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        try:
            resp = requests.post(url, json={"chat_id": self.telegram_chat_id, "text": message}, timeout=5)
            return resp.status_code == 200
        except Exception as exc:
            logger.error(f"Telegram alert error: {exc}")
            return False

    def send_discord(self, message: str) -> bool:
        if not self.discord_webhook_url:
            return False
        try:
            resp = requests.post(self.discord_webhook_url, json={"content": message}, timeout=5)
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.error(f"Discord alert error: {exc}")
            return False
