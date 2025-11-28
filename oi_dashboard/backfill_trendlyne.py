"""
Backfill historical option chain data from Trendlyne SmartOptions API.
This populates the OptionChainData table with today's historical data.
"""
import requests
import time
from datetime import datetime, timedelta
from database import SessionLocal, Stock, OptionChainData

# Keep a cache to avoid repeated API calls
STOCK_ID_CACHE = {}

def get_stock_id_for_symbol(symbol):
    """Automatically lookup Trendlyne stock ID for a given symbol"""
    # Check cache first
    if symbol in STOCK_ID_CACHE:
        return STOCK_ID_CACHE[symbol]
    
    # Search API
    search_url = "https://smartoptions.trendlyne.com/phoenix/api/search-contract-stock/"
    params = {'query': symbol.lower()}
    
    try:
        print(f"Looking up stock ID for {symbol}...")
        response = requests.get(search_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        print(data['body'])
        
        # The API returns a list of matches
        # Usually the first result is the exact match
        if data and len(data['body']['data']) > 0:
            stock_id = data['body']['data'][0]['stock_id']
            if stock_id:
                STOCK_ID_CACHE[symbol] = stock_id
                print(f"[OK] Found stock ID {stock_id} for {symbol}")
                return stock_id
        
        print(f"[FAIL] Could not find stock ID for {symbol}")
        return None
        
    except Exception as e:
        print(f"[ERROR] Error looking up {symbol}: {e}")
        return None

def backfill_from_trendlyne(symbol, stock_id, expiry_date_str, min_time="09:15", max_time=None):
    """Fetch and save historical OI data from Trendlyne"""
    
    if max_time is None:
        max_time = datetime.now().strftime("%H:%M")
    
    url = f"https://smartoptions.trendlyne.com/phoenix/api/live-oi-data/"
    params = {
        'stockId': stock_id,
        'expDateList': expiry_date_str,  # Format: 2025-12-30
        'minTime': min_time,
        'maxTime': max_time
    }
    
    print(f"Fetching data for {symbol} (stockId={stock_id}, expiry={expiry_date_str})...")
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data['head']['status'] != '0':
            print(f"[ERROR] API error: {data['head'].get('statusDescription', 'Unknown error')}")
            return
        
        body = data['body']
        oi_data = body.get('oiData', {})
        input_data = body.get('inputData', {})
        
        # Get stock from database
        db = SessionLocal()
        try:
            stock = db.query(Stock).filter(Stock.symbol == symbol).first()
            if not stock:
                stock = Stock(symbol=symbol)
                db.add(stock)
                db.commit()
                db.refresh(stock)
            
            current_date = datetime.strptime(input_data['tradingDate'], "%Y-%m-%d").date()
            expiry_str = input_data['expDateList'][0]
            
            # Save data for each strike
            saved_count = 0
            for strike_price_str, strike_data in oi_data.items():
                strike_price = float(strike_price_str)
                
                # Use the current OI values as the "latest" snapshot
                call_oi = int(strike_data.get('callOi', 0))
                put_oi = int(strike_data.get('putOi', 0))
                call_oi_change = int(strike_data.get('callOiChange', 0))
                put_oi_change = int(strike_data.get('putOiChange', 0))
                
                entry = OptionChainData(
                    stock_id=stock.id,
                    date=current_date,
                    timestamp=max_time,
                    expiry_date=expiry_str,
                    strike_price=strike_price,
                    call_oi=call_oi,
                    call_oi_change=call_oi_change,
                    call_volume=0,
                    put_oi=put_oi,
                    put_oi_change=put_oi_change,
                    put_volume=0
                )
                db.add(entry)
                saved_count += 1
            
            db.commit()
            print(f"[OK] Saved {saved_count} strike records for {symbol}")
            
        finally:
            db.close()
            
    except Exception as e:
        print(f"[ERROR] Error fetching data for {symbol}: {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("Backfilling historical OI data from Trendlyne API")
    print("=" * 60)
    
    # Get all symbols from database
    db = SessionLocal()
    try:
        stocks = db.query(Stock).all()
        symbols = [s.symbol for s in stocks if s.symbol]
        print(f"Found {len(symbols)} symbols in database")
    finally:
        db.close()
    
    # Default expiry date (adjust as needed)
    default_expiry = '2025-12-30'
    
    successful = 0
    failed = 0
    # symbols = ['NIFTY','BANKNIFTY','FINNIFTY']
    for symbol in symbols:  
        # Skip if symbol is too generic
        if ' ' in symbol or len(symbol) > 15:
            continue
        
        # Look up stock ID
        stock_id = get_stock_id_for_symbol(symbol)
        if not stock_id:
            failed += 1
            continue
        
        expiryforSymbolURL = "https://smartoptions.trendlyne.com/phoenix/api/fno/get-expiry-dates/?mtype=options&stock_id=" + str(stock_id)
        expiryforSymbolResponse = requests.get(expiryforSymbolURL)
        expiryforSymbolData = expiryforSymbolResponse.json()
        expiryforSymbolData = expiryforSymbolData['body']['expiryDates']
        default_expiry = expiryforSymbolData[0]
#  response looks like       {
#     "head": {
#         "status": "0",
#         "statusDescription": "success",
#         "responseCode": null
#     },
#     "body": {
#         "expiryDates": [
#             "2025-12-30",
#             "2026-01-27",
#             "2026-02-24"
#         ],
#         "default_expiry_date": "2025-12-30",
#         "exchange": "NSE"
#     }
# }
        # Backfill
        backfill_from_trendlyne(symbol, stock_id, default_expiry)
        successful += 1
        
        time.sleep(0.5)  # Be polite to the API
    
    print("\n" + "=" * 60)
    print(f"[DONE] Backfill complete! {successful} successful, {failed} failed")
    print("=" * 60)
    print("Refresh your OI Change Chart tab to see the data.")
