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
    max_pain = Column(Float)
    buy_sell_signal = Column(String)

class OptionChainData(Base):
    """Stores per-strike option chain data for historical analysis."""
    __tablename__ = "option_chain_data"
    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, index=True)
    date = Column(Date, index=True)
    timestamp = Column(String)
    expiry_date = Column(String, index=True)  # Expiry date of the option
    strike_price = Column(Float, index=True)
    call_oi = Column(Integer)
    call_oi_change = Column(Integer)
    call_volume = Column(Integer)
    put_oi = Column(Integer)
    put_oi_change = Column(Integer)
    put_volume = Column(Integer)


class Meta(Base):
    """Simple key/value table for storing small bits of state (like last scan index)."""
    __tablename__ = 'meta'
    key = Column(String, primary_key=True, index=True)
    value = Column(String)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Ensure any new columns added to models are present in existing SQLite tables.
    # SQLite supports ADD COLUMN for simple migrations; this keeps the project lightweight.
    conn = engine.connect()
    try:
        def _has_column(table, column):
            res = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            existing = [r[1] for r in res]
            return column in existing

        # Ensure `max_pain` exists on `oi_data`
        if not _has_column('oi_data', 'max_pain'):
            try:
                conn.execute("ALTER TABLE oi_data ADD COLUMN max_pain FLOAT")
            except Exception:
                pass

        # Ensure `buy_sell_signal` exists on `oi_data`
        if not _has_column('oi_data', 'buy_sell_signal'):
            try:
                conn.execute("ALTER TABLE oi_data ADD COLUMN buy_sell_signal TEXT")
            except Exception:
                pass
    finally:
        conn.close()


def get_meta(session, key, default=None):
    item = session.query(Meta).filter(Meta.key == key).first()
    if not item:
        return default
    return item.value


def set_meta(session, key, value):
    item = session.query(Meta).filter(Meta.key == key).first()
    if not item:
        item = Meta(key=key, value=str(value))
        session.add(item)
    else:
        item.value = str(value)
    session.commit()

if __name__ == "__main__":
    init_db()
