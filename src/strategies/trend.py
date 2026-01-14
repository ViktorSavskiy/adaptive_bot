import pandas as pd
import numpy as np
from .base import BaseStrategy

class TrendStrategy(BaseStrategy):
    def check_signal(self):
        # 1. Глобальный тренд (EMA 200 на HTF)
        htf_trend = self.get_htf_trend()
        if htf_trend == 0: 
            return None

        # 2. Получение данных
        df = self.get_data(limit=250)
        if df.empty or len(df) < 100: 
            return None

        # 3. Параметры (v9_GoldenRatio)
        adx_min = self.params.get('trend_adx', 35)
        sl_mult = self.params.get('trend_sl', 1.5)
        tp_mult = self.params.get('trend_tp', 6.0)

        # 4. Расчет индикаторов (EMA 9, 21, 50)
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        atr, _ = self.calculate_atr(df)
        if atr <= 0: return None
        
        adx = self.calculate_adx(df)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        entry_price = last['close']

        # --- ЛОГИКА LONG (Бычий тренд) ---
        # Условия: HTF=Up, ADX сильный, Идеальный порядок средних (9 > 21 > 50)
        if htf_trend == 1 and adx > adx_min:
            if last['ema9'] > last['ema21'] and last['ema21'] > last['ema50']:
                # Триггер: Пересечение ИЛИ отскок от EMA21
                if (prev['ema9'] <= prev['ema21'] and last['ema9'] > last['ema21']) or \
                   (prev['low'] <= prev['ema21'] and last['close'] > last['ema21']):
                    
                    # Стоп ставим за EMA50 или по ATR (что ниже/безопаснее)
                    sl = min(last['ema50'], entry_price - (atr * sl_mult))
                    tp = entry_price + (atr * tp_mult)
                    
                    # Проверка Risk/Reward (минимум 2.0 для тренда)
                    risk = entry_price - sl
                    reward = tp - entry_price
                    if risk > 0 and (reward / risk) >= 2.0:
                        return {
                            'ticker': self.ticker, 'signal': 'long', 'entry': entry_price, 
                            'sl': sl, 'tp': tp, 'atr': atr, 
                            'strategy': f'trend_{self.interval}'
                        }

        # --- ЛОГИКА SHORT (Медвежий тренд) ---
        if htf_trend == -1 and adx > adx_min:
            if last['ema9'] < last['ema21'] and last['ema21'] < last['ema50']:
                if (prev['ema9'] >= prev['ema21'] and last['ema9'] < last['ema21']) or \
                   (prev['high'] >= prev['ema21'] and last['close'] < last['ema21']):
                    
                    # Стоп за EMA50 или по ATR (что выше)
                    sl = max(last['ema50'], entry_price + (atr * sl_mult))
                    tp = entry_price - (atr * tp_mult)
                    
                    risk = sl - entry_price
                    reward = entry_price - tp
                    if risk > 0 and (reward / risk) >= 2.0:
                        return {
                            'ticker': self.ticker, 'signal': 'short', 'entry': entry_price, 
                            'sl': sl, 'tp': tp, 'atr': atr, 
                            'strategy': f'trend_{self.interval}'
                        }

        return None

    def calculate_adx(self, df, period=14):
        """Устойчивый расчет ADX"""
        try:
            df = df.copy()
            plus_dm = df['high'].diff().clip(lower=0)
            minus_dm = (-df['low'].diff()).clip(lower=0)
            
            plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0)
            minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0)
            
            tr = np.maximum(df['high'] - df['low'], 
                 np.maximum(abs(df['high'] - df['close'].shift(1)), 
                 abs(df['low'] - df['close'].shift(1))))
            
            atr_s = pd.Series(tr).rolling(window=period).mean()
            
            plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr_s)
            minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr_s)
            
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            adx = dx.rolling(window=period).mean().iloc[-1]
            
            return adx if not np.isnan(adx) else 0
        except:
            return 0