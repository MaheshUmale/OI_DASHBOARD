from database import SessionLocal, Stock

def seed_stocks():
    db = SessionLocal()

    # List of stock symbols to seed
    symbols = ["NIFTY", "BANKNIFTY", "RELIANCE"]

    for symbol in symbols:
        # Check if the stock already exists
        exists = db.query(Stock).filter(Stock.symbol == symbol).first()
        if not exists:
            new_stock = Stock(symbol=symbol)
            db.add(new_stock)
            print(f"Adding {symbol} to the database.")
        else:
            print(f"{symbol} already exists in the database.")

    db.commit()
    db.close()

if __name__ == "__main__":
    seed_stocks()
