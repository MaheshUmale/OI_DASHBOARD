import pandas as pd
from database import SessionLocal, Stock, OIData

def seed_from_csv():
    """
    Seeds the database with historical data from a CSV file.
    """
    db = SessionLocal()

    # Read the CSV file
    try:
        df = pd.read_csv('oi_dashboard/historical_data.csv')
    except FileNotFoundError:
        print("historical_data.csv not found. Skipping seeding.")
        return

    # Iterate over the rows of the dataframe
    for _, row in df.iterrows():
        # Get or create the stock
        stock = db.query(Stock).filter(Stock.symbol == row['symbol']).first()
        if not stock:
            stock = Stock(symbol=row['symbol'])
            db.add(stock)
            db.commit()
            db.refresh(stock)

        # Create a new OIData record
        oi_data = OIData(
            stock_id=stock.id,
            date=row['date'],
            timestamp=row['timestamp'],
            ltp=row['ltp'],
            change_in_ltp=row['change_in_ltp'],
            volume=row['volume'],
            future_oi=row['future_oi'],
            change_in_future_oi=row['change_in_future_oi'],
            call_oi=row['call_oi'],
            change_in_call_oi=row['change_in_call_oi'],
            put_oi=row['put_oi'],
            change_in_put_oi=row['change_in_put_oi'],
            oi_interpretation=row['oi_interpretation'],
            max_pain=row['max_pain'],
            buy_sell_signal=row['buy_sell_signal']
        )
        db.add(oi_data)

    db.commit()
    db.close()

if __name__ == '__main__':
    seed_from_csv()
