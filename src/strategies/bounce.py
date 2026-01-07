import pandas as pd
from .base import BaseStrategy

class BounceStrategy(BaseStrategy):
    def __init__(self, session, ticker, interval, db_manager):
        super().__init__(session, ticker, interval, db_manager)
        self.db = db_manager

    def check_signal(self):
        # 1. Получаем данные
        df = self.get_data(limit=120)
        if df.empty: return None

        atr, atr_pct = self.calculate_atr(df)
        current_close = df['close'].iloc[-1]

        # 2. Поиск уровней (Жесткое окно 10)
        res_raw, sup_raw = self.find_levels(df, window=10)
        
        # 3. Кластеризация и фильтрация качества
        # Оставляем только те уровни, которые прошли проверку на "чистоту"
        valid_res = [r for r in self.cluster_levels(res_raw, atr_pct) 
                     if self.check_level_quality(df, r, 'resistance', atr_pct)]
        
        valid_sup = [s for s in self.cluster_levels(sup_raw, atr_pct) 
                     if self.check_level_quality(df, s, 'support', atr_pct)]

        # --- УСЛОВИЕ КОРИДОРА ---
        # Обязательно наличие и качественного сопротивления СВЕРХУ, и поддержки СНИЗУ
        upper_levels = [r for r in valid_res if r > current_close]
        lower_levels = [s for s in valid_sup if s < current_close]

        if not upper_levels or not lower_levels:
            return None # Нет четкого коридора - нет сделки

        # Ближайшие границы
        nearest_res = min(upper_levels)
        nearest_sup = max(lower_levels)

        # --- ЛОГИКА СИГНАЛА ---
        
        # А) Отскок от сопротивления (SHORT)
        if nearest_res * 0.998 <= current_close <= nearest_res * 1.002:
            # Тейк: поддержка + 1% отступ (чтобы точно заполнило)
            tp = nearest_sup * 1.01
            # Стоп: 2% ЗА уровнем
            sl = nearest_res * 1.02
            
            # Доп. проверка: соотношение риск/прибыль хотя бы 1 к 1
            if abs(current_close - tp) > abs(current_close - sl):
                return {
                    'ticker': self.ticker, 'signal': 'short', 'entry': current_close,
                    'sl': sl, 'tp': tp, 'atr': atr, 'strategy': f'bounce_{self.interval}'
                }

        # Б) Отскок от поддержки (LONG)
        if nearest_sup * 0.998 <= current_close <= nearest_sup * 1.002:
            # Тейк: сопротивление - 1% отступ
            tp = nearest_res * 0.99
            # Стоп: 2% ЗА уровнем
            sl = nearest_sup * 0.98
            
            if abs(current_close - tp) > abs(current_close - sl):
                return {
                    'ticker': self.ticker, 'signal': 'long', 'entry': current_close,
                    'sl': sl, 'tp': tp, 'atr': atr, 'strategy': f'bounce_{self.interval}'
                }

        return None