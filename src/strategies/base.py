from abc import ABC, abstractmethod
import pandas as pd
import numpy as np
import time
from loguru import logger

class BaseStrategy(ABC):
    # Статический кэш для предотвращения повторных расчетов внутри одного цикла сканирования
    _analysis_cache = {}
    _trend_cache = {} 
    def __init__(self, session, ticker, interval, db_manager, is_backtest=False, params=None):
        self.session = session
        self.ticker = ticker
        self.interval = interval
        self.db = db_manager
        self.is_backtest = is_backtest
        self.params = params or {}

    def get_data(self, limit=200):
        """
        Получение данных. В Live-режиме всегда отрезает текущую незакрытую свечу
        для полной синхронизации с логикой бэктеста.
        """
        # Ключ кэша: (тикер, интервал, метка времени)
        # В Live кэш живет 1 минуту
        now_mark = self.session.sim_time if self.is_backtest else int(time.time() // 60)
        cache_key = (self.ticker, self.interval, now_mark)

        if cache_key in BaseStrategy._analysis_cache:
            return BaseStrategy._analysis_cache[cache_key]

        try:
            # В Live запрашиваем на 1 свечу больше, чтобы отбросить "живую"
            fetch_limit = limit + 1 if not self.is_backtest else limit
            
            response = self.session.get_kline(
                category="linear", symbol=self.ticker, interval=self.interval, limit=fetch_limit
            )
            klines = response.get('result', {}).get('list', [])
            if not klines:
                return pd.DataFrame()

            # Формируем DataFrame
            df = pd.DataFrame(klines, columns=['time_ms', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            
            # Быстрое приведение к числам
            num_cols = ['open', 'high', 'low', 'close', 'volume']
            df[num_cols] = df[num_cols].astype(float)
            
            # Переворачиваем в хронологию
            df = df.iloc[::-1].reset_index(drop=True)

            # ВАЖНО: В Live отсекаем последнюю (текущую) свечу
            if not self.is_backtest:
                df = df.iloc[:-1].reset_index(drop=True)

            # Сохраняем в кэш
            BaseStrategy._analysis_cache[cache_key] = df
            
            # Очистка старого кэша при раздувании
            if len(BaseStrategy._analysis_cache) > 500:
                BaseStrategy._analysis_cache.clear()

            return df
        except Exception as e:
            logger.error(f"Ошибка получения данных для {self.ticker}: {e}")
            return pd.DataFrame()

    def calculate_atr(self, df, period=14):
        """Скоростной расчет ATR через NumPy"""
        if len(df) < period + 1:
            return 0.0, 0.0
        
        high = df['high'].values
        low = df['low'].values
        prev_close = df['close'].shift(1).values

        tr = np.maximum(high - low, 
                np.maximum(abs(high - prev_close), 
                abs(low - prev_close)))
        
        atr = np.nanmean(tr[-period:])
        atr_pct = (atr / df['close'].iloc[-1]) * 100
        return float(atr), float(atr_pct)

    def find_levels(self, df, window=7):
        """Поиск фрактальных уровней ( window=7 оптимально для 15м/60м )"""
        if len(df) < window * 2 + 1:
            return [], []
        
        highs = df['high'].values
        lows = df['low'].values
        res_levels = []
        sup_levels = []

        for i in range(window, len(df) - window):
            # Сопротивление
            if all(highs[i] >= highs[i-window:i]) and all(highs[i] > highs[i+1:i+window+1]):
                res_levels.append(highs[i])
            # Поддержка
            if all(lows[i] <= lows[i-window:i]) and all(lows[i] < lows[i+1:i+window+1]):
                sup_levels.append(lows[i])
        
        return res_levels, sup_levels

    def cluster_levels(self, levels, atr_pct):
        """Объединение близких уровней"""
        if not levels: return []
        threshold = (atr_pct / 100) * 0.7 
        sorted_lvls = sorted(levels)
        clusters = []
        if not sorted_lvls: return []
        
        current_cluster = [sorted_lvls[0]]
        for i in range(1, len(sorted_lvls)):
            avg = np.mean(current_cluster)
            if (sorted_lvls[i] - avg) / avg < threshold:
                current_cluster.append(sorted_lvls[i])
            else:
                clusters.append(np.mean(current_cluster))
                current_cluster = [sorted_lvls[i]]
        clusters.append(np.mean(current_cluster))
        return clusters

    def analyze_volume_spike(self, df, multiplier=1.3):
        """Проверка всплеска объема относительно среднего"""
        if len(df) < 21: return False
        avg_vol = df['volume'].iloc[-21:-1].mean()
        return df['volume'].iloc[-1] > (avg_vol * multiplier)

    def get_htf_trend(self):
        """Определение тренда с кэшированием на 10 минут"""
        now_ts = time.time()
        cache_key = (self.ticker, self.interval)
        
        # Если в кэше есть свежий тренд (младше 10 минут) - берем его
        if not self.is_backtest and cache_key in BaseStrategy._trend_cache:
            ts, val = BaseStrategy._trend_cache[cache_key]
            if now_ts - ts < 600: # 600 секунд = 10 минут
                return val

        # Если нет - делаем запрос (твой старый код)
        htf_interval = "60" if self.interval == "15" else "240"
        old_interval = self.interval
        self.interval = htf_interval
        df_htf = self.get_data(limit=250)
        self.interval = old_interval
        
        res = 0
        if not df_htf.empty and len(df_htf) >= 200:
            ema200 = df_htf['close'].ewm(span=200, adjust=False).mean().iloc[-1]
            current = df_htf['close'].iloc[-1]
            if current > ema200 * 1.0002: res = 1
            elif current < ema200 * 0.9998: res = -1

        # Сохраняем в кэш
        if not self.is_backtest:
            BaseStrategy._trend_cache[cache_key] = (now_ts, res)
        
        return res

    def check_level_quality(self, df, level, level_type, atr_pct):
        """Проверка надежности уровня по всей истории DataFrame"""
        zone = level * (atr_pct / 100) * 0.4
        touches = 0
        violations = 0 
        
        for i in range(len(df)):
            low, high = df['low'].iloc[i], df['high'].iloc[i]
            body_max = max(df['open'].iloc[i], df['close'].iloc[i])
            body_min = min(df['open'].iloc[i], df['close'].iloc[i])
            
            if level_type == 'resistance':
                if high >= level - zone and high <= level + zone: touches += 1
                if body_max > level + zone: violations += 1
            else:
                if low >= level - zone and low <= level + zone: touches += 1
                if body_min < level - zone: violations += 1
        
        # Уровень годен, если было хоть одно подтверждающее касание
        return touches >= 1 and (violations <= touches)

    @abstractmethod
    def check_signal(self):
        pass