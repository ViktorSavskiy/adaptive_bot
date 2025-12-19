import pandas as pd
from .base import BaseStrategy

class BounceStrategy(BaseStrategy):
    def __init__(self, session, ticker, interval, db_manager):
        super().__init__(session, ticker, interval)
        self.db = db_manager

    def analyze_touches(self, df, levels, level_type, atr_pct):
        """Считает количество касаний уровня (логика аналогична breakout)"""
        results = {}
        threshold = atr_pct / 100
        
        for level in levels:
            if level_type == 'resistance':
                touches = df[
                    (df['high'] >= level * (1 - threshold)) & 
                    (df['high'] <= level * (1 + threshold))
                ]
            else:
                touches = df[
                    (df['low'] >= level * (1 - threshold)) & 
                    (df['low'] <= level * (1 + threshold))
                ]
            results[level] = len(touches)
        return results

    def check_signal(self):
        """Логика стратегии отскока от уровней"""
        df = self.get_data(limit=100)
        if df.empty:
            return None

        atr, atr_pct = self.calculate_atr(df)
        # Для отскока лучше всего подходит флэт или умеренный тренд
        trend_slope = self.get_trend_strength(df, window=15)
        
        res_raw, sup_raw = self.find_levels(df, window=3)
        res_levels = self.cluster_levels(res_raw, atr_pct)
        sup_levels = self.cluster_levels(sup_raw, atr_pct)

        current_close = df['close'].iloc[-1]
        current_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].tail(20).mean()

        # --- ОТСКОК ОТ СОПРОТИВЛЕНИЯ (SHORT) ---
        # Если цена у сопротивления, но объем НЕ растет (нет сил на пробой)
        touch_counts_res = self.analyze_touches(df, res_levels, 'resistance', atr_pct)
        for level, count in touch_counts_res.items():
            if count >= 4:
                is_near = level * (1 - 0.002) <= current_close <= level * (1 + 0.002)
                # Условие отскока: объем обычный или низкий (нет всплеска 1.3x)
                low_volume = current_volume <= avg_volume * 1.1 
                
                if is_near and low_volume:
                    return {
                        'ticker': self.ticker,
                        'signal': 'short',
                        'entry': current_close,
                        # Стоп ставим за уровень сопротивления
                        'sl': level * 1.005, 
                        'tp': current_close - (2 * atr),
                        'strategy': 'bounce'
                    }

        # --- ОТСКОК ОТ ПОДДЕРЖКИ (LONG) ---
        touch_counts_sup = self.analyze_touches(df, sup_levels, 'support', atr_pct)
        for level, count in touch_counts_sup.items():
            if count >= 4:
                is_near = level * (1 - 0.002) <= current_close <= level * (1 + 0.002)
                low_volume = current_volume <= avg_volume * 1.1
                
                if is_near and low_volume:
                    return {
                        'ticker': self.ticker,
                        'signal': 'long',
                        'entry': current_close,
                        # Стоп ставим за уровень поддержки
                        'sl': level * 0.995,
                        'tp': current_close + (2 * atr),
                        'strategy': 'bounce'
                    }

        return None