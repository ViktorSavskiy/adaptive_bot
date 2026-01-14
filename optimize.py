import asyncio
import pandas as pd
import sqlite3
import os
from loguru import logger
from backtest.engine import run_backtest

# ГРИД ИЗ 10 ВАРИАЦИЙ
SEARCH_GRID = [
    # 1. Консервативный (Текущий улучшенный)
    {'name': 'v1_Conservative', 'trend_adx': 35, 'trend_sl': 2.0, 'trend_tp': 4.0, 'breakout_vol': 2.0, 'pf_min': 1.2, 'bounce_sl': 1.5, 'bounce_tp': 3.0},
    
    # 2. Трендоловов (Длинные тейки)
    {'name': 'v2_TrendFollower', 'trend_adx': 30, 'trend_sl': 2.0, 'trend_tp': 6.0, 'breakout_vol': 1.8, 'pf_min': 1.2, 'bounce_sl': 2.0, 'bounce_tp': 4.0},
    
    # 3. Снайпер (Жесткие фильтры)
    {'name': 'v3_Sniper', 'trend_adx': 45, 'trend_sl': 1.5, 'trend_tp': 5.0, 'breakout_vol': 3.0, 'pf_min': 1.5, 'bounce_sl': 1.0, 'bounce_tp': 3.0},
    
    # 4. Скальпер (Короткие стопы и тейки)
    {'name': 'v4_Scalper', 'trend_adx': 25, 'trend_sl': 1.0, 'trend_tp': 2.5, 'breakout_vol': 1.3, 'pf_min': 1.1, 'bounce_sl': 0.8, 'bounce_tp': 2.0},
    
    # 5. Элитный клуб (Высокий порог входа в LIVE)
    {'name': 'v5_EliteOnly', 'trend_adx': 35, 'trend_sl': 2.0, 'trend_tp': 4.5, 'breakout_vol': 2.2, 'pf_min': 1.8, 'bounce_sl': 1.5, 'bounce_tp': 3.5},
    
    # 6. Агрессивный пробойник
    {'name': 'v6_BreakoutMaster', 'trend_adx': 30, 'trend_sl': 2.5, 'trend_tp': 5.0, 'breakout_vol': 1.5, 'pf_min': 1.2, 'bounce_sl': 1.5, 'bounce_tp': 3.0},
    
    # 7. Защитный (Широкие стопы, чтобы не выбивало шумом)
    {'name': 'v7_Protective', 'trend_adx': 35, 'trend_sl': 3.0, 'trend_tp': 6.0, 'breakout_vol': 2.0, 'pf_min': 1.3, 'bounce_sl': 2.5, 'bounce_tp': 5.0},
    
    # 8. Импульсный (Высокий ADX, короткий RR)
    {'name': 'v8_Impulse', 'trend_adx': 40, 'trend_sl': 1.2, 'trend_tp': 3.0, 'breakout_vol': 2.5, 'pf_min': 1.4, 'bounce_sl': 1.2, 'bounce_tp': 2.5},
    
    # 9. Трендовый RR 1:4
    {'name': 'v9_GoldenRatio', 'trend_adx': 35, 'trend_sl': 1.5, 'trend_tp': 6.0, 'breakout_vol': 2.0, 'pf_min': 1.3, 'bounce_sl': 1.5, 'bounce_tp': 4.5},
    
    # 10. Лабораторный (Низкий PF, много сделок)
    {'name': 'v10_HighFreq', 'trend_adx': 20, 'trend_sl': 1.5, 'trend_tp': 3.5, 'breakout_vol': 1.2, 'pf_min': 1.05, 'bounce_sl': 1.0, 'bounce_tp': 3.0},
]

async def start_optimization():
    summary = []
    db_path = "data/backtest_results.db"

    for config in SEARCH_GRID:
        logger.warning(f"\n🚀 >>> ЗАПУСК ТЕСТА [{SEARCH_GRID.index(config)+1}/10]: {config['name']} <<<")
        
        if os.path.exists(db_path):
            try: os.remove(db_path)
            except: pass

        # Запускаем бэктест
        await run_backtest(params=config)
        
        # Анализ результатов
        conn = sqlite3.connect(db_path)
        live_res = conn.execute("SELECT SUM(pnl_usd), COUNT(*) FROM trades WHERE trade_type='live' AND status='closed'").fetchone()
        
        # Считаем прибыльные и убыточные для Profit Factor
        wins = conn.execute("SELECT SUM(pnl_usd) FROM trades WHERE trade_type='live' AND pnl_usd > 0").fetchone()[0] or 0
        losses = abs(conn.execute("SELECT SUM(pnl_usd) FROM trades WHERE trade_type='live' AND pnl_usd < 0").fetchone()[0] or 0)
        
        conn.close()

        pnl = round(live_res[0] or 0, 2)
        count = live_res[1] or 0
        pf = round(wins / losses, 2) if losses > 0 else 10.0
        
        summary.append({
            'Config': config['name'],
            'PnL ($)': pnl,
            'Trades': count,
            'PF': pf,
            'Avg': round(pnl/count, 3) if count > 0 else 0
        })

        # Вывод промежуточного результата, чтобы не ждать конца всех 10 тестов
        logger.success(f"Результат {config['name']}: PnL ${pnl}, PF {pf}")

    # Финальная таблица
    df = pd.DataFrame(summary)
    df = df.sort_values(by='PnL ($)', ascending=False) # Лучшие сверху
    print("\n" + "="*70)
    print("🏆 ИТОГОВАЯ ТАБЛИЦА ОПТИМИЗАЦИИ")
    print("="*70)
    print(df.to_string(index=False))
    print("="*70)
    # ... (после вывода принтом итоговой таблицы)
    
    # 1. Сохраняем в CSV (удобно для Excel)
    df.to_csv("data/optimization_report.csv", index=False)
    logger.success("📊 Отчет сохранен в data/optimization_report.csv")

    # 2. Сохраняем в отдельную базу итогов (чтобы не затерлось)
    report_conn = sqlite3.connect("data/final_optimization_results.db")
    df.to_sql("summary", report_conn, if_exists='replace', index=False)
    report_conn.close()
    logger.success("🗄️ Итоги сохранены в базу data/final_optimization_results.db")
if __name__ == "__main__":
    asyncio.run(start_optimization())