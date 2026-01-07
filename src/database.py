import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, desc, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class Trade(Base):
    """Модель торговой сделки"""
    __tablename__ = 'trades'
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False)
    strategy_name = Column(String(50), nullable=False)
    trade_type = Column(String(10), nullable=False) # 'live' или 'paper'
    side = Column(String(10))
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    atr_at_entry = Column(Float)
    is_breakeven = Column(Boolean, default=False)
    leverage = Column(Integer, default=3) # Плечо по умолчанию 3
    amount_usd = Column(Float)
    pnl_usd = Column(Float, default=0.0)
    status = Column(String(20), default='open') # 'open', 'closed'
    created_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime)

class BotSettings(Base):
    """Таблица для хранения времени сброса цикла"""
    __tablename__ = 'bot_settings'
    key = Column(String(50), primary_key=True)
    value_date = Column(DateTime)

class DatabaseManager:
    def __init__(self, db_path="data/trade_bot.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.Trade = Trade 

    # --- МЕТОДЫ ДЛЯ ТАЙМИНГА ЦИКЛА ---

    def get_last_reset_time(self):
        """Получает время последнего сброса 24-часового цикла"""
        session = self.Session()
        setting = session.query(BotSettings).filter(BotSettings.key == 'last_cycle_reset').first()
        session.close()
        return setting.value_date if setting else None

    def save_reset_time(self, reset_time):
        """Сохраняет время сброса цикла"""
        session = self.Session()
        setting = session.query(BotSettings).filter(BotSettings.key == 'last_cycle_reset').first()
        if not setting:
            setting = BotSettings(key='last_cycle_reset', value_date=reset_time)
            session.add(setting)
        else:
            setting.value_date = reset_time
        session.commit()
        session.close()

    # --- МЕТОДЫ ТОРГОВЛИ ---

    def add_trade(self, ticker, strategy, trade_type, side, entry, sl, tp, atr_at_entry=None, amount=0.0):
        session = self.Session()
        new_trade = Trade(
            ticker=ticker, strategy_name=strategy, trade_type=trade_type,
            side=side, entry_price=entry, stop_loss=sl, take_profit=tp,
            atr_at_entry=atr_at_entry, amount_usd=amount, status='open'
        )
        session.add(new_trade)
        session.commit()
        tid = new_trade.id
        session.close()
        return tid

    def get_active_trades_count(self, trade_type='paper'):
        session = self.Session()
        count = session.query(Trade).filter(Trade.trade_type == trade_type, Trade.status == 'open').count()
        session.close()
        return count

    def get_active_count_by_strategy(self, strategy_name, trade_type='paper'):
        session = self.Session()
        count = session.query(Trade).filter(
            Trade.strategy_name == strategy_name, 
            Trade.trade_type == trade_type, 
            Trade.status == 'open'
        ).count()
        session.close()
        return count

    def is_ticker_in_cooldown(self, ticker):
        """Умный кулдаун: убыток - 4ч, профит - 1ч"""
        session = self.Session()
        last_trade = session.query(Trade).filter(Trade.ticker == ticker, Trade.status == 'closed').order_by(desc(Trade.closed_at)).first()
        if not last_trade:
            session.close()
            return False
        
        time_passed = datetime.utcnow() - last_trade.closed_at
        cooldown = timedelta(hours=4) if last_trade.pnl_usd < 0 else timedelta(hours=1)
        res = time_passed < cooldown
        session.close()
        return res

    def has_open_trade(self, ticker, strategy_name=None, trade_type='paper'):
        session = self.Session()
        query = session.query(Trade).filter(Trade.ticker == ticker, Trade.trade_type == trade_type, Trade.status == 'open')
        if strategy_name:
            query = query.filter(Trade.strategy_name == strategy_name)
        exists = query.first()
        session.close()
        return exists is not None

    def close_all_open_trades(self, trade_type='live'):
        session = self.Session()
        open_trades = session.query(Trade).filter(Trade.trade_type == trade_type, Trade.status == 'open').all()
        for t in open_trades:
            t.status = 'closed'
            t.closed_at = datetime.utcnow()
        session.commit()
        count = len(open_trades)
        session.close()
        return count

    def check_consecutive_live_losses(self, limit=3):
        session = self.Session()
        last_trades = session.query(Trade).filter(Trade.trade_type == 'live', Trade.status == 'closed').order_by(desc(Trade.closed_at)).limit(limit).all()
        session.close()
        if len(last_trades) < limit: return False
        return all(t.pnl_usd < 0 for t in last_trades)

    def close_trade(self, trade_id, exit_price, pnl):
        session = self.Session()
        trade = session.query(Trade).filter(Trade.id == trade_id).first()
        if trade:
            trade.exit_price, trade.pnl_usd, trade.status, trade.closed_at = exit_price, pnl, 'closed', datetime.utcnow()
            session.commit()
        session.close()

    def get_total_paper_pnl(self):
        session = self.Session()
        res = session.query(func.sum(Trade.pnl_usd)).filter(Trade.trade_type == 'paper', Trade.status == 'closed').scalar()
        session.close()
        return res if res else 0.0

    def get_strategy_performance(self, strategy_name, hours=24):
        session = self.Session()
        since = datetime.utcnow() - timedelta(hours=hours)
        trades = session.query(Trade).filter(Trade.strategy_name == strategy_name, Trade.closed_at >= since, Trade.status == 'closed').all()
        res = sum(t.pnl_usd for t in trades)
        session.close()
        return res

    def check_kill_switch(self, max_losses=5):
        session = self.Session()
        strats = [r[0] for r in session.query(Trade.strategy_name).distinct().all()]
        strike_results = []
        for s in strats:
            last_trades = session.query(Trade).filter(Trade.strategy_name == s, Trade.status == 'closed').order_by(desc(Trade.closed_at)).limit(max_losses).all()
            if len(last_trades) >= max_losses:
                strike_results.append(all(t.pnl_usd < 0 for t in last_trades))
        session.close()
        return all(strike_results) if strike_results else False

    def get_detailed_stats(self, strategy_name, hours=24):
        """Возвращает детальную статистику стратегии для умного выбора"""
        session = self.Session()
        since = datetime.utcnow() - timedelta(hours=hours)
        trades = session.query(self.Trade).filter(
            self.Trade.strategy_name == strategy_name,
            self.Trade.status == 'closed',
            self.Trade.closed_at >= since
        ).all()
        session.close()

        if not trades:
            return {'pnl': 0, 'pf': 0, 'wr': 0, 'count': 0}

        total_pnl = sum(t.pnl_usd for t in trades)
        wins = [t.pnl_usd for t in trades if t.pnl_usd > 0]
        losses = [abs(t.pnl_usd) for t in trades if t.pnl_usd < 0]
        
        count = len(trades)
        win_rate = (len(wins) / count) * 100
        
        sum_wins = sum(wins)
        sum_losses = sum(losses)
        # Profit Factor: Профит / Убыток
        profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else (sum_wins if sum_wins > 0 else 0)

        return {
            'pnl': total_pnl,
            'pf': profit_factor,
            'wr': win_rate,
            'count': count
        }