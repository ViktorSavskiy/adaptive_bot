import time
from datetime import datetime, timedelta
from loguru import logger

from .database import DatabaseManager
from .strategies.breakout import BreakoutStrategy
from .strategies.bounce import BounceStrategy
from .strategies.trend import TrendStrategy
    # В начале файла orchestrator.py добавьте:
INITIAL_VIRTUAL_DEPOSIT = 90.0
class Orchestrator:
    def __init__(self, session, ticker_list):
        self.session = session
        self.db = DatabaseManager()
        self.all_tickers = ticker_list
        
        # Параметры управления
        self.max_live_slots = 3
        self.amount_per_slot = 30.0
        self.leverage = 5
        self.warmup_hours = 5
        
        # Состояние
        self.active_strategy_name = None
        self.is_kill_switch_active = False

    def get_market_tickers(self):
        """Фильтрация тикеров с объемом > 1 млн (как в твоем исходном коде)"""
        try:
            tickers_data = self.session.get_tickers(category="linear")['result']['list']
            filtered = [
                t['symbol'] for t in tickers_data 
                if float(t['turnover24h']) >= 1_000_000 
                and "-" not in t['symbol'] and "_" not in t['symbol']
            ]
            return filtered
        except Exception as e:
            logger.error(f"Ошибка обновления тикеров: {e}")
            return self.all_tickers

# Внутри класса Orchestrator обновите метод update_open_trades:
    def update_open_trades(self):
        session = self.db.Session()
        open_trades = session.query(self.db.Trade).filter(self.db.Trade.status == 'open').all()
        
        trades_closed_now = False
        
        for trade in open_trades:
            try:
                # Получаем текущие данные (Last Price)
                res = self.session.get_tickers(category="linear", symbol=trade.ticker)
                current_price = float(res['result']['list'][0]['lastPrice'])
                
                is_closed = False
                exit_price = 0
                
                # Проверка условий LONG
                if trade.side == 'long':
                    if current_price >= trade.take_profit:
                        is_closed, exit_price = True, trade.take_profit
                    elif current_price <= trade.stop_loss:
                        is_closed, exit_price = True, trade.stop_loss
                
                # Проверка условий SHORT
                elif trade.side == 'short':
                    if current_price <= trade.take_profit:
                        is_closed, exit_price = True, trade.take_profit
                    elif current_price >= trade.stop_loss:
                        is_closed, exit_price = True, trade.stop_loss

                if is_closed:
                    # Расчет PnL: (разница в %) * объем * плечо
                    price_diff_pct = (exit_price - trade.entry_price) / trade.entry_price
                    if trade.side == 'short':
                        price_diff_pct = -price_diff_pct
                    
                    pnl = price_diff_pct * trade.amount_usd * self.leverage
                    self.db.close_trade(trade.id, exit_price, pnl)
                    trades_closed_now = True
                    
                    logger.success(f"✅ ЗАКРЫТА {trade.trade_type.upper()} сделка по {trade.ticker}")
                    logger.info(f"   Стратегия: {trade.strategy_name} | PnL: ${pnl:.2f}")

            except Exception as e:
                logger.error(f"Ошибка проверки сделки {trade.ticker}: {e}")
        
        session.close()

        # Если в этом цикле были закрыты сделки — выводим баланс
        if trades_closed_now:
            total_pnl = self.db.get_total_paper_pnl()
            current_balance = INITIAL_VIRTUAL_DEPOSIT + total_pnl
            logger.info("=" * 40)
            logger.success(f"💰 ТЕКУЩИЙ ВИРТУАЛЬНЫЙ БАЛАНС: ${current_balance:.2f}")
            logger.info(f"   Общий профит/убыток: {total_pnl:+.2f}")
            logger.info("=" * 40)

    def select_best_strategy(self):
        """Выбор стратегии с наибольшим профитом за последние 5 часов"""
        strategies = ['breakout', 'bounce', 'trend']
        performance = {}
        
        for name in strategies:
            pnl = self.db.get_strategy_performance(name, hours=self.warmup_hours)
            performance[name] = pnl
            logger.info(f"Профит стратегии {name} за {self.warmup_hours}ч: ${pnl:.2f}")
        
        # Находим лучшую
        best_strat = max(performance, key=performance.get)
        
        # Если лучшая стратегия прибыльна, выбираем её
        if performance[best_strat] > 0:
            self.active_strategy_name = best_strat
        else:
            self.active_strategy_name = None # Пока нет прибыльных, не торгуем в реале

    def run_cycle(self):
        """Один цикл работы бота"""
        logger.info("--- Новый цикл анализа ---")
        
        # 1. Обновляем тикеры
        current_tickers = self.get_market_tickers()
        
        # 2. Проверяем открытые позиции (закрываем по TP/SL)
        self.update_open_trades()
        
        # 3. Проверяем Kill Switch
        if self.db.check_kill_switch(max_losses=5):
            logger.warning("!!! KILL SWITCH АКТИВИРОВАН. Все стратегии убыточны. Реальная торговля остановлена !!!")
            self.is_kill_switch_active = True
        else:
            self.is_kill_switch_active = False

        # 4. Выбираем лучшую стратегию
        self.select_best_strategy()
        logger.info(f"Активная стратегия для реальной торговли: {self.active_strategy_name}")

        # 5. Сканируем рынок
        active_slots = self.db.get_active_slots_count()
        
        for ticker in current_tickers:
            # Инициализируем стратегии
            strats = {
                'breakout': BreakoutStrategy(self.session, ticker, "15", self.db),
                'bounce': BounceStrategy(self.session, ticker, "15", self.db),
                'trend': TrendStrategy(self.session, ticker, "15", self.db)
            }

            for name, strat_obj in strats.items():
                signal = strat_obj.check_signal()
                if not signal:
                    continue

                # А) Всегда открываем виртуальную сделку (Paper) для статистики
                self.db.add_trade(
                    ticker=ticker,
                    strategy=name,
                    trade_type='paper',
                    side=signal['signal'],
                    entry=signal['entry'],
                    sl=signal['sl'],
                    tp=signal['tp']
                )

                # Б) Если это лучшая стратегия и есть свободные слоты — открываем REAL
                if (name == self.active_strategy_name and 
                    not self.is_kill_switch_active and 
                    active_slots < self.max_live_slots):
                    
                    # Проверяем, нет ли уже открытой реальной сделки по этому тикеру
                    # (чтобы не дублировать)
                    # Здесь должен быть вызов API Bybit для реальной покупки:
                    # self.session.place_order(...)
                    
                    self.db.add_trade(
                        ticker=ticker,
                        strategy=name,
                        trade_type='live',
                        side=signal['signal'],
                        entry=signal['entry'],
                        sl=signal['sl'],
                        tp=signal['tp']
                    )
                    active_slots += 1
                    logger.success(f"ОТКРЫТА РЕАЛЬНАЯ СДЕЛКА: {ticker} ({name})")

        logger.info("Цикл завершен. Ожидание...")