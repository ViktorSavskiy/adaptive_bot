def get_symbol_info(self, symbol):
        """Получает точность цены и количества для символа"""
        try:
            res = self.session.get_instruments_info(category="linear", symbol=symbol)
            info = res['result']['list'][0]
            return {
                'qty_step': float(info['lotSizeFilter']['qtyStep']),
                'price_step': float(info['priceFilter']['tickSize']),
                'min_qty': float(info['lotSizeFilter']['minOrderQty'])
            }
        except Exception as e:
            logger.error(f"Ошибка получения инфо символа {symbol}: {e}")
            return None

def set_leverage(self, symbol, leverage):
        """Устанавливает плечо на Bybit"""
        try:
            self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage)
            )
        except Exception as e:
            # Если плечо уже стоит такое же, Bybit вернет ошибку, это нормально
            passdef get_symbol_info(self, symbol):
        """Получает точность цены и количества для символа"""
        try:
            res = self.session.get_instruments_info(category="linear", symbol=symbol)
            info = res['result']['list'][0]
            return {
                'qty_step': float(info['lotSizeFilter']['qtyStep']),
                'price_step': float(info['priceFilter']['tickSize']),
                'min_qty': float(info['lotSizeFilter']['minOrderQty'])
            }
        except Exception as e:
            logger.error(f"Ошибка получения инфо символа {symbol}: {e}")
            return None

def set_leverage(self, symbol, leverage):
        """Устанавливает плечо на Bybit"""
        try:
            self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage)
            )
        except Exception as e:
            # Если плечо уже стоит такое же, Bybit вернет ошибку, это нормально
            pass

def get_real_balance(self):
    try:
        res = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        return float(res['result']['list'][0]['totalEquity'])
    except:
        return 100.0 # Запасной вариант