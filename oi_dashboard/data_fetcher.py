import requests
import json
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from database import engine, Stock, OIData, SessionLocal

def get_nse_cookies():
    baseurl = "https://www.nseindia.com/"
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36',
        'accept-language': 'en,gu;q=0.9,hi;q=0.8',
    }
    session = requests.Session()
    request = session.get(baseurl, headers=headers, timeout=5)
    cookies = dict(request.cookies)
    return cookies

def fetch_oi_data(symbol="NIFTY"):
    cookies = get_nse_cookies()
    if symbol in ["NIFTY", "BANKNIFTY"]:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    else:
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36',
        'accept-language': 'en,gu;q=0.9,hi;q=0.8',
        'referer': 'https://www.nseindia.com/market-data/option-chain'
    }
    response = requests.get(url, headers=headers, cookies=cookies)
    return response.json()

def process_and_save_oi_data(symbol, data):
    db = SessionLocal()
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if not stock:
        stock = Stock(symbol=symbol)
        db.add(stock)
        db.commit()
        db.refresh(stock)

    underlying_value = data['records']['underlyingValue']
    total_call_oi = data['filtered']['CE']['totOI']
    total_put_oi = data['filtered']['PE']['totOI']

    # Get the last record to calculate changes
    last_oi_data = db.query(OIData).filter(OIData.stock_id == stock.id).order_by(OIData.id.desc()).first()

    change_in_ltp = 0
    change_in_future_oi = 0
    change_in_call_oi = 0
    change_in_put_oi = 0

    if last_oi_data:
        change_in_ltp = underlying_value - last_oi_data.ltp
        change_in_call_oi = total_call_oi - last_oi_data.call_oi
        change_in_put_oi = total_put_oi - last_oi_data.put_oi

    oi_interpretation = ""
    if change_in_ltp > 0 and change_in_call_oi > 0:
        oi_interpretation = "Long Buildup"
    elif change_in_ltp < 0 and change_in_call_oi > 0:
        oi_interpretation = "Short Buildup"
    elif change_in_ltp < 0 and change_in_call_oi < 0:
        oi_interpretation = "Long Unwinding"
    elif change_in_ltp > 0 and change_in_call_oi < 0:
        oi_interpretation = "Short Covering"


    oi_data = OIData(
        stock_id=stock.id,
        date=datetime.now().date(),
        timestamp=datetime.now().strftime("%H:%M"),
        ltp=underlying_value,
        change_in_ltp=change_in_ltp,
        volume=data['filtered']['CE']['totVol'] + data['filtered']['PE']['totVol'],
        future_oi=0,  # This data is not in the option chain response
        change_in_future_oi=0,
        call_oi=total_call_oi,
        change_in_call_oi=change_in_call_oi,
        put_oi=total_put_oi,
        change_in_put_oi=change_in_put_oi,
        oi_interpretation=oi_interpretation,
        buy_sell_signal="" # Logic to be implemented
    )
    db.add(oi_data)
    db.commit()
    db.close()

if __name__ == "__main__":
    nifty_data = fetch_oi_data("NIFTY")
    process_and_save_oi_data("NIFTY", nifty_data)
    print("Data fetched and saved for NIFTY.")
