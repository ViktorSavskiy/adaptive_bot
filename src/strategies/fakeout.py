import pandas as pd
from .base import BaseStrategy

class FakeoutStrategy(BaseStrategy):
    def __init__(self, session, ticker, interval, db_manager):
        super().__init__(session, ticker, interval, db_manager)
    def check_signal(self):
        df = self.get_data(limit=100)
        if df.empty: return None

        atr, atr_pct = self.calculate_atr(df)
        current_close = df['close'].iloc[-1]
        high_curr = df['high'].iloc[-1]
        low_curr = df['low'].iloc[-1]
        
        res_raw, sup_raw = self.find_levels(df, window=10)
        valid_res = self.cluster_levels(res_raw, atr_pct)
        valid_sup = self.cluster_levels(sup_raw, atr_pct)

        # ЛОЖНЫЙ ПРОБОЙ СОПРОТИВЛЕНИЯ (Входим в SHORT)
        for level in valid_res:
            # Условие: Тень свечи была выше уровня, а закрытие — под уровнем
            if high_curr > level * 1.002 and current_close < level:
                return {
                    'ticker': self.ticker, 'signal': 'short', 'entry': current_close,
                    'sl': high_curr + (1 * atr), # Стоп за кончик тени
                    'tp': current_close - (3 * atr),
                    'atr': atr, 'strategy': f'fakeout_{self.interval}'
                }

        # ЛОЖНЫЙ ПРОБОЙ ПОДДЕРЖКИ (Входим в LONG)
        for level in valid_sup:
            # Условие: Тень свечи была ниже уровня, а закрытие — выше уровня
            if low_curr < level * 0.998 and current_close > level:
                return {
                    'ticker': self.ticker, 'signal': 'long', 'entry': current_close,
                    'sl': low_curr - (1 * atr),
                    'tp': current_close + (3 * atr),
                    'atr': atr, 'strategy': f'fakeout_{self.interval}'
                }
        return None