import asyncio
import os
import sys
import pandas as pd
from datetime import datetime, timedelta, timezone
from loguru import logger

from src.orchestrator import Orchestrator
from .session import BacktestSession
import src.utils.telegram_notify as tg

tg.send_telegram_message = lambda message: None
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>", level="INFO")

async def run_backtest(params=None):
    history_path = "data/history"
    test_db_path = "data/backtest_results.db"
    
    if not os.path.exists(history_path):
        logger.error(f"Папка {history_path} не найдена!")
        return

    # 1. ПОИСК ФАЙЛОВ
    all_files = os.listdir(history_path)
    # Ищем все тикеры, у которых есть ОБА таймфрейма (15 и 60)
    t15 = {f.split('_')[0] for f in all_files if f.endswith('_15.csv')}
    t60 = {f.split('_')[0] for f in all_files if f.endswith('_60.csv')}
    tickers = sorted(list(t15.intersection(t60)))
    
    if not tickers:
        logger.error(f"Не найдено парных файлов (15 и 60 мин) в {history_path}!")
        return
        
    logger.info(f"📊 Загрузка истории для {len(tickers)} монет...")

    history = {}
    history_starts = []
    history_ends = []

    # 2. ЗАГРУЗКА
    for t in tickers:
        for tf in ["15", "60"]:
            path = f"{history_path}/{t}_{tf}.csv"
            try:
                df = pd.read_csv(path)
                
                # Авто-определение колонки времени
                time_col = 'time_ms' if 'time_ms' in df.columns else 'time'
                
                if time_col == 'time_ms':
                    # Заменяем utc=True на .dt.tz_localize(None)
                    df['time'] = pd.to_datetime(df['time_ms'], unit='ms').dt.tz_localize(None)
                else:
                    df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)

                df = df.sort_values('time').reset_index(drop=True)
                
                # Проверка на пустой файл
                if df.empty:
                    continue

                history[f"{t}_{tf}"] = df
                
                if tf == "15":
                    history_starts.append(df['time'].min())
                    history_ends.append(df['time'].max())
            except Exception as e:
                logger.error(f"Ошибка чтения {path}: {e}")

    if not history_starts:
        logger.error("Нет валидных дат в файлах истории!")
        return

    # 3. РАСЧЕТ ДАТ (Исправление NaT)
    data_start = max(history_starts)
    data_end = min(history_ends)
    
    # Убеждаемся, что даты не NaT
    if pd.isna(data_start) or pd.isna(data_end):
        logger.error("Критическая ошибка: Даты начала или конца определены как NaT (Not a Time). Проверь содержимое CSV.")
        return

    # Убеждаемся, что стартовые точки тоже без поясов
    sim_start = (data_start + timedelta(days=10)).replace(tzinfo=None)
    sim_end = data_end.replace(tzinfo=None)

    logger.info(f"⏳ Период теста: {sim_start.date()} -> {sim_end.date()}")

    # 4. ИНИЦИАЛИЗАЦИЯ
    session_mock = BacktestSession(history)
    session_mock.sim_time = sim_start 
    
    bot = Orchestrator(
        session=session_mock, 
        ticker_list=tickers, 
        is_backtest=True, 
        db_path=test_db_path, 
        start_time=sim_start,
        params=params
    )
    
    bot.db.reset_database()
    bot.ws = session_mock 

    # Прогрев индексов
    for key in history:
        idx = history[key]['time'].searchsorted(sim_start, side='left')
        setattr(session_mock, f"_idx_{key}", idx)

    logger.info("🚀 Симуляция запущена...")
    current_time = sim_start
    last_print_date = None
    start_perf = datetime.now()

    try:
        while current_time <= sim_end:
            session_mock.sim_time = current_time
            bot.set_sim_time(current_time)
            
            # Быстрое обновление индексов
            for t in tickers:
                for tf in ["15", "60"]:
                    key = f"{t}_{tf}"
                    if key in history:
                        df = history[key]
                        curr_idx = getattr(session_mock, f"_idx_{key}")
                        while curr_idx < len(df) and df.iloc[curr_idx]['time'] <= current_time:
                            curr_idx += 1
                        setattr(session_mock, f"_idx_{key}", curr_idx)

            bot.update_open_trades_ws()

            if current_time.minute % 15 == 0:
                await bot.run_parallel_scan()
            
            current_time += timedelta(minutes=1)
            
            if current_time.date() != last_print_date:
                elapsed = datetime.now() - start_perf
                logger.info(f"📈 {current_time.date()} | Сделок: {bot.db.get_active_trades_count('live')} | Затрачено: {str(elapsed).split('.')[0]}")
                last_print_date = current_time.date()

    except Exception as e:
        logger.exception(f"💥 Сбой: {e}")

    logger.success(f"🏁 ТЕСТ ЗАВЕРШЕН!")

if __name__ == "__main__":
    asyncio.run(run_backtest())