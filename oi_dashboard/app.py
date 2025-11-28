from flask import Flask, render_template, request
from database import SessionLocal, Stock, OIData, init_db
from data_fetcher import fetch_oi_data, process_and_save_oi_data
from seed import seed_stocks
from datetime import datetime, timedelta
import threading
import time

app = Flask(__name__)

# --- Background data fetching ---
def background_fetch():
    while True:
        db = SessionLocal()
        stocks = db.query(Stock).all()
        for stock in stocks:
            try:
                oi_data = fetch_oi_data(stock.symbol)
                process_and_save_oi_data(stock.symbol, oi_data)
                print(f"Successfully fetched and saved data for {stock.symbol}")
            except Exception as e:
                print(f"Error fetching data for {stock.symbol} in background: {e}")
        db.close()
        time.sleep(60)  # Fetch every 60 seconds

def get_rolling_oi_change(db, stock_id, minutes):
    time_threshold = datetime.now() - timedelta(minutes=minutes)

    first_record = db.query(OIData).filter(
        OIData.stock_id == stock_id,
        OIData.timestamp >= time_threshold
    ).order_by(OIData.timestamp.asc()).first()

    latest_record = db.query(OIData).filter(
        OIData.stock_id == stock_id
    ).order_by(OIData.timestamp.desc()).first()

    if first_record and latest_record:
        rolling_call_oi_change = latest_record.call_oi - first_record.call_oi
        rolling_put_oi_change = latest_record.put_oi - first_record.put_oi
        return rolling_call_oi_change, rolling_put_oi_change

    return 0, 0

@app.route('/', methods=['GET', 'POST'])
def index():
    db = SessionLocal()
    stocks = db.query(Stock).all()
    selected_stock_symbol = request.form.get('stock_symbol', "NIFTY")

    selected_stock = db.query(Stock).filter(Stock.symbol == selected_stock_symbol).first()
    if selected_stock:
        data = db.query(OIData).filter(OIData.stock_id == selected_stock.id).order_by(OIData.timestamp.desc()).all()
        rolling_3min_call_change, rolling_3min_put_change = get_rolling_oi_change(db, selected_stock.id, 3)
        rolling_5min_call_change, rolling_5min_put_change = get_rolling_oi_change(db, selected_stock.id, 5)
    else:
        data = []
        rolling_3min_call_change, rolling_3min_put_change = 0, 0
        rolling_5min_call_change, rolling_5min_put_change = 0, 0

    db.close()
    return render_template('index.html',
                           stocks=stocks,
                           selected_stock_symbol=selected_stock_symbol,
                           data=data,
                           rolling_3min_call_change=rolling_3min_call_change,
                           rolling_3min_put_change=rolling_3min_put_change,
                           rolling_5min_call_change=rolling_5min_call_change,
                           rolling_5min_put_change=rolling_5min_put_change)

if __name__ == '__main__':
    init_db()
    seed_stocks()

    # Start the background thread
    fetch_thread = threading.Thread(target=background_fetch, daemon=True)
    fetch_thread.start()

    app.run(debug=True)
