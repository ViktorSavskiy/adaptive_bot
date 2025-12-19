import pandas as pd
from .base import BaseStrategy

class BreakoutStrategy(BaseStrategy):
    def __init__(self, session, ticker, interval, db_manager):
        super().__init__(session, ticker, interval)
        self.db = db_manager

    def analyze_touches(self, df, levels, level_type, atr_pct):
        """
        Считает количество касаний для каждого уровня.
        Логика из твоего кода: попадание high/low в зону уровня (порог = ATR %)
        """
        results = {}
        threshold = atr_pct / 100
        
        for level in levels:
            if level_type == 'resistance':
                # Касание сопротивления максимумом свечи
                touches = df[
                    (df['high'] >= level * (1 - threshold)) & 
                    (df['high'] <= level * (1 + threshold))
                ]
            else:
                # Касание поддержки минимумом свечи
                touches = df[
                    (df['low'] >= level * (1 - threshold)) & 
                    (df['low'] <= level * (1 + threshold))
                ]
            
            results[level] = len(touches)
        return results

    def check_signal(self):
        """Основная логика стратегии пробоя"""
        df = self.get_data(limit=100)
        if df.empty:
            return None

        # 1. Получаем ATR и силу тренда
        atr, atr_pct = self.calculate_atr(df)
        trend_slope = self.get_trend_strength(df, window=15)
        
        # 2. Ищем уровни (window=3 как в твоем примере)
        res_raw, sup_raw = self.find_levels(df, window=3)
        
        # 3. Кластеризуем уровни
        res_levels = self.cluster_levels(res_raw, atr_pct)
        sup_levels = self.cluster_levels(sup_raw, atr_pct)

        current_close = df['close'].iloc[-1]
        current_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].tail(20).mean()

        # Получаем данные тикера для Открытого Интереса
        try:
            ticker_info = self.session.get_tickers(category="linear", symbol=self.ticker)['result']['list'][0]
            current_oi = float(ticker_info['openInterestValue'])
            # Для простоты примера предположим, что мы сравниваем со средним OI за последние свечи, 
            # либо этот параметр будет передаваться из оркестратора
        except:
            current_oi = 0

        # --- ЛОГИКА ПРОБОЯ СОПРОТИВЛЕНИЯ (LONG) ---
        if trend_slope > 0.2:
            touch_counts = self.analyze_touches(df, res_levels, 'resistance', atr_pct)
            
            for level, count in touch_counts.items():
                # Условия из твоего кода: 
                # 1. Касаний >= 4
                # 2. Цена рядом (0.2% от уровня)
                # 3. Объем > среднего на 30%
                if count >= 4:
                    is_near = level * (1 - 0.002) <= current_close <= level * (1 + 0.002)
                    vol_confirm = current_volume > avg_volume * 1.3
                    
                    if is_near and vol_confirm:
                        return {
                            'ticker': self.ticker,
                            'signal': 'long',
                            'entry': current_close,
                            'sl': current_close - (2 * atr),
                            'tp': current_close + (3 * atr),
                            'strategy': 'breakout'
                        }

        # --- ЛОГИКА ПРОБОЯ ПОДДЕРЖКИ (SHORT) ---
        elif trend_slope < -0.2:
            touch_counts = self.analyze_touches(df, sup_levels, 'support', atr_pct)
            
            for level, count in touch_counts.items():
                if count >= 4:
                    is_near = level * (1 - 0.002) <= current_close <= level * (1 + 0.002)
                    vol_confirm = current_volume > avg_volume * 1.3
                    
                    if is_near and vol_confirm:
                        return {
                            'ticker': self.ticker,
                            'signal': 'short',
                            'entry': current_close,
                            'sl': current_close + (2 * atr),
                            'tp': current_close - (3 * atr),
                            'strategy': 'breakout'
                        }

        return None