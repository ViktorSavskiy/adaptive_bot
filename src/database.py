import os
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, desc, func, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from loguru import logger

Base = declarative_base()

class Trade(Base):
    __tablename__ = 'trades'
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False)
    strategy_name = Column(String(50), nullable=False, index=True)
    trade_type = Column(String(10), nullable=False, index=True) 
    side = Column(String(10))
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    atr_at_entry = Column(Float)
    is_breakeven = Column(Boolean, default=False)
    leverage = Column(Integer, default=3)
    amount_usd = Column(Float)
    pnl_usd = Column(Float, default=0.0)
    status = Column(String(20), default='open', index=True) 
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    closed_at = Column(DateTime)

class BotSettings(Base):
    __tablename__ = 'bot_settings'
    key = Column(String(50), primary_key=True)
    value_date = Column(DateTime)

Index('idx_strategy_closed', Trade.strategy_name, Trade.status, Trade.closed_at)

class DatabaseManager:
    def __init__(self, db_path="data/trade_bot.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.Trade = Trade

    def _get_now(self, current_time=None):
        dt = current_time if current_time else datetime.now(timezone.utc)
        return dt.replace(tzinfo=None) if hasattr(dt, 'tzinfo') and dt.tzinfo else dt

    def get_last_reset_time(self):
        session = self.Session()
        try:
            setting = session.query(BotSettings).filter(BotSettings.key == 'last_cycle_reset').first()
            return setting.value_date if setting else None
        finally: session.close()

    def save_reset_time(self, reset_time):
        session = self.Session()
        try:
            rt = reset_time.replace(tzinfo=None) if hasattr(reset_time, 'tzinfo') and reset_time.tzinfo else reset_time
            setting = session.query(BotSettings).filter(BotSettings.key == 'last_cycle_reset').first()
            if not setting:
                session.add(BotSettings(key='last_cycle_reset', value_date=rt))
            else:
                setting.value_date = rt
            session.commit()
        finally: session.close()

    def add_trade(self, ticker, strategy, trade_type, side, entry, sl, tp, atr_at_entry=None, amount=0.0, current_time=None):
        session = self.Session()
        try:
            new_trade = Trade(
                ticker=ticker, strategy_name=strategy, trade_type=trade_type,
                side=side, entry_price=entry, stop_loss=sl, take_profit=tp,
                atr_at_entry=atr_at_entry, amount_usd=amount, status='open',
                created_at=self._get_now(current_time)
            )
            session.add(new_trade)
            session.commit()
            return new_trade.id
        finally: session.close()

    def has_open_trade(self, ticker, strategy_name=None, trade_type='paper'):
        session = self.Session()
        try:
            query = session.query(Trade).filter(Trade.ticker == ticker, Trade.trade_type == trade_type, Trade.status == 'open')
            if strategy_name: query = query.filter(Trade.strategy_name == strategy_name)
            return query.first() is not None
        finally: session.close()

    def has_recent_trade(self, ticker, strategy_name, minutes=15):
        session = self.Session()
        try:
            exists = session.query(Trade).filter(Trade.ticker == ticker, Trade.strategy_name == strategy_name, Trade.status == 'open').first()
            if exists: return True
            since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)
            recent = session.query(Trade).filter(Trade.ticker == ticker, Trade.strategy_name == strategy_name, Trade.status == 'closed', Trade.closed_at >= since).first()
            return recent is not None
        finally: session.close()

    def get_active_trades_count(self, trade_type='paper'):
        session = self.Session()
        try: return session.query(Trade).filter(Trade.trade_type == trade_type, Trade.status == 'open').count()
        finally: session.close()

    def get_active_count_by_strategy(self, strategy_name, trade_type='paper'):
        session = self.Session()
        try: return session.query(Trade).filter(Trade.strategy_name == strategy_name, Trade.trade_type == trade_type, Trade.status == 'open').count()
        finally: session.close()

    def is_ticker_in_cooldown(self, ticker, current_time=None):
        session = self.Session()
        now = self._get_now(current_time)
        try:
            last = session.query(Trade).filter(Trade.ticker == ticker, Trade.status == 'closed').order_by(desc(Trade.closed_at)).first()
            if not last or not last.closed_at: return False
            cooldown = timedelta(hours=4) if last.pnl_usd < 0 else timedelta(hours=1)
            return (now - last.closed_at.replace(tzinfo=None)) < cooldown
        finally: session.close()

    def get_live_daily_pnl(self, since_time):
        session = self.Session()
        try:
            st = since_time.replace(tzinfo=None) if hasattr(since_time, 'tzinfo') and since_time.tzinfo else since_time
            res = session.query(func.sum(Trade.pnl_usd)).filter(Trade.trade_type == 'live', Trade.status == 'closed', Trade.closed_at >= st).scalar()
            return float(res) if res is not None else 0.0
        finally: session.close()

    def close_trade(self, trade_id, exit_price, pnl, current_time=None):
        session = self.Session()
        try:
            trade = session.query(Trade).filter(Trade.id == trade_id).first()
            if trade:
                trade.exit_price, trade.pnl_usd, trade.status = exit_price, pnl, 'closed'
                trade.closed_at = self._get_now(current_time)
                session.commit()
        finally: session.close()

    def get_detailed_stats(self, strategy_name, hours=24, current_time=None):
        session = self.Session()
        now = self._get_now(current_time)
        since = now - timedelta(hours=hours)
        try:
            trades = session.query(Trade).filter(Trade.strategy_name == strategy_name, Trade.status == 'closed', Trade.closed_at >= since).all()
            if not trades: return {'pnl': 0, 'pf': 0, 'wr': 0, 'count': 0}
            wins = [t.pnl_usd for t in trades if t.pnl_usd > 0]
            losses = [abs(t.pnl_usd) for t in trades if t.pnl_usd < 0]
            pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else (10.0 if sum(wins) > 0 else 0.0)
            return {'pnl': round(sum(t.pnl_usd for t in trades), 2), 'pf': round(pf, 2), 'wr': round((len(wins) / len(trades)) * 100, 1), 'count': len(trades)}
        finally: session.close()

    def check_consecutive_live_losses(self, limit=5, since_time=None):
        session = self.Session()
        try:
            query = session.query(Trade).filter(Trade.trade_type == 'live', Trade.status == 'closed')
            if since_time: query = query.filter(Trade.closed_at >= since_time.replace(tzinfo=None))
            last_trades = query.order_by(desc(Trade.closed_at)).limit(limit).all()
            return len(last_trades) >= limit and all(t.pnl_usd < 0 for t in last_trades)
        finally: session.close()