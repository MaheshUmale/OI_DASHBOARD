import requests
import json
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from database import engine, Stock, OIData, SessionLocal
import time

# Simple in-memory cache with TTL
cache = {}
CACHE_TTL = 60  # 60 seconds

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
    now = time.time()
    if symbol in cache and (now - cache[symbol]['timestamp']) < CACHE_TTL:
        return cache[symbol]['data']

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
    response.raise_for_status()
    data = response.json()
    cache[symbol] = {'timestamp': now, 'data': data}
    return data

def process_and_save_oi_data(symbol, data):
    db = SessionLocal()
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if not stock:
        stock = Stock(symbol=symbol)
        db.add(stock)
        db.commit()
        db.refresh(stock)

    underlying_value = data['records']['underlyingValue']

    # Current expiry OI (from filtered data)
    total_call_oi = data['filtered']['CE']['totOI']
    total_put_oi = data['filtered']['PE']['totOI']

    # Calculate OI for the next expiry
    expiry_dates = data['records']['expiryDates']
    next_expiry_date = None
    if len(expiry_dates) > 1:
        next_expiry_date = expiry_dates[1]

    prev_expiry_call_oi = 0
    prev_expiry_put_oi = 0
    if next_expiry_date:
        for record in data['records']['data']:
            if record['expiryDate'] == next_expiry_date:
                if 'CE' in record and 'openInterest' in record['CE']:
                    prev_expiry_call_oi += record['CE']['openInterest']
                if 'PE' in record and 'openInterest' in record['PE']:
                    prev_expiry_put_oi += record['PE']['openInterest']

    # Get the last record to calculate changes
    last_oi_data = db.query(OIData).filter(OIData.stock_id == stock.id).order_by(OIData.id.desc()).first()

    change_in_ltp = 0
    change_in_call_oi = 0
    change_in_put_oi = 0
    change_in_prev_expiry_call_oi = 0
    change_in_prev_expiry_put_oi = 0

    if last_oi_data:
        change_in_ltp = underlying_value - last_oi_data.ltp
        change_in_call_oi = total_call_oi - last_oi_data.call_oi
        change_in_put_oi = total_put_oi - last_oi_data.put_oi
        change_in_prev_expiry_call_oi = prev_expiry_call_oi - (last_oi_data.prev_expiry_call_oi or 0)
        change_in_prev_expiry_put_oi = prev_expiry_put_oi - (last_oi_data.prev_expiry_put_oi or 0)

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
        timestamp=datetime.now(),
        ltp=underlying_value,
        change_in_ltp=change_in_ltp,
        volume=data['filtered']['CE']['totVol'] + data['filtered']['PE']['totVol'],
        future_oi=0,
        change_in_future_oi=0,
        call_oi=total_call_oi,
        change_in_call_oi=change_in_call_oi,
        put_oi=total_put_oi,
        change_in_put_oi=change_in_put_oi,
        prev_expiry_call_oi=prev_expiry_call_oi,
        change_in_prev_expiry_call_oi=change_in_prev_expiry_call_oi,
        prev_expiry_put_oi=prev_expiry_put_oi,
        change_in_prev_expiry_put_oi=change_in_prev_expiry_put_oi,
        oi_interpretation=oi_interpretation,
        buy_sell_signal=""
    )
    db.add(oi_data)
    db.commit()
    db.close()

if __name__ == "__main__":
    nifty_data = fetch_oi_data("NIFTY")
    process_and_save_oi_data("NIFTY", nifty_data)
    print("Data fetched and saved for NIFTY.")
