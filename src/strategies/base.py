from abc import ABC, abstractmethod
import pandas as pd
import numpy as np
from scipy.stats import linregress
from collections import defaultdict

class BaseStrategy(ABC):
    def __init__(self, session, ticker, interval, db_manager):
        self.session = session
        self.ticker = ticker
        self.interval = interval
        self.db = db_manager

    def get_data(self, limit=100):
        try:
            response = self.session.get_kline(
                category="linear", symbol=self.ticker, interval=self.interval, limit=limit
            )
            klines = response.get('result', {}).get('list', [])
            if not klines: return pd.DataFrame()
            df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
            return df.iloc[::-1].reset_index(drop=True)
        except Exception as e:
            print(f"Ошибка данных {self.ticker}: {e}")
            return pd.DataFrame()

    def calculate_atr(self, df, period=14):
        if len(df) < period + 1: return 0.0, 0.0
        high, low, close = df['high'].values, df['low'].values, df['close'].values
        tr = np.maximum(high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
        atr = np.mean(tr[-period:])
        atr_pct = (atr / df['close'].iloc[-1]) * 100
        return float(atr), float(atr_pct)

    def get_trend_strength(self, df, window=15):
        if len(df) < window: return 0.0
        prices = np.log(df['close'].tail(window).values)
        slope, _, _, _, _ = linregress(np.arange(len(prices)), prices)
        return slope * 100

    def find_levels(self, df, window=10):
        if len(df) < window * 2: return [], []
        df['is_max'] = df['high'] == df['high'].rolling(window=window*2+1, center=True).max()
        df['is_min'] = df['low'] == df['low'].rolling(window=window*2+1, center=True).min()
        return df[df['is_max'] == True]['high'].tolist(), df[df['is_min'] == True]['low'].tolist()

    def cluster_levels(self, levels, atr_pct):
        if not levels: return []
        threshold = atr_pct / 100
        clusters = defaultdict(list)
        for level in sorted(levels):
            found = False
            for cluster_key in clusters:
                if abs(level - cluster_key) < threshold * cluster_key:
                    clusters[cluster_key].append(level)
                    found = True
                    break
            if not found: clusters[level].append(level)
        return [np.mean(v) for v in clusters.values()]

    def get_htf_trend(self):
        """
        Определяет глобальный тренд на старшем таймфрейме (HTF).
        15м -> смотрим 1ч (60).
        60м -> смотрим 4ч (240).
        """
        htf_interval = "60" if self.interval == "15" else "240"
        
        try:
            res = self.session.get_kline(
                category="linear", 
                symbol=self.ticker, 
                interval=htf_interval, 
                limit=210
            )
            
            klines = res.get('result', {}).get('list', [])
            if len(klines) < 200: 
                return 0

            # Создаем DataFrame с именованными колонками для надежности
            df_htf = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'turnover'])
            
            # Преобразуем цены закрытия (индекс 4 в списке Bybit) в числа
            close_prices = pd.to_numeric(df_htf['close']).iloc[::-1] # Переворот в хронологию

            # Расчет EMA 200
            ema200_series = close_prices.ewm(span=200, adjust=False).mean()
            last_ema = ema200_series.iloc[-1]
            current_price = close_prices.iloc[-1]

            return 1 if current_price > last_ema else -1
            
        except Exception as e:
            # Если биржа не ответила или данные битые — возвращаем 0 (нейтрально)
            # logger.warning(f"Ошибка HTF тренда для {self.ticker}: {e}")
            return 0

    # --- НОВЫЕ МЕТОДЫ ДЛЯ ТВОЕЙ ЛОГИКИ ПРОБОЯ ---

    def analyze_touches(self, df, level, level_type, atr_pct):
        """Считает количество касаний уровня (твоя логика)"""
        threshold = level * (atr_pct / 100) * 0.5 # Зона касания 50% от ATR
        if level_type == 'resistance':
            touches = df[(df['high'] >= level - threshold) & (df['high'] <= level + threshold)]
        else:
            touches = df[(df['low'] >= level - threshold) & (df['low'] <= level + threshold)]
        return len(touches)

    def is_price_near(self, current_p, level_p, tolerance=0.002):
        """Проверка близости цены к уровню (0.2%)"""
        return level_p * (1 - tolerance) <= current_p <= level_p * (1 + tolerance)

    def analyze_volume_spike(self, df, multiplier=1.3):
        """Проверка всплеска объема"""
        avg_vol = df['volume'].tail(20).mean()
        return df['volume'].iloc[-1] > avg_vol * multiplier

    def check_level_quality(self, df, level, level_type, atr_pct):
        """Для отскока: проверка что уровень не 'прошит' телами"""
        tolerance = level * (atr_pct / 100) * 0.2
        touches, breaks = 0, 0
        for i in range(len(df)):
            high, low = df['high'].iloc[i], df['low'].iloc[i]
            b_max, b_min = max(df['open'].iloc[i], df['close'].iloc[i]), min(df['open'].iloc[i], df['close'].iloc[i])
            if level_type == 'resistance':
                if abs(high - level) <= tolerance: touches += 1
                if b_max > level + tolerance: breaks += 1
            else:
                if abs(low - level) <= tolerance: touches += 1
                if b_min < level - tolerance: breaks += 1
        return touches >= 2 and (breaks == 0 or (touches / breaks) >= 3)

    @abstractmethod
    def check_signal(self):
        pass