import os
from datetime import datetime, timedelta
from sqlalchemy import create_all, Column, Integer, String, Float, DateTime, Boolean, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

# Базовый класс для моделей
Base = declarative_base()

class Trade(Base):
    """Модель торговой сделки (и реальной, и виртуальной)"""
    __tablename__ = 'trades'

    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False)
    strategy_name = Column(String(50), nullable=False)  # breakout, bounce, trend
    trade_type = Column(String(10), nullable=False)    # 'live' или 'paper' (виртуальная)
    side = Column(String(10))                          # 'long' или 'short'
    
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    
    leverage = Column(Integer, default=5)
    amount_usd = Column(Float, default=30.0)           # Размер позиции в USD
    
    pnl_usd = Column(Float, default=0.0)               # Прибыль/убыток в долларах
    status = Column(String(20), default='open')        # 'open', 'closed'
    
    created_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime)

class DatabaseManager:
    def __init__(self, db_path="data/trade_bot.db"):
        # Создаем папку data, если её нет
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
     
     def get_total_paper_pnl(self):
        """Считает общую прибыль/убыток по всем закрытым виртуальным сделкам"""
        session = self.Session()
        # Суммируем PnL всех закрытых бумажных сделок
        total_pnl = session.query(self.db.sqlalchemy.func.sum(Trade.pnl_usd)).filter(
            Trade.trade_type == 'paper',
            Trade.status == 'closed'
        ).scalar()
        session.close()
        return total_pnl if total_pnl else 0.0

    def add_trade(self, ticker, strategy, trade_type, side, entry, sl, tp, amount=30.0):
        """Запись новой сделки в БД"""
        session = self.Session()
        new_trade = Trade(
            ticker=ticker,
            strategy_name=strategy,
            trade_type=trade_type,
            side=side,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            amount_usd=amount,
            status='open'
        )
        session.add(new_trade)
        session.commit()
        trade_id = new_trade.id
        session.close()
        return trade_id

    def close_trade(self, trade_id, exit_price, pnl):
        """Закрытие сделки с фиксацией результата"""
        session = self.Session()
        trade = session.query(Trade).filter(Trade.id == trade_id).first()
        if trade:
            trade.exit_price = exit_price
            trade.pnl_usd = pnl
            trade.status = 'closed'
            trade.closed_at = datetime.utcnow()
            session.commit()
        session.close()

    def get_strategy_performance(self, strategy_name, hours=5):
        """
        Получает профит стратегии за последние N часов.
        Используется оркестратором для выбора лучшей стратегии.
        """
        session = self.Session()
        since = datetime.utcnow() - timedelta(hours=hours)
        
        trades = session.query(Trade).filter(
            Trade.strategy_name == strategy_name,
            Trade.closed_at >= since,
            Trade.status == 'closed'
        ).all()
        
        total_pnl = sum(t.pnl_usd for t in trades)
        session.close()
        return total_pnl

    def check_kill_switch(self, max_losses=5):
        """
        Проверяет, нет ли у каждой стратегии серии из N убытков подряд.
        Возвращает True, если нужно остановить торговлю.
        """
        session = self.Session()
        strategies = ['breakout', 'bounce', 'trend']
        
        strike_counts = []
        for strat in strategies:
            # Берем последние 5 закрытых сделок каждой стратегии
            last_trades = session.query(Trade).filter(
                Trade.strategy_name == strat,
                Trade.status == 'closed'
            ).order_by(desc(Trade.closed_at)).limit(max_losses).all()
            
            # Считаем, сколько из них убыточные
            losses = [t for t in last_trades if t.pnl_usd < 0]
            strike_counts.append(len(losses))

        session.close()
        
        # Если у ВСЕХ стратегий последние 5 сделок были убыточными
        return all(count >= max_losses for count in strike_counts)

    def get_active_slots_count(self):
        """Считает количество открытых реальных сделок (макс 3)"""
        session = self.Session()
        count = session.query(Trade).filter(
            Trade.trade_type == 'live',
            Trade.status == 'open'
        ).count()
        session.close()
        return count