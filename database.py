import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

def get_uae_time():
    return datetime.utcnow() + timedelta(hours=4)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./scalper.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ScalpJournal(Base):
    __tablename__ = "scalp_journal"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=get_uae_time)
    pair = Column(String, default="XAU/USD")
    action = Column(String)  # BUY / SELL
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    lot_size = Column(Float)
    status = Column(String, default="ACTIVE")
    reason = Column(String)

def init_db():
    Base.metadata.create_all(bind=engine)
