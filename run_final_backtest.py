import asyncio
from backtest.engine import run_backtest
from loguru import logger

# Тот самый конфиг v9_GoldenRatio
GOLDEN_PARAMS = {
    'name': 'v9_GoldenRatio',
    'trend_adx': 35,
    'trend_sl': 1.5,
    'trend_tp': 6.0,
    'breakout_vol': 2.0,
    'pf_min': 1.3,
    'bounce_sl': 1.5,
    'bounce_tp': 4.5,
    'fakeout_sl': 1.0,
    'fakeout_tp': 2.5
}

async def main():
    logger.info("🚀 ЗАПУСК ФИНАЛЬНОГО ГОДОВОГО ТЕСТА (30 МОНЕТ)...")
    await run_backtest(params=GOLDEN_PARAMS)
    logger.success("🏁 ТЕСТ ЗАВЕРШЕН. Теперь запусти 'python analyze_final.py'")

if __name__ == "__main__":
    asyncio.run(main())