import asyncio
import os
import time
import sys
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from loguru import logger

from src.orchestrator import Orchestrator
from src.ws_manager import WSManager 

# 1. КОНФИГУРАЦИЯ v9_GoldenRatio
LIVE_PARAMS = {
    'name': 'v9_GoldenRatio',
    'trend_adx': 35,
    'trend_sl': 1.5,
    'trend_tp': 6.0,
    'breakout_vol': 1.5, 
    'pf_min': 1.3,       
    'bounce_sl': 1.5,
    'bounce_tp': 4.5,
    'fakeout_sl': 1.0,
    'fakeout_tp': 2.5
}

load_dotenv()
API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')
USE_TESTNET = os.getenv('USE_TESTNET', 'False') == 'True'

logger.remove() 
logger.add("data/bot_runtime.log", rotation="50 MB", retention="10 days", level="INFO", encoding="utf-8", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")

async def monitoring_task(bot):
    while True:
        try: await asyncio.to_thread(bot.update_open_trades_ws)
        except Exception as e: logger.error(f"Ошибка в мониторинге: {e}")
        await asyncio.sleep(10)

async def scanning_task(bot, ws_manager):
    scan_interval = 60 
    while True:
        try:
            await bot.run_parallel_scan()
            if int(time.time()) % 3600 < scan_interval:
                new_tickers = await asyncio.to_thread(bot.get_market_tickers)
                ws_manager.subscribe_tickers(new_tickers)
        except Exception as e: logger.error(f"Ошибка в сканировании: {e}")
        await asyncio.sleep(scan_interval)

async def main():
    print("🚀 СИСТЕМА ЗАПУЩЕНА. Логи: data/bot_runtime.log")
    try:
        session = HTTP(testnet=USE_TESTNET, api_key=API_KEY, api_secret=API_SECRET, recv_window=10000)
        res = session.get_tickers(category="linear")
        current_tickers = [t['symbol'] for t in res['result']['list'] if t['symbol'].endswith('USDT') and float(t['turnover24h']) > 20_000_000]
        
        ws_manager = WSManager(API_KEY, API_SECRET, USE_TESTNET)
        ws_manager.subscribe_tickers(current_tickers)
        await asyncio.sleep(5)

        bot = Orchestrator(
            session=session, ticker_list=current_tickers, db_path="data/trade_bot.db", 
            is_backtest=False, params=LIVE_PARAMS
        )
        bot.ws = ws_manager
        await asyncio.gather(monitoring_task(bot), scanning_task(bot, ws_manager))
    except Exception as e: logger.critical(f"💥 СБОЙ: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())