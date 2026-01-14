import pandas as pd
from datetime import datetime, timezone

class BacktestSession:
    def __init__(self, history_dict):
        """
        history_dict: { 'BTCUSDT_15': DataFrame, ... }
        """
        self.history = history_dict
        self.sim_time = None

    def get_kline(self, category, symbol, interval, limit, **kwargs):
        key = f"{symbol}_{interval}"
        df = self.history.get(key)
        if df is None: 
            return {'retCode': 0, 'result': {'list': []}}
        
        # Индекс теперь передается из engine.py для мгновенного доступа
        # Если индекса нет (например, при первом запуске), ищем его
        idx = getattr(self, f"_idx_{key}", None)
        if idx is None:
            idx = df['time'].searchsorted(self.sim_time, side='left')
        
        # Срез данных (limit свечей до текущего момента)
        subset = df.iloc[max(0, idx - int(limit)):idx]
        
        if subset.empty:
            return {'retCode': 0, 'result': {'list': []}}

        # Возвращаем список в формате Bybit (от новых к старым)
        # Колонки: time_ms, open, high, low, close, volume, turnover
        res = subset[['time_ms', 'open', 'high', 'low', 'close', 'volume', 'turnover']].values.tolist()
        return {'retCode': 0, 'result': {'list': res[::-1]}}

    def get_last_price(self, ticker):
        """Цена последней закрытой 15м свечи"""
        key = f"{ticker}_15"
        df = self.history.get(key)
        if df is not None:
            idx = getattr(self, f"_idx_{key}", None)
            if idx is None:
                idx = df['time'].searchsorted(self.sim_time, side='left')
            if idx > 0:
                return float(df.iloc[idx-1]['close'])
        return None

    def get_tickers(self, category, symbol=None):
        if symbol:
            price = self.get_last_price(symbol)
            return {'result': {'list': [{'symbol': symbol, 'lastPrice': str(price or 0), 'turnover24h': '50000000'}]}}
        
        unique_symbols = list(set([k.split('_')[0] for k in self.history.keys()]))
        return {'result': {'list': [{'symbol': s, 'lastPrice': '1.0', 'turnover24h': '50000000'} for s in unique_symbols]}}

    def get_wallet_balance(self, **kwargs):
        return {
            'retCode': 0,
            'result': {
                'list': [{
                    'totalEquity': '1000', 
                    'availableToWithdraw': '1000',
                    'coin': [{'coin': 'USDT', 'availableToWithdraw': '1000', 'equity': '1000'}]
                }]
            }
        }

    def place_order(self, **kwargs): return {'retCode': 0, 'result': {'orderId': 'bt_order'}}
    def get_instruments_info(self, **kwargs):
        return {'retCode': 0, 'result': {'list': [{
            'lotSizeFilter': {'qtyStep': '0.001', 'minOrderQty': '0.001'},
            'priceFilter': {'tickSize': '0.01'}
        }]}}
    def set_leverage(self, **kwargs): return {'retCode': 0}
    def get_positions(self, **kwargs): return {'retCode': 0, 'result': {'list': []}}