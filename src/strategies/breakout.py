import pandas as pd
from .base import BaseStrategy

class BreakoutStrategy(BaseStrategy):
    def check_signal(self):
        # 1. Проверка глобального тренда (HTF)
        htf_trend = self.get_htf_trend()
        if htf_trend == 0: return None

        # 2. Получение данных
        df = self.get_data(limit=100)
        if df.empty: return None

        atr, atr_pct = self.calculate_atr(df)
        # Сила тренда (регрессия)
        slope = self.get_trend_strength(df, window=15)
        current_close = df['close'].iloc[-1]
        
        # 3. Поиск уровней ( window=3 как в твоем коде)
        res_raw, sup_raw = self.find_levels(df, window=3)
        res_levels = self.cluster_levels(res_raw, atr_pct)
        sup_levels = self.cluster_levels(sup_raw, atr_pct)

        # --- ЛОГИКА ПРОБОЯ ПО ТВОИМ ПАРАМЕТРАМ ---

        # А) LONG (Пробой сопротивления)
        if slope > 0.2 and htf_trend == 1:
            for level in res_levels:
                # 4 касания уровня
                touches = self.analyze_touches(df, level, 'resistance', atr_pct)
                if touches >= 4:
                    # Цена рядом с уровнем (0.2%)
                    if self.is_price_near(current_close, level):
                        # Всплеск объема (1.3x)
                        if self.analyze_volume_spike(df, multiplier=1.3):
                            return {
                                'ticker': self.ticker, 'signal': 'long', 'entry': current_close,
                                'sl': current_close - (2 * atr), # Твоя формула стопа
                                'tp': current_close + (3 * atr), # Твоя формула тейка
                                'atr': atr, 'strategy': f'breakout_{self.interval}'
                            }

        # Б) SHORT (Пробой поддержки)
        elif slope < -0.2 and htf_trend == -1:
            for level in sup_levels:
                touches = self.analyze_touches(df, level, 'support', atr_pct)
                if touches >= 4:
                    if self.is_price_near(current_close, level):
                        if self.analyze_volume_spike(df, multiplier=1.3):
                            return {
                                'ticker': self.ticker, 'signal': 'short', 'entry': current_close,
                                'sl': current_close + (2 * atr),
                                'tp': current_close - (3 * atr),
                                'atr': atr, 'strategy': f'breakout_{self.interval}'
                            }

        return None