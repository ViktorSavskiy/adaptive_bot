import time
import os
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from loguru import logger

from src.orchestrator import Orchestrator

# Загрузка ключей из .env
load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
USE_TESTNET = os.getenv('USE_TESTNET', 'False') == 'True'

# Настройка логирования
logger.add("data/bot_log.log", rotation="500 MB", level="INFO")

def main():
    logger.info("🚀 Запуск торгового бота в режиме виртуального тестирования")
    
    # Авторизация в Bybit
    try:
        session = HTTP(
            testnet=USE_TESTNET,
            api_key=API_KEY,
            api_secret=API_SECRET,
        )
        logger.info("✅ Подключение к Bybit установлено")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения: {e}")
        return

    # Изначальный список тикеров (будет обновлен оркестратором)
    initial_tickers = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    # Создаем оркестратор
    bot = Orchestrator(session, initial_tickers)
    
    # Бесконечный цикл
    while True:
        try:
            bot.run_cycle()
        except Exception as e:
            logger.error(f"Критическая ошибка в основном цикле: {e}")
        
        # Пауза между циклами (например, 5 минут)
        # Так как мы работаем на 15-минутных свечах, чаще проверять нет смысла
        logger.info("💤 Спим 5 минут до следующего анализа...")
        time.sleep(300)

if __name__ == "__main__":
    main()