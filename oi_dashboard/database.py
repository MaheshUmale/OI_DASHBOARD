from sqlalchemy import create_engine, Column, Integer, String, Float, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///oi_data.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Stock(Base):
    __tablename__ = "stocks"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, unique=True, index=True)

class OIData(Base):
    __tablename__ = "oi_data"
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, index=True)
    date = Column(Date)
    timestamp = Column(String)
    ltp = Column(Float)
    change_in_ltp = Column(Float)
    volume = Column(Integer)
    future_oi = Column(Integer)
    change_in_future_oi = Column(Integer)
    call_oi = Column(Integer)
    change_in_call_oi = Column(Integer)
    put_oi = Column(Integer)
    change_in_put_oi = Column(Integer)
    oi_interpretation = Column(String)
    buy_sell_signal = Column(String)

def init_db():
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    init_db()
