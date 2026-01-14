import time
from pybit.unified_trading import WebSocket
from loguru import logger

class WSManager:
    def __init__(self, api_key, api_secret, testnet=False):
        self.prices = {}
        self.last_update_time = 0 
        self.message_count = 0    
        self.subscribed_topics = set() # Храним текущие подписки, чтобы не спамить в API
        
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        self._connect()

    def _connect(self):
        """Внутренний метод для (пере)подключения"""
        try:
            self.ws = WebSocket(
                testnet=self.testnet,
                channel_type="linear",
                api_key=self.api_key,
                api_secret=self.api_secret
            )
            logger.info("📡 WebSocket: Соединение установлено.")
        except Exception as e:
            logger.error(f"❌ WebSocket: Критическая ошибка подключения: {e}")

    def handle_message(self, msg):
        """Обработка тикеров: вытаскиваем только актуальную цену"""
        try:
            # Проверка структуры сообщения Bybit
            if "data" in msg:
                data = msg["data"]
                
                # Данные могут прийти как один словарь (dict) или список (list)
                items = data if isinstance(data, list) else [data]
                
                for item in items:
                    symbol = item.get("symbol")
                    price = item.get("lastPrice")
                    
                    if symbol and price:
                        # Обновляем локальный кэш цен
                        self.prices[symbol] = float(price)
                        self.last_update_time = time.time()
                        self.message_count += 1
                        
        except Exception as e:
            logger.error(f"❌ WebSocket: Ошибка парсинга сообщения: {e}")

    def subscribe_tickers(self, tickers):
        """Умная подписка: только на новые монеты"""
        new_tickers = []
        for t in tickers:
            if t not in self.subscribed_topics:
                new_tickers.append(t)
        
        if not new_tickers:
            return # Мы уже на всё подписаны

        try:
            for ticker in new_tickers:
                # Подписываемся на индивидуальный поток тикера
                self.ws.ticker_stream(symbol=ticker, callback=self.handle_message)
                self.subscribed_topics.add(ticker)
                
            logger.info(f"📡 WebSocket: Успешная подписка на {len(new_tickers)} новых монет. Всего: {len(self.subscribed_topics)}")
        except Exception as e:
            # Если ошибка "already subscribed", просто игнорируем её
            if "already subscribed" in str(e).lower():
                pass
            else:
                logger.error(f"❌ WebSocket: Ошибка подписки: {e}")

    def get_last_price(self, ticker):
        price = self.prices.get(ticker)
        if price is None:
            # Логируем только один раз в 30 секунд для конкретной монеты
            # чтобы не спамить каждую секунду
            last_log_key = f"log_{ticker}"
            last_log_time = getattr(self, last_log_key, 0)
            if time.time() - last_log_time > 30:
                logger.warning(f"⚠️ WebSocket: Цена для {ticker} временно недоступна (проверьте связь)")
                setattr(self, last_log_key, time.time())
        return price

    def get_status(self):
        """Проверка 'здоровья' потока данных"""
        if not self.last_update_time:
            return "🟠 ОЖИДАНИЕ ДАННЫХ"
        
        # Если данных нет больше 60 секунд — это повод для паники в реале
        diff = time.time() - self.last_update_time
        if diff > 60:
            return f"🔴 ЗАВИСЛО ({int(diff)} сек без обновлений)"
        
        return f"🟢 АКТИВНО (Подписок: {len(self.subscribed_topics)})"