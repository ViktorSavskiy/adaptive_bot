import time
import os
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from loguru import logger

from src.orchestrator import Orchestrator
from src.ws_manager import WSManager  # Не забудь создать этот файл!

# Загрузка ключей
load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
USE_TESTNET = os.getenv('USE_TESTNET', 'False') == 'True'

# Логирование
logger.add("data/bot_log.log", rotation="500 MB", level="INFO")

def main():
    logger.info("🚀 Запуск торгового бота: WS Monitoring + MTF Analysis")
    
    try:
        session = HTTP(
            testnet=USE_TESTNET,
            api_key=API_KEY,
            api_secret=API_SECRET,
            recv_window=60000, # Окно приема (мы уже ставили)
            timeout=30
        )
        logger.info("✅ HTTP Сессия Bybit установлена")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения: {e}")
        return

    # 1. Инициализируем WebSocket Менеджер
    ws_manager = WSManager(API_KEY, API_SECRET, USE_TESTNET)
    
    # 2. Создаем Оркестратор и передаем ему сессию
    initial_tickers = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    bot = Orchestrator(session, initial_tickers)
    
    # ПРИВЯЗЫВАЕМ WS К БОТУ
    bot.ws = ws_manager 
    
    # 3. Подписываемся на тикеры через WS
    # Сначала получаем актуальный список монет с объемами
    current_tickers = bot.get_market_tickers()
    ws_manager.subscribe_tickers(current_tickers)
    logger.info(f"📡 Подписка WS оформлена на {len(current_tickers)} тикеров")

    # Переменная для контроля времени анализа (ставим 0, чтобы первый запуск был сразу)
    last_analysis_time = 0 

    # --- ГЛАВНЫЙ ЦИКЛ ---
    try:
        while True:
            try:
                # 1. БЫСТРАЯ ПРОВЕРКА (каждую секунду)
                # Мониторим открытые сделки на предмет касания SL/TP через WebSocket
                bot.update_open_trades_ws() 
                
                # 2. МЕДЛЕННАЯ ПРОВЕРКА (раз в 5 минут / 300 секунд)
                # Сканируем рынок на новые сигналы через HTTP Klines
                if time.time() - last_analysis_time >= 300:
                    logger.info("🔍 Запуск планового сканирования рынка...")
                    bot.run_cycle()
                    last_analysis_time = time.time()
                    
            except Exception as e:
                logger.error(f"Критическая ошибка в основном цикле: {e}")
                time.sleep(10) # Пауза при ошибке, чтобы не заспамить логи
            
            # Минимальная пауза цикла, чтобы не нагружать процессор
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n⚠️ Получен сигнал прерывания (Ctrl+C). Корректное завершение работы...")
        logger.info("👋 Бот остановлен пользователем")

if __name__ == "__main__":
    main()