import pandas as pd
from .base import BaseStrategy

class FakeoutStrategy(BaseStrategy):
    def check_signal(self):
        # 1. Получение данных
        df = self.get_data(limit=150)
        if df.empty or len(df) < 60: 
            return None

        # 2. Параметры (v9_GoldenRatio)
        sl_mult = self.params.get('fakeout_sl', 1.0) # Множитель ATR для отступа стопа
        tp_mult = self.params.get('fakeout_tp', 2.5) # Тейк-профит

        # 3. Индикаторы
        atr, atr_pct = self.calculate_atr(df)
        if atr <= 0: return None
        
        last_candle = df.iloc[-1]
        
        # 4. Поиск и фильтрация уровней (window=10 для сильных зон)
        res_raw, sup_raw = self.find_levels(df, window=10)
        valid_res = [r for r in self.cluster_levels(res_raw, atr_pct) 
                     if self.check_level_quality(df, r, 'resistance', atr_pct)]
        
        valid_sup = [s for s in self.cluster_levels(sup_raw, atr_pct) 
                     if self.check_level_quality(df, s, 'support', atr_pct)]

        # 5. Условия "закола" (от 0.3 до 1.5 ATR)
        min_poke, max_poke = atr * 0.3, atr * 1.5
        volume_spike = self.analyze_volume_spike(df, multiplier=1.2)
        htf_trend = self.get_htf_trend()

        # --- ЛОГИКА SHORT (Ложный пробой сопротивления) ---
        # В идеале торгуем, когда глобальный тренд не бычий
        if htf_trend <= 0:
            for level in valid_res:
                poke_dist = last_candle['high'] - level
                # Если хай свечи выше уровня, а закрытие под ним + объем
                if min_poke < poke_dist < max_poke and last_candle['close'] < level and volume_spike:
                    
                    # Стоп за хай "закола" + отступ
                    sl = last_candle['high'] + (atr * sl_mult * 0.2)
                    tp = last_candle['close'] - (atr * tp_mult)
                    
                    # Проверка Risk/Reward (минимум 1.5)
                    risk = sl - last_candle['close']
                    reward = last_candle['close'] - tp
                    if risk > 0 and (reward / risk) >= 1.5:
                        return {
                            'ticker': self.ticker, 'signal': 'short', 'entry': last_candle['close'], 
                            'sl': sl, 'tp': tp, 'atr': atr, 
                            'strategy': f'fakeout_{self.interval}'
                        }

        # --- ЛОГИКА LONG (Ложный пробой поддержки) ---
        if htf_trend >= 0:
            for level in valid_sup:
                poke_dist = level - last_candle['low']
                if min_poke < poke_dist < max_poke and last_candle['close'] > level and volume_spike:
                    
                    sl = last_candle['low'] - (atr * sl_mult * 0.2)
                    tp = last_candle['close'] + (atr * tp_mult)
                    
                    risk = last_candle['close'] - sl
                    reward = tp - last_candle['close']
                    if risk > 0 and (reward / risk) >= 1.5:
                        return {
                            'ticker': self.ticker, 'signal': 'long', 'entry': last_candle['close'], 
                            'sl': sl, 'tp': tp, 'atr': atr, 
                            'strategy': f'fakeout_{self.interval}'
                        }

        return None