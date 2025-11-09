from flask import Flask, render_template, request
from database import SessionLocal, Stock, OIData, init_db
from data_fetcher import fetch_oi_data, process_and_save_oi_data
from seed import seed_stocks

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    db = SessionLocal()
    stocks = db.query(Stock).all()
    selected_stock_symbol = "NIFTY"
    if request.method == 'POST':
        selected_stock_symbol = request.form.get('stock_symbol')
        try:
            oi_data = fetch_oi_data(selected_stock_symbol)
            process_and_save_oi_data(selected_stock_symbol, oi_data)
        except Exception as e:
            print(f"Error fetching or saving data for {selected_stock_symbol}: {e}")

    selected_stock = db.query(Stock).filter(Stock.symbol == selected_stock_symbol).first()
    if selected_stock:
        data = db.query(OIData).filter(OIData.stock_id == selected_stock.id).all()
    else:
        data = []

    db.close()
    return render_template('index.html', stocks=stocks, selected_stock_symbol=selected_stock_symbol, data=data)

if __name__ == '__main__':
    init_db()
    seed_stocks()
    app.run(debug=True)
