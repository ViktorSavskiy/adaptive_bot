import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from loguru import logger

from .strategies.fakeout import FakeoutStrategy
from .strategies.breakout import BreakoutStrategy
from .strategies.bounce import BounceStrategy
from .strategies.trend import TrendStrategy
from .database import DatabaseManager
from .utils.telegram_notify import send_telegram_message

class Orchestrator:
    def __init__(self, session, ticker_list, db_path="data/trade_bot.db", is_backtest=False, start_time=None, params=None):
        self.session = session
        self.db = DatabaseManager(db_path)
        self.all_tickers = ticker_list
        self.ws = None 
        self.is_backtest = is_backtest
        self._sim_time = start_time 
        self.params = params or {} 
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(5)
        self.last_http = {}

        last_reset = self.db.get_last_reset_time()
        self.cycle_start_time = start_time if is_backtest else (last_reset or self.get_now())
        if not is_backtest and not last_reset:
            self.db.save_reset_time(self.cycle_start_time)

        self.cycle_duration_hours = 24  
        self.active_portfolio = {}      
        self.market_sentiment = 0       
        self.live_trading_blocked = False
        self.initial_virtual_deposit = 68.0
        self.risk_per_trade = 0.02      
        self.max_leverage = 3    
        self.max_order_usd_limit = 40.0 
        self.max_live_slots_total = 5   
        self.timeframes = ["15", "60"]
        self.select_best_strategy_extended()

    def get_now(self):
        dt = self._sim_time if self.is_backtest and self._sim_time else datetime.now(timezone.utc)
        return dt.replace(tzinfo=None) if hasattr(dt, 'tzinfo') and dt.tzinfo else dt

    def set_sim_time(self, new_time):
        self._sim_time = new_time.replace(tzinfo=None) if new_time and hasattr(new_time, 'tzinfo') else new_time

    def format_step(self, value, step):
        return float(Decimal(str(value)).quantize(Decimal(str(step)), rounding=ROUND_DOWN))

    def get_balances(self):
        if self.is_backtest: return {'equity': 1000.0, 'available': 1000.0}
        try:
            res = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            acc = res['result']['list'][0]
            equity = float(acc.get('totalEquity', self.initial_virtual_deposit) or self.initial_virtual_deposit)
            available = 0.0
            for c in acc.get('coin', []):
                if c['coin'] == 'USDT':
                    available = float(c.get('availableToWithdraw', 0) or 0)
                    break
            return {'equity': equity, 'available': available}
        except: return {'equity': self.initial_virtual_deposit, 'available': 0.0}

    def calculate_position_size(self, entry, sl):
        bal = self.get_balances()
        risk_usd = bal['equity'] * self.risk_per_trade
        dist = abs(entry - sl) / entry
        if dist < 0.001: return 0
        ideal = risk_usd / dist
        max_margin = (bal['equity'] / self.max_live_slots_total) * self.max_leverage
        return round(min(ideal, max_margin, self.max_order_usd_limit), 2)

    def select_best_strategy_extended(self):
        all_strats = [f"{n}_{tf}" for tf in self.timeframes for n in ['breakout', 'bounce', 'trend', 'fakeout']]
        pf_min = self.params.get('pf_min', 1.3)
        scored_strats = []
        for hours in [12, 24, 48]:
            scored_strats = []
            for s in all_strats:
                stats = self.db.get_detailed_stats(s, hours=hours, current_time=self.get_now())
                if stats['count'] >= 3 and stats['pf'] >= pf_min and stats['pnl'] > 0:
                    scored_strats.append({'name': s, 'score': stats['pnl'] * stats['pf'] * (stats['wr'] / 100)})
            if len(scored_strats) >= 2: break
        scored_strats.sort(key=lambda x: x['score'], reverse=True)
        self.active_portfolio = {}
        if len(scored_strats) >= 1: self.active_portfolio[scored_strats[0]['name']] = 3
        if len(scored_strats) >= 2: self.active_portfolio[scored_strats[1]['name']] = 1
        if len(scored_strats) >= 3: self.active_portfolio[scored_strats[2]['name']] = 1
        if self.active_portfolio: logger.info(f"💼 ПОРТФЕЛЬ: {self.active_portfolio}")

    def get_market_sentiment(self):
        try:
            res = self.session.get_kline(category="linear", symbol="BTCUSDT", interval="60", limit=60)
            klines = res.get('result', {}).get('list', [])
            if not klines: return 0
            closes = [float(k[4]) for k in klines[::-1]]
            sma = sum(closes[-50:]) / 50
            return 1 if closes[-1] > sma * 1.0002 else (-1 if closes[-1] < sma * 0.9998 else 0)
        except: return 0

    async def run_parallel_scan(self):
        now = self.get_now()
        if (now - self.cycle_start_time).total_seconds() > self.cycle_duration_hours * 3600:
            async with self.lock:
                self.select_best_strategy_extended()
                self.cycle_start_time = now
                self.db.save_reset_time(now)
                self.live_trading_blocked = False

        if not self.live_trading_blocked:
            daily_pnl = await asyncio.to_thread(self.db.get_live_daily_pnl, self.cycle_start_time)
            if daily_pnl <= -5.0:
                self.live_trading_blocked = True
                send_telegram_message(f"🚨 <b>LIVE STOP</b>: Убыток за день ${daily_pnl:.2f}.")

        self.market_sentiment = await asyncio.to_thread(self.get_market_sentiment)
        current_tickers = await asyncio.to_thread(self.get_market_tickers)
        strategy_map = {'breakout': BreakoutStrategy, 'fakeout': FakeoutStrategy, 'bounce': BounceStrategy, 'trend': TrendStrategy}
        tasks = [self._throttled_scan(t, strategy_map) for t in current_tickers]
        await asyncio.gather(*tasks)
        logger.info(f"✅ Скан завершен в {now.strftime('%H:%M:%S')}")

    async def _throttled_scan(self, ticker, strategy_map):
        async with self.semaphore:
            await asyncio.sleep(0.1) 
            for tf in self.timeframes: await self.process_ticker_tf(ticker, tf, strategy_map)

    async def process_ticker_tf(self, ticker, tf, strategy_map):
        if self.db.is_ticker_in_cooldown(ticker, current_time=self.get_now()): return
        for name, StratClass in strategy_map.items():
            full_name = f"{name}_{tf}"
            if await asyncio.to_thread(self.db.has_recent_trade, ticker, full_name, 15): continue
            obj = StratClass(self.session, ticker, tf, self.db, is_backtest=self.is_backtest, params=self.params)
            signal = await asyncio.to_thread(obj.check_signal)
            if signal:
                async with self.lock:
                    if not self.db.has_recent_trade(ticker, full_name, 1):
                        await asyncio.to_thread(self.handle_signal_logic, ticker, full_name, signal)

    def handle_signal_logic(self, ticker, full_name, signal):
        amount = self.calculate_position_size(signal['entry'], signal['sl'])
        if amount <= 0: return
        self.db.add_trade(ticker, full_name, 'paper', signal['signal'], signal['entry'], signal['sl'], signal['tp'], signal.get('atr', 0), amount, current_time=self.get_now())
        if full_name in self.active_portfolio and not self.live_trading_blocked:
            if ticker != "BTCUSDT":
                if self.market_sentiment == 1 and signal['signal'] == 'short': return
                if self.market_sentiment == -1 and signal['signal'] == 'long': return
            if self.db.get_active_trades_count('live') < self.max_live_slots_total:
                if self.db.get_active_count_by_strategy(full_name, 'live') < self.active_portfolio[full_name]:
                    if not self.db.has_open_trade(ticker, None, 'live'):
                        if self.place_live_order(ticker, signal['signal'], signal['entry'], signal['sl'], signal['tp'], amount):
                            self.db.add_trade(ticker, full_name, 'live', signal['signal'], signal['entry'], signal['sl'], signal['tp'], signal.get('atr', 0), amount, current_time=self.get_now())
                            logger.info(f"🔥 LIVE OPEN: {ticker} ({full_name})")
                            send_telegram_message(f"🚀 <b>LIVE ВХОД</b>\n{ticker} ({full_name})\n{signal['signal'].upper()}")

    def update_open_trades_ws(self):
        session_db = self.db.Session()
        try:
            open_trades = session_db.query(self.db.Trade).filter(self.db.Trade.status == 'open').all()
            now = self.get_now()
            for trade in open_trades:
                price = self.ws.get_last_price(trade.ticker) if self.ws else None
                if price is None:
                    last_req = self.last_http.get(trade.ticker, 0)
                    if time.time() - last_req > 30:
                        try:
                            res = self.session.get_tickers(category="linear", symbol=trade.ticker)
                            price = float(res['result']['list'][0]['lastPrice'])
                            self.last_http[trade.ticker] = time.time()
                            logger.debug(f"🔄 Цена {trade.ticker} получена через HTTP")
                        except: continue
                    else: continue
                if not trade.is_breakeven and trade.atr_at_entry and trade.atr_at_entry > 0:
                    trigger = trade.atr_at_entry * 2.0
                    if (trade.side == 'long' and price >= (trade.entry_price + trigger)) or (trade.side == 'short' and price <= (trade.entry_price - trigger)):
                        trade.stop_loss, trade.is_breakeven = trade.entry_price, True
                        if trade.trade_type == 'live' and not self.is_backtest: self.modify_live_stop_loss(trade.ticker, trade.entry_price)
                ttl = 8 if "15" in trade.strategy_name else 24
                if (now - trade.created_at.replace(tzinfo=None)).total_seconds() > ttl * 3600:
                    self.close_and_notify(trade, price, "TTL Exit")
                    continue
                is_closed = False
                if trade.side == 'long':
                    if price >= trade.take_profit or price <= trade.stop_loss: is_closed = True
                else:
                    if price <= trade.take_profit or price >= trade.stop_loss: is_closed = True
                if is_closed: self.close_and_notify(trade, price, "Target/Stop")
            session_db.commit()
        except Exception as e: logger.error(f"WS Error: {e}")
        finally: session_db.close()

    def modify_live_stop_loss(self, ticker, new_sl):
        try:
            info = self.session.get_instruments_info(category="linear", symbol=ticker)['result']['list'][0]
            sl_f = self.format_step(new_sl, info['priceFilter']['tickSize'])
            self.session.set_trading_stop(category="linear", symbol=ticker, stopLoss=str(sl_f), slTriggerBy="LastPrice", tpslMode="Full")
        except: pass

    def place_live_order(self, ticker, side, entry, sl, tp, amount_usd):
        if self.is_backtest: return True
        try:
            info = self.session.get_instruments_info(category="linear", symbol=ticker)['result']['list'][0]
            qty = self.format_step(amount_usd / entry, info['lotSizeFilter']['qtyStep'])
            if qty < float(info['lotSizeFilter']['minOrderQty']): return False
            try: self.session.set_leverage(category="linear", symbol=ticker, buyLeverage=str(self.max_leverage), sellLeverage=str(self.max_leverage))
            except: pass
            res = self.session.place_order(
                category="linear", symbol=ticker, side="Buy" if side == "long" else "Sell",
                orderType="Market", qty=str(qty), takeProfit=str(self.format_step(tp, info['priceFilter']['tickSize'])),
                stopLoss=str(self.format_step(sl, info['priceFilter']['tickSize'])), tpslMode="Full", isLeverage=1
            )
            return res['retCode'] == 0
        except Exception as e: return False

    def close_and_notify(self, trade, price, reason):
        if trade.trade_type == 'live' and not self.is_backtest: self.close_live_position(trade.ticker, trade.side)
        pnl = self.calculate_pnl_simple(trade, price)
        self.db.close_trade(trade.id, price, pnl, current_time=self.get_now())
        if trade.trade_type == 'live':
            icon = "💰" if pnl > 0 else "📉"
            send_telegram_message(f"{icon} <b>LIVE ЗАКРЫТ</b>\n{trade.ticker}\nPnL: ${pnl:+.2f}\n{reason}")
        logger.info(f"✅ CLOSED {trade.ticker} ({trade.trade_type}): {pnl}$ | {reason}")

    def calculate_pnl_simple(self, trade, exit_price):
        diff = (exit_price - trade.entry_price) / trade.entry_price
        if trade.side == 'short': diff = -diff
        return round((diff * trade.amount_usd) - (trade.amount_usd * 0.0012), 4)

    def get_market_tickers(self):
        try:
            res = self.session.get_tickers(category="linear")
            blacklist = ['DOLOUSDT', 'DEGENUSDT', 'DEFIUSDT', 'BUSDT', 'ARBUSDT', 'FILUSDT']
            return [t['symbol'] for t in res['result']['list'] if t['symbol'].endswith('USDT') and float(t['turnover24h']) > 20_000_000 and t['symbol'] not in blacklist]
        except: return self.all_tickers

    def close_live_position(self, ticker, side):
        try:
            res = self.session.get_positions(category="linear", symbol=ticker)
            pos = res.get('result', {}).get('list', [])
            if pos and float(pos[0].get('size', 0)) > 0:
                self.session.place_order(category="linear", symbol=ticker, side="Sell" if side=="long" else "Buy", orderType="Market", qty=pos[0]['size'], reduceOnly=True)
                return True
            return False
        except: return False