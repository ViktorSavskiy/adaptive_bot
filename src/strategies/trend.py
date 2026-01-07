import pandas as pd
import numpy as np
from .base import BaseStrategy

class TrendStrategy(BaseStrategy):
    def __init__(self, session, ticker, interval, db_manager):
        super().__init__(session, ticker, interval, db_manager)
        self.db = db_manager

    def check_signal(self):
        # 1. Проверка глобального тренда (HTF)
        htf_trend = self.get_htf_trend()
        if htf_trend == 0: return None

        # 2. Получение данных
        df = self.get_data(limit=100)
        if df.empty or len(df) < 50: return None

        # Расчет EMA
        df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
        
        atr, atr_pct = self.calculate_atr(df)
        slope = self.get_trend_strength(df, window=20)
        
        current_close = df['close'].iloc[-1]
        ema_f_curr, ema_s_curr = df['ema_fast'].iloc[-1], df['ema_slow'].iloc[-1]
        ema_f_prev, ema_s_prev = df['ema_fast'].iloc[-2], df['ema_slow'].iloc[-2]

        # --- ЛОГИКА ТРЕНДА ---

        # А) Вход в LONG
        # Условия: HTF Бычий + Наклон > 0.2 + Пересечение EMA вверх
        if htf_trend == 1 and slope > 0.12:
            if ema_f_prev <= ema_s_prev and ema_f_curr > ema_s_curr:
                return {
                    'ticker': self.ticker, 'signal': 'long', 'entry': current_close,
                    'sl': current_close - (2 * atr), 
                    'tp': current_close + (4 * atr), # В тренде тейк больше (соотношение 1:2)
                    'atr': atr, 'strategy': f'trend_{self.interval}'
                }

        # Б) Вход в SHORT
        # Условия: HTF Медвежий + Наклон < -0.2 + Пересечение EMA вниз
        elif htf_trend == -1 and slope < -0.12:
            if ema_f_prev >= ema_s_prev and ema_f_curr < ema_s_curr:
                return {
                    'ticker': self.ticker, 'signal': 'short', 'entry': current_close,
                    'sl': current_close + (2 * atr),
                    'tp': current_close - (4 * atr),
                    'atr': atr, 'strategy': f'trend_{self.interval}'
                }

        return None