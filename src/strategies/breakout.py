import pandas as pd
from .base import BaseStrategy

class BreakoutStrategy(BaseStrategy):
    def check_signal(self):
        # 1. Глобальный тренд (HTF) — Пробой торгуем только по направлению рынка
        htf_trend = self.get_htf_trend()
        if htf_trend == 0: 
            return None

        # 2. Получение данных
        df = self.get_data(limit=100)
        if df.empty or len(df) < 50: 
            return None

        # 3. Параметры (из конфига v9_GoldenRatio или дефолты)
        vol_mult = self.params.get('breakout_vol', 1.5) # Рекомендуем 1.5 для баланса
        sl_mult = self.params.get('breakout_sl', 1.0)
        tp_mult = self.params.get('breakout_tp', 4.0)  # Увеличили тейк для лучшего RR

        atr, _ = self.calculate_atr(df)
        if atr <= 0: return None
        
        last_close = df['close'].iloc[-1]
        last_open = df['open'].iloc[-1]
        
        # 4. Определение границ канала за последние 30 закрытых свечей
        lookback = 30
        channel_high = df['high'].iloc[-(lookback+1):-1].max()
        channel_low = df['low'].iloc[-(lookback+1):-1].min()
        
        # 5. Проверка всплеска объема
        volume_ok = self.analyze_volume_spike(df, multiplier=vol_mult)

        # --- ЛОГИКА LONG ---
        if htf_trend == 1:
            # Условия: Закрылись выше канала, открылись внутри/ниже, цена не улетела слишком далеко (0.5 ATR)
            if last_close > channel_high and last_open <= channel_high:
                if last_close <= (channel_high + atr * 0.5) and volume_ok:
                    
                    # Стоп ставим чуть ниже пробитого уровня
                    sl = channel_high - (atr * sl_mult)
                    tp = last_close + (atr * tp_mult)
                    
                    # Защита: стоп должен быть ниже цены входа
                    if sl < last_close:
                        return {
                            'ticker': self.ticker, 'signal': 'long', 'entry': last_close, 
                            'sl': sl, 'tp': tp, 'atr': atr, 
                            'strategy': f'breakout_{self.interval}'
                        }

        # --- ЛОГИКА SHORT ---
        elif htf_trend == -1:
            # Условия: Закрылись ниже канала, открылись внутри/выше
            if last_close < channel_low and last_open >= channel_low:
                if last_close >= (channel_low - atr * 0.5) and volume_ok:
                    
                    sl = channel_low + (atr * sl_mult)
                    tp = last_close - (atr * tp_mult)
                    
                    if sl > last_close:
                        return {
                            'ticker': self.ticker, 'signal': 'short', 'entry': last_close, 
                            'sl': sl, 'tp': tp, 'atr': atr, 
                            'strategy': f'breakout_{self.interval}'
                        }

        return None