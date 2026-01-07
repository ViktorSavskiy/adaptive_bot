import time
from datetime import datetime, timedelta
from loguru import logger

from .strategies.fakeout import FakeoutStrategy
from .database import DatabaseManager
from .strategies.breakout import BreakoutStrategy
from .strategies.bounce import BounceStrategy
from .strategies.trend import TrendStrategy
from .utils.telegram_notify import send_telegram_message

class Orchestrator:
    def __init__(self, session, ticker_list):
        self.session = session
        self.db = DatabaseManager()
        self.all_tickers = ticker_list
        self.ws = None 

        # --- НАСТРОЙКИ ТАЙМИНГА ---
        last_reset = self.db.get_last_reset_time()
        if last_reset:
            self.cycle_start_time = last_reset
            logger.info(f"📅 Цикл подхвачен из базы. Начало: {self.cycle_start_time}")
        else:
            self.cycle_start_time = datetime.utcnow()
            self.db.save_reset_time(self.cycle_start_time)
            logger.info(f"🆕 Начало первого цикла: {self.cycle_start_time}")

        self.cycle_duration_hours = 24  
        
        # --- СОСТОЯНИЕ ТОРГОВЛИ ---
        self.live_trading_blocked = False  
        
        # --- РИСКИ И ЛИМИТЫ (Оптимизировано под $68) ---
        self.initial_virtual_deposit = 68.0
        self.risk_per_trade = 0.02      # Риск 2%
        self.max_leverage = 3    
        self.max_order_usd_limit = 40.0  # <--- НОВЫЙ ЖЕСТКИЙ ЛИМИТ $40       # Плечо 3х
        self.slots_per_paper_strategy = 5 
        self.max_live_slots_total = 5   # 5 слота (диверсификация)
        
        # --- ФИЛЬТРЫ РЫНКА ---
        self.min_volume_24h = 20_000_000 
        self.timeframes = ["15", "60"]
        self.warmup_hours = 24

        # Сразу определяем лидера при запуске
        self.select_best_strategy_extended()

    # --- МЕТОДЫ API BYBIT ---

    def get_balances(self):
        """Получает баланс с защитой от пустых строк и лагов маржи"""
        try:
            res = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            if res['retCode'] != 0: return {'equity': self.initial_virtual_deposit, 'available': 0.0}

            account_data = res['result']['list'][0]
            
            def to_f(val, default=0.0):
                if val is None or str(val).strip() == "": return default
                try: return float(val)
                except: return default

            equity = to_f(account_data.get('totalEquity'), self.initial_virtual_deposit)
            available = 0.0
            for c in account_data.get('coin', []):
                if c.get('coin') == 'USDT':
                    available = to_f(c.get('availableToWithdraw'))
                    if available == 0: available = to_f(c.get('equity'))
                    break
            
            # Если позиция открыта на всё, available может быть 0
            return {'equity': equity, 'available': available}
        except:
            return {'equity': self.initial_virtual_deposit, 'available': 0.0}

    def get_market_tickers(self):
        try:
            response = self.session.get_tickers(category="linear")
            tickers_data = response.get('result', {}).get('list', [])
            blacklist = ['AVNTUSDT'] 
            return [t['symbol'] for t in tickers_data 
                    if float(t['turnover24h']) >= self.min_volume_24h 
                    and t['symbol'].endswith('USDT') 
                    and t['symbol'] not in blacklist]
        except: return self.all_tickers

    def get_symbol_info(self, symbol):
        try:
            res = self.session.get_instruments_info(category="linear", symbol=symbol)
            info = res['result']['list'][0]
            return {
                'qty_step': float(info['lotSizeFilter']['qtyStep']),
                'price_step': float(info['priceFilter']['tickSize']),
                'min_qty': float(info['lotSizeFilter']['minOrderQty'])
            }
        except: return None

    def set_leverage(self, symbol, leverage):
        try:
            self.session.set_leverage(category="linear", symbol=symbol, 
                                     buyLeverage=str(leverage), sellLeverage=str(leverage))
        except: pass

    def place_live_order(self, ticker, side, entry, sl, tp, amount_usd):
        """Выставление реального ордера. amount_usd — это номинал (уже с учетом плеча)"""
        try:
            self.set_leverage(ticker, self.max_leverage)
            info = self.get_symbol_info(ticker)
            if not info: return False

            # --- ИСПРАВЛЕННАЯ МАТЕМАТИКА QTY ---
            # amount_usd уже включает в себя плечо из метода calculate_position_size
            qty_raw = amount_usd / entry 
            
            qty = round(round(qty_raw / info['qty_step']) * info['qty_step'], 8)
            if qty < info['min_qty']: 
                logger.error(f"Qty {qty} ниже минималки {info['min_qty']}")
                return False

            sl = round(round(sl / info['price_step']) * info['price_step'], 8)
            tp = round(round(tp / info['price_step']) * info['price_step'], 8)

            res = self.session.place_order(
                category="linear", symbol=ticker, side="Buy" if side == "long" else "Sell",
                orderType="Market", qty=str(qty), takeProfit=str(tp), stopLoss=str(sl),
                tpOrderType="Market", slOrderType="Market", tpslMode="Full", isLeverage=1
            )
            if res['retCode'] == 0:
                logger.success(f"🚀 ОРДЕР ИСПОЛНЕН: {ticker}")
                return True
            else:
                logger.error(f"❌ Bybit Error {ticker}: {res['retMsg']}")
                send_telegram_message(f"❌ <b>ОШИБКА ОРДЕРА {ticker}</b>\n{res['retMsg']}")
                return False
        except Exception as e:
            logger.error(f"Ошибка метода ордера: {e}")
            return False

    # --- ЛОГИКА ТОРГОВЛИ ---

    def calculate_pnl_simple(self, trade, exit_price):
        """Расчет PnL с вычетом комиссии 0.12% (вход + выход)"""
        diff = (exit_price - trade.entry_price) / trade.entry_price
        if trade.side == 'short': diff = -diff
        
        gross_pnl = diff * trade.amount_usd
        # Комиссия Bybit (Taker) ~0.06% за открытие и 0.06% за закрытие = 0.12%
        fee = trade.amount_usd * 0.0012
        
        return round(gross_pnl - fee, 4)

    def calculate_position_size(self, entry, sl):
        """Рассчитывает номинальный объем позиции с жестким лимитом $40"""
        try:
            balances = self.get_balances()
            equity = balances['equity']
            available = balances['available'] if balances['available'] > 0 else 5.0 
            
            # 1. Расчет объема исходя из риска 2% (например, $1.36 при депо $68)
            risk_usd = equity * self.risk_per_trade 
            stop_dist = abs(entry - sl) / entry
            if stop_dist < 0.001: return 0
            
            ideal_nominal = risk_usd / stop_dist 
            
            # 2. Лимит маржи (исходя из свободных денег на бирже)
            free_slots = max(1, self.max_live_slots_total - self.db.get_active_trades_count('live'))
            # Сколько маржи можно выделить на один слот (с учетом плеча 3х и запаса 10%)
            max_nominal_by_margin = (available / free_slots) * self.max_leverage * 0.9
            
            # 3. Итоговый выбор (Самое МЕНЬШЕЕ из трех)
            # - идеальный риск
            # - физический предел кошелька
            # - твой жесткий лимит $40
            final_amount = min(ideal_nominal, max_nominal_by_margin, self.max_order_usd_limit)
            
            logger.info(f"Sizing: Риск {ideal_nominal:.1f}$, Маржа {max_nominal_by_margin:.1f}$, Лимит {self.max_order_usd_limit}$. Итог: {final_amount}$")
            
            return round(final_amount, 2)
        except Exception as e:
            logger.error(f"Ошибка в расчете сайзинга: {e}")
            return 0

    def check_cycle_reset(self):
        """Проверка завершения 24-часового цикла с умным закрытием позиций"""
        now = datetime.utcnow()
        if now - self.cycle_start_time > timedelta(hours=self.cycle_duration_hours):
            logger.warning("🏁 ЗАВЕРШЕНИЕ 24-ЧАСОВОГО ЦИКЛА. Анализируем смену лидера...")
            
            # Запоминаем старого лидера перед пересчетом
            old_leader = self.active_strategy_name
            
            # 1. Подводим итоги (это обновит self.active_strategy_name)
            self.select_best_strategy_extended()
            new_leader = self.active_strategy_name
            
            # 2. Если лидер сменился — чистим портфель от "старых" стратегий
            if new_leader != old_leader and old_leader is not None:
                logger.info(f"🔄 Лидер изменился ({old_leader} -> {new_leader}). Закрываем прибыльные/нейтральные сделки.")
                self.close_profitable_live_trades()
            
            # 3. Разблокируем торговлю и обновляем время
            self.live_trading_blocked = False
            self.cycle_start_time = now
            self.db.save_reset_time(now)
            
            send_telegram_message(
                f"📊 <b>НОВЫЙ ТОРГОВЫЙ ЦИКЛ (24ч)</b>\n"
                f"🏆 Лидер: <code>{new_leader}</code>\n"
                f"🛡️ LIVE: РАЗБЛОКИРОВАН"
            )
    def select_best_strategy_extended(self):
        """Умный выбор лидера на основе Profit Factor и количества сделок"""
        all_strats = [f"{n}_{tf}" for tf in self.timeframes for n in ['breakout', 'bounce', 'trend', 'fakeout']]
        
        best_name = None
        max_score = -999999

        for s in all_strats:
            stats = self.db.get_detailed_stats(s, hours=24)
            
            # Логируем расширенную инфо
            logger.info(f"📊 {s.ljust(12)} | PnL: {stats['pnl']:+.2f}$ | PF: {stats['pf']:.2f} | WR: {stats['wr']:.1f}% | Сделок: {stats['count']}")

            # --- МАТЕМАТИЧЕСКИЙ СКОРИНГ ---
            # Условия, чтобы стратегия считалась надежной:
            # 1. Хотя бы 3 сделки за сутки
            # 2. Profit Factor > 1.1 (зарабатывает больше, чем теряет)
            # 3. PnL > 0
            if stats['count'] >= 3 and stats['pf'] > 1.1 and stats['pnl'] > 0:
                score = stats['pnl'] * stats['pf'] # Вес прибыли умножаем на фактор стабильности
            else:
                score = stats['pnl'] - 100 # Штраф за нестабильность

            if score > max_score:
                max_score = score
                best_name = s

        if best_name and max_score > 0:
            self.active_strategy_name = best_name
            logger.success(f"🏆 ТЕКУЩИЙ ЛИДЕР: {best_name}")
        else:
            self.active_strategy_name = None
            logger.warning("⏸️ LIVE ПАУЗА: Надежных прибыльных стратегий не найдено.")

    def update_open_trades_ws(self):
        if not self.ws: return
        session = self.db.Session()
        open_trades = session.query(self.db.Trade).filter(self.db.Trade.status == 'open').all()
        
        for trade in open_trades:
            try:
                current_price = self.ws.get_last_price(trade.ticker)
                if not current_price: continue

                # 1. TTL (8ч для 15м, 24ч для 60м)
                ttl = 8 if "15" in trade.strategy_name else 24
                if datetime.utcnow() - trade.created_at > timedelta(hours=ttl):
                    if trade.trade_type == 'live': self.close_live_position(trade.ticker, trade.side)
                    pnl = self.calculate_pnl_simple(trade, current_price)
                    self.db.close_trade(trade.id, current_price, pnl)
                    send_telegram_message(f"⏰ <b>TTL ЗАКРЫТО: {trade.ticker}</b>\nPnL: ${pnl:.2f}")
                    continue

                # 2. Безубыток 2.5 ATR
                if not trade.is_breakeven and trade.atr_at_entry:
                    trigger = trade.atr_at_entry * 2.5
                    if (trade.side == 'long' and current_price >= trade.entry_price + trigger) or \
                       (trade.side == 'short' and current_price <= trade.entry_price - trigger):
                        trade.stop_loss = trade.entry_price
                        trade.is_breakeven = True
                        logger.info(f"🛡️ {trade.ticker} -> BE")

                # 3. Выход TP/SL
                is_closed = False
                exit_p = current_price
                if trade.side == 'long':
                    if current_price >= trade.take_profit: is_closed, exit_p = True, trade.take_profit
                    elif current_price <= trade.stop_loss: is_closed, exit_p = True, trade.stop_loss
                else:
                    if current_price <= trade.take_profit: is_closed, exit_p = True, trade.take_profit
                    elif current_price >= trade.stop_loss: is_closed, exit_p = True, trade.stop_loss

                if is_closed:
                    pnl = self.calculate_pnl_simple(trade, exit_p)
                    self.db.close_trade(trade.id, exit_p, pnl)
                    total_bal = self.get_balances()['equity']
                    icon = "🔥 LIVE" if trade.trade_type == 'live' else "🧪 PAPER"
                    logger.success(f"✅ {icon} {trade.ticker} закрыт. Баланс: ${total_bal:.2f}")
                    send_telegram_message(f"✅ <b>{icon} ЗАКРЫТ</b>\n{trade.ticker}\nPnL: ${pnl:+.2f}")

            except Exception as e: logger.error(f"WS Error: {e}")
        session.commit()
        session.close()

    def close_live_position(self, ticker, side):
        try:
            close_side = "Sell" if side == "long" else "Buy"
            pos = self.session.get_positions(category="linear", symbol=ticker)
            if pos['retCode'] == 0 and pos['result']['list']:
                qty = pos['result']['list'][0]['size']
                if float(qty) > 0:
                    self.session.place_order(category="linear", symbol=ticker, side=close_side,
                                             orderType="Market", qty=qty, reduceOnly=True, tpslMode="Full")
                    return True
            return False
        except: return False
    def close_profitable_live_trades(self):
        """Закрывает только те LIVE сделки, которые сейчас в плюсе или около нуля"""
        session = self.db.Session()
        open_live_trades = session.query(self.db.Trade).filter(
            self.db.Trade.trade_type == 'live',
            self.db.Trade.status == 'open'
        ).all()

        closed_count = 0
        for trade in open_live_trades:
            try:
                current_price = self.ws.get_last_price(trade.ticker)
                if not current_price: continue

                pnl = self.calculate_pnl_simple(trade, current_price)
                
                # Условие: закрываем, если профит >= -0.1$ (почти ноль или плюс)
                # Это позволяет выйти из сделки без существенного убытка при смене стратегии
                if pnl >= -0.10:
                    success = self.close_live_position(trade.ticker, trade.side)
                    if success:
                        self.db.close_trade(trade.id, current_price, pnl)
                        closed_count += 1
                        send_telegram_message(f"♻️ <b>Смена стратегии:</b> Закрыта нейтральная сделка {trade.ticker}\nPnL: ${pnl:.2f}")
            
            except Exception as e:
                logger.error(f"Ошибка при ротации сделки {trade.ticker}: {e}")

        session.close()
        logger.info(f"Ротация завершена. Закрыто {closed_count} сделок.")

    def run_cycle(self):
        if self.ws: logger.info(f"Статус WS: {self.ws.get_status()}")
        self.check_cycle_reset()
        
        if not self.live_trading_blocked and self.db.check_consecutive_live_losses(limit=3):
            self.live_trading_blocked = True
            send_telegram_message("🚨 <b>LIVE СТОП</b>: 3 убытка подряд.")

        current_tickers = self.get_market_tickers()
        
        strategy_classes = [('breakout', BreakoutStrategy), ('fakeout', FakeoutStrategy), 
                            ('bounce', BounceStrategy), ('trend', TrendStrategy)]

        for ticker in current_tickers:
            if self.db.is_ticker_in_cooldown(ticker): continue
            time.sleep(0.2) 

            for tf in self.timeframes:
                sorted_strats = sorted(strategy_classes, key=lambda x: f"{x[0]}_{tf}" == self.active_strategy_name, reverse=True)

                for name, StratClass in sorted_strats:
                    full_name = f"{name}_{tf}"
                    if self.db.has_open_trade(ticker, full_name, 'paper'): continue
                    if self.db.get_active_count_by_strategy(full_name, 'paper') >= self.slots_per_paper_strategy: continue

                    obj = StratClass(self.session, ticker, tf, self.db)
                    signal = obj.check_signal()
                    
                    if signal:
                        amount = self.calculate_position_size(signal['entry'], signal['sl'])
                        if amount <= 0: continue

                        # PAPER ENTRY
                        self.db.add_trade(ticker, full_name, 'paper', signal['signal'], signal['entry'], 
                                          signal['sl'], signal['tp'], signal.get('atr', 0), amount)
                        
                        side_icon = "🟢 LONG" if signal['signal'] == 'long' else "🔴 SHORT"
                        msg = (f"🧪 <b>PAPER: {ticker}</b> ({full_name})\n🧭 {side_icon}\n💰 Вход: {signal['entry']}\n🛑 Стоп: {signal['sl']}\n🎯 Тейк: {signal['tp']}\n📊 Номинал: ${amount}")
                        send_telegram_message(msg)

                        # LIVE ENTRY
                        if full_name == self.active_strategy_name and not self.live_trading_blocked:
                            if self.db.get_active_trades_count('live') < self.max_live_slots_total:
                                if not self.db.has_open_trade(ticker, None, 'live'):
                                    if self.place_live_order(ticker, signal['signal'], signal['entry'], signal['sl'], signal['tp'], amount):
                                        self.db.add_trade(ticker, full_name, 'live', signal['signal'], signal['entry'], 
                                                          signal['sl'], signal['tp'], signal.get('atr', 0), amount)
                                        send_telegram_message(f"🔥 <b>LIVE ВХОД ВЫПОЛНЕН: {ticker}</b>")

        logger.info("--- Цикл завершен ---")