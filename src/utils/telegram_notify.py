import requests
import os
from loguru import logger

def send_telegram_message(message):
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not token or not chat_id:
        logger.error("Telegram Error: Token или Chat ID не найдены в .env")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(url, json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'})
        if response.status_code != 200:
            logger.error(f"Telegram API Error: {response.text}")
        else:
            logger.info("Telegram: Уведомление отправлено успешно")
    except Exception as e:
        logger.error(f"Telegram Connection Error: {e}")