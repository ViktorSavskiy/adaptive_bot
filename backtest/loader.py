import pandas as pd
import time
import os
from datetime import datetime, timedelta
from pybit.unified_trading import HTTP
from dotenv import load_dotenv

load_dotenv()

session = HTTP(testnet=False, api_key=os.getenv('BYBIT_API_KEY'), api_secret=os.getenv('BYBIT_API_SECRET'), domain="bytick")

def download_data(symbol, interval, days):
    print(f"📥 Загрузка {symbol} ({interval}m)...")
    target_candles = (days * 24 * 60) // int(interval)
    all_klines = []
    end_time = int(time.time() * 1000)
    
    while len(all_klines) < target_candles:
        try:
            res = session.get_kline(category="linear", symbol=symbol, interval=interval, limit=1000, end=end_time)
            klines = res.get('result', {}).get('list', [])
            if not klines: break
            all_klines.extend(klines)
            end_time = int(klines[-1][0]) - 1
            time.sleep(0.1) # Ускорили паузу
        except Exception as e:
            print(f"Ошибка на {symbol}: {e}")
            break

    df = pd.DataFrame(all_klines, columns=['time_ms', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df = df.iloc[::-1]
    df['time'] = pd.to_datetime(df['time_ms'].astype(float), unit='ms')
    
    os.makedirs("data/history", exist_ok=True)
    df.to_csv(f"data/history/{symbol}_{interval}.csv", index=False)

if __name__ == "__main__":
    # Топ-30 ликвидных монет
    tickers = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT", "MATICUSDT",
        "LTCUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT", "BCHUSDT", "UNIUSDT", "APTUSDT",
        "SUIUSDT", "ARBUSDT", "OPUSDT", "FILUSDT", "TIAUSDT", "RNDRUSDT", "ORDIUSDT",
        "SEIUSDT", "ENAUSDT", "NOTUSDT", "JUPUSDT", "WIFUSDT"
    ]
    
    for t in tickers:
        download_data(t, "15", 365) # 1 год 15м
        download_data(t, "60", 365) # 1 год 1ч
    print("✨ Все данные успешно загружены!")