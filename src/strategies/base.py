from abc import ABC, abstractmethod
import pandas as pd
import numpy as np
from scipy.stats import linregress
from collections import defaultdict

class BaseStrategy(ABC):
    def __init__(self, session, ticker, interval):
        """
        :param session: Экземпляр pybit.unified_trading.HTTP
        :param ticker: Символ (например, 'BTCUSDT')
        :param interval: Таймфрейм ('15', '60', 'D')
        """
        self.session = session
        self.ticker = ticker
        self.interval = interval

    def get_data(self, limit=100):
        """Получает исторические данные и возвращает чистый DataFrame"""
        try:
            response = self.session.get_kline(
                category="linear",
                symbol=self.ticker,
                interval=self.interval,
                limit=limit
            )
            
            klines = response.get('result', {}).get('list', [])
            if not klines:
                return pd.DataFrame()

            # Bybit возвращает данные от новых к старым, переворачиваем для тех. анализа
            df = pd.DataFrame(klines, columns=[
                'time', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ])
            
            # Преобразование типов
            cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
            df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
            
            return df.iloc[::-1].reset_index(drop=True)
        except Exception as e:
            print(f"Ошибка получения данных для {self.ticker}: {e}")
            return pd.DataFrame()

    def calculate_atr(self, df, period=14):
        """Расчет ATR и ATR в процентах"""
        if len(df) < period + 1:
            return 0.0, 0.0

        high = df['high'].values
        low = df['low'].values
        close = df['close'].values

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )
        
        atr = np.mean(tr[-period:])
        avg_price = df['close'].tail(period).mean()
        atr_pct = (atr / avg_price) * 100
        
        return float(atr), float(atr_pct)

    def get_trend_strength(self, df, window=15):
        """Определяет наклон тренда через линейную регрессию логарифма цены"""
        if len(df) < window:
            return 0.0
            
        prices = df['close'].tail(window).values
        log_prices = np.log(prices)
        slope, _, _, _, _ = linregress(np.arange(len(log_prices)), log_prices)
        
        return slope * 100  # Возвращаем процентное изменение

    def find_levels(self, df, window=5):
        """Поиск локальных максимумов (сопротивление) и минимумов (поддержка)"""
        df['local_max'] = df['high'].rolling(window=window, center=True).max()
        df['local_min'] = df['low'].rolling(window=window, center=True).min()

        res_levels = df[df['high'] == df['local_max']]['high'].unique().tolist()
        sup_levels = df[df['low'] == df['local_min']]['low'].unique().tolist()

        return res_levels, sup_levels

    def cluster_levels(self, levels, atr_pct):
        """Кластеризация уровней на основе ATR для фильтрации шума"""
        if not levels:
            return []
            
        threshold = atr_pct / 100
        clusters = defaultdict(list)
        
        for level in sorted(levels):
            found = False
            for cluster_key in clusters:
                if abs(level - cluster_key) < threshold * cluster_key:
                    clusters[cluster_key].append(level)
                    found = True
                    break
            if not found:
                clusters[level].append(level)
        
        return [np.mean(v) for v in clusters.values()]

    @abstractmethod
    def check_signal(self):
        """
        Метод должен быть реализован в каждой стратегии.
        Должен возвращать словарь:
        {
            'signal': 'long' | 'short' | None,
            'entry': float,
            'sl': float,
            'tp': float
        }
        """
        pass