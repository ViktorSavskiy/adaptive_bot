import pandas as pd
import numpy as np
from .base import BaseStrategy

class BounceStrategy(BaseStrategy):
    def check_signal(self):
        # 1. Загрузка данных
        df = self.get_data(limit=150)
        if df.empty or len(df) < 100: 
            return None

        # 2. Получение параметров (v9_GoldenRatio)
        sl_mult = self.params.get('bounce_sl', 1.5)
        tp_mult = self.params.get('bounce_tp', 4.5) # Используем наш оптимизированный ТП

        # 3. Расчет индикаторов
        atr, atr_pct = self.calculate_atr(df)
        if atr <= 0: return None

        current_close = df['close'].iloc[-1]
        current_high = df['high'].iloc[-1]
        current_low = df['low'].iloc[-1]
        
        # 4. Поиск и фильтрация уровней
        res_raw, sup_raw = self.find_levels(df, window=7)
        
        # Используем методы из BaseStrategy
        valid_res = [r for r in self.cluster_levels(res_raw, atr_pct) 
                     if self.check_level_quality(df, r, 'resistance', atr_pct)]
        
        valid_sup = [s for s in self.cluster_levels(sup_raw, atr_pct) 
                     if self.check_level_quality(df, s, 'support', atr_pct)]

        # 5. Глобальный тренд (HTF)
        htf_trend = self.get_htf_trend()

        # 6. Поиск ближайших границ коридора
        upper_levels = [r for r in valid_res if r > current_close * 1.001]
        lower_levels = [s for s in valid_sup if s < current_close * 0.999]

        if not upper_levels or not lower_levels:
            return None # Стратегии нужен четкий канал

        nearest_res = min(upper_levels)
        nearest_sup = max(lower_levels)
        
        # Зона входа (20% от ATR)
        entry_zone = atr * 0.2

        # --- ЛОГИКА SHORT (Отскок от сопротивления) ---
        # Входим, если тренд не бычий (медвежий или боковик)
        if htf_trend <= 0:
            if nearest_res - entry_zone <= current_high <= nearest_res + entry_zone:
                # Стоп-лосс за уровнем
                sl = nearest_res + (atr * sl_mult)
                # Тейк-профит: берем либо противоположный уровень, либо по множителю ATR (что ближе)
                tp_level = nearest_sup + (atr * 0.5)
                tp_atr = current_close - (atr * tp_mult)
                tp = max(tp_level, tp_atr) # Выбираем более консервативный/достижимый тейк
                
                # Проверка Risk/Reward (минимум 1.5)
                risk = sl - current_close
                reward = current_close - tp
                if risk > 0 and (reward / risk) >= 1.5:
                    return {
                        'ticker': self.ticker, 'signal': 'short', 'entry': current_close,
                        'sl': sl, 'tp': tp, 'atr': atr, 
                        'strategy': f'bounce_{self.interval}'
                    }

        # --- ЛОГИКА LONG (Отскок от поддержки) ---
        # Входим, если тренд не медвежий
        if htf_trend >= 0:
            if nearest_sup - entry_zone <= current_low <= nearest_sup + entry_zone:
                sl = nearest_sup - (atr * sl_mult)
                tp_level = nearest_res - (atr * 0.5)
                tp_atr = current_close + (atr * tp_mult)
                tp = min(tp_level, tp_atr)
                
                risk = current_close - sl
                reward = tp - current_close
                if risk > 0 and (reward / risk) >= 1.5:
                    return {
                        'ticker': self.ticker, 'signal': 'long', 'entry': current_close,
                        'sl': sl, 'tp': tp, 'atr': atr, 
                        'strategy': f'bounce_{self.interval}'
                    }

        return None