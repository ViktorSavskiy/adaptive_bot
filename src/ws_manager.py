import time
from pybit.unified_trading import WebSocket
from loguru import logger

class WSManager:
    def __init__(self, api_key, api_secret, testnet=False):
        self.prices = {}
        self.last_update_time = 0  # Время последнего сообщения
        self.message_count = 0     # Счетчик сообщений для статистики
        
        try:
            self.ws = WebSocket(
                testnet=testnet,
                channel_type="linear",
                api_key=api_key,
                api_secret=api_secret
            )
            logger.info("📡 WebSocket: Инициализация соединения...")
        except Exception as e:
            logger.error(f"❌ WebSocket: Ошибка при запуске: {e}")

    def handle_message(self, msg):
        """Обработка входящих данных"""
        if "data" in msg:
            data = msg["data"]
            # В потоке тикеров Bybit данные могут быть списком или словарем
            if isinstance(data, dict):
                symbol = data.get("symbol")
                price = data.get("lastPrice")
                if symbol and price:
                    self.prices[symbol] = float(price)
                    self.last_update_time = time.time()
                    self.message_count += 1
            elif isinstance(data, list):
                for item in data:
                    symbol = item.get("symbol")
                    price = item.get("lastPrice")
                    if symbol and price:
                        self.prices[symbol] = float(price)
                        self.last_update_time = time.time()
                        self.message_count += 1

    def subscribe_tickers(self, tickers):
        """Подписка на монеты"""
        try:
            for ticker in tickers:
                self.ws.ticker_stream(symbol=ticker, callback=self.handle_message)
            logger.success(f"📡 WebSocket: Подписан на {len(tickers)} тикеров")
        except Exception as e:
            logger.error(f"❌ WebSocket: Ошибка подписки: {e}")

    def get_last_price(self, ticker):
        return self.prices.get(ticker)

    def get_status(self):
        """Возвращает текстовый статус соединения"""
        if self.last_update_time == 0:
            return "🟠 ОЖИДАНИЕ ДАННЫХ"
        
        # Если данных нет больше 30 секунд — считаем соединение зависшим
        time_since_last_msg = time.time() - self.last_update_time
        if time_since_last_msg > 30:
            return f"🔴 ЗАВИСЛО ({int(time_since_last_msg)} сек. без данных)"
        
        return f"🟢 АКТИВНО ({self.message_count} сообщ. получено)"