import requests
import json
import time
import random
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from database import engine, Stock, OIData, SessionLocal
import logging

logger = logging.getLogger('oi_dashboard.data_fetcher')

# Module-level session to reuse connections
_SESSION = requests.Session()


def requests_get_with_retry(url, headers=None, cookies=None, max_retries=4, backoff_factor=1.0, timeout=10):
    """GET with retries, exponential backoff and jitter.

    Returns requests.Response on success or raises the last exception on failure.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = _SESSION.get(url, headers=headers, cookies=cookies, timeout=timeout)
            # treat 200 as success; other status codes we may want to retry on
            if resp.status_code == 200:
                return resp
            else:
                # raise HTTPError to trigger retry logic below
                resp.raise_for_status()
        except Exception as exc:
            last_exc = exc
            sleep_seconds = backoff_factor * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            # brief log
            logger.warning(f"Request to {url} failed (attempt {attempt}/{max_retries}): {exc}. Retrying in {sleep_seconds:.1f}s")
            time.sleep(sleep_seconds)
            continue
    # if we get here, raise last exception
    raise last_exc

def get_nse_cookies():
    """Fetch NSE cookies by visiting the homepage."""
    try:
        # Send a GET request to the NSE homepage
        response = _SESSION.get("https://www.nseindia.com", timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes

        # Extract cookies from the response
        cookies = response.cookies.get_dict()

        return cookies
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching NSE cookies: {e}")
        return {}

def is_market_open():
    """Check if the market is open."""
    now = datetime.now()
    market_open = datetime.strptime("09:15", "%H:%M").time()
    market_close = datetime.strptime("15:30", "%H:%M").time()
    return market_open <= now.time() <= market_close and now.weekday() < 5

def fetch_oi_data(symbol="NIFTY"):
    cookies = get_nse_cookies()
    if symbol in ["NIFTY", "BANKNIFTY"]:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    else:
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

    headers = {
        'Host': 'www.nseindia.com',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.nseindia.com/market-data/option-chain'
    }
    resp = requests_get_with_retry(url, headers=headers, cookies=cookies, max_retries=4, backoff_factor=0.8, timeout=10)
    return resp.json()

def calculate_max_pain(full_data):
    """
    Calculates Max Pain from the full NSE API response.
    Max Pain is the strike price with minimum total loss for option writers.
    
    Args:
        full_data: The complete NSE API response (not just records_data)
    """
    if not full_data or 'records' not in full_data:
        return 0.0
    
    records = full_data['records']
    
    # Get the current expiry (first in the expiryDates list)
    if 'expiryDates' not in records or not records['expiryDates']:
        return 0.0
    
    current_expiry = records['expiryDates'][0]
    
    # Filter option chain data for current expiry only
    if 'data' not in records:
        return 0.0
    
    option_chain_data = [r for r in records['data'] if r.get('expiryDate') == current_expiry]
    
    if not option_chain_data:
        return 0.0

    strikes = []
    ce_oi = {}
    pe_oi = {}

    for record in option_chain_data:
        strike = record.get('strikePrice')
        if not strike:
            continue
        strikes.append(strike)
        ce_oi[strike] = record.get('CE', {}).get('openInterest', 0)
        pe_oi[strike] = record.get('PE', {}).get('openInterest', 0)

    if not strikes:
        return 0.0

    min_loss = float('inf')
    max_pain_strike = 0.0

    # For each possible strike price, calculate total loss
    for test_strike in strikes:
        total_loss = 0
        for strike in strikes:
            # Call Writers Loss: If Price > Strike, Loss = (Price - Strike) * Call OI
            if test_strike > strike:
                total_loss += (test_strike - strike) * ce_oi.get(strike, 0)
            
            # Put Writers Loss: If Price < Strike, Loss = (Strike - Price) * Put OI
            if test_strike < strike:
                total_loss += (strike - test_strike) * pe_oi.get(strike, 0)
        
        if total_loss < min_loss:
            min_loss = total_loss
            max_pain_strike = test_strike

    return max_pain_strike

def process_and_save_oi_data(symbol, data):
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if not stock:
            stock = Stock(symbol=symbol)
            db.add(stock)
            db.commit()
            db.refresh(stock)

        # Check if data is valid
        if not data or 'records' not in data:
            logger.info(f"process_and_save_oi_data: missing 'records' for {symbol}, skipping")
            return

        # Calculate Max Pain (pass full data, not just records_data)
        max_pain = calculate_max_pain(data)

        # Defensive extraction of expected fields
        records = data.get('records') if isinstance(data, dict) else None
        if not records:
            logger.info(f"process_and_save_oi_data: missing 'records' for {symbol}, skipping")
            return

        underlying_value = records.get('underlyingValue')
        if underlying_value is None:
            logger.info(f"process_and_save_oi_data: missing underlyingValue for {symbol}, skipping")
            return

        filtered = data.get('filtered') if isinstance(data, dict) else {}
        ce = filtered.get('CE', {}) if isinstance(filtered, dict) else {}
        pe = filtered.get('PE', {}) if isinstance(filtered, dict) else {}

        # Safely parse numeric fields with defaults
        def _to_int(x, default=0):
            try:
                return int(x)
            except Exception:
                try:
                    return int(float(x))
                except Exception:
                    return default

        def _to_float(x, default=0.0):
            try:
                return float(x)
            except Exception:
                return default

        total_call_oi = _to_int(ce.get('totOI', 0))
        total_put_oi = _to_int(pe.get('totOI', 0))
        vol_ce = _to_int(ce.get('totVol', 0))
        vol_pe = _to_int(pe.get('totVol', 0))

        # Get the last record to calculate changes
        last_oi_data = db.query(OIData).filter(OIData.stock_id == stock.id).order_by(OIData.id.desc()).first()

        change_in_ltp = 0.0
        change_in_future_oi = 0
        change_in_call_oi = 0
        change_in_put_oi = 0

        if last_oi_data and last_oi_data.ltp is not None:
            change_in_ltp = _to_float(underlying_value) - _to_float(last_oi_data.ltp or 0.0)
            change_in_call_oi = total_call_oi - _to_int(last_oi_data.call_oi or 0)
            change_in_put_oi = total_put_oi - _to_int(last_oi_data.put_oi or 0)

        oi_interpretation = ""
        try:
            if change_in_ltp > 0 and change_in_call_oi > 0:
                oi_interpretation = "Long Buildup"
            elif change_in_ltp < 0 and change_in_call_oi > 0:
                oi_interpretation = "Short Buildup"
            elif change_in_ltp < 0 and change_in_call_oi < 0:
                oi_interpretation = "Long Unwinding"
            elif change_in_ltp > 0 and change_in_call_oi < 0:
                oi_interpretation = "Short Covering"
        except Exception:
            oi_interpretation = ""

        oi_data = OIData(
            stock_id=stock.id,
            date=datetime.now().date(),
            timestamp=datetime.now().strftime("%H:%M"),
            ltp=_to_float(underlying_value),
            change_in_ltp=change_in_ltp,
            volume=vol_ce + vol_pe,
            future_oi=0,  # This data is not in the option chain response
            change_in_future_oi=0,
            call_oi=total_call_oi,
            change_in_call_oi=change_in_call_oi,
            put_oi=total_put_oi,
            change_in_put_oi=change_in_put_oi,
            oi_interpretation=oi_interpretation,
            max_pain=max_pain,
            buy_sell_signal=""  # Logic to be implemented
        )
        db.add(oi_data)
        db.commit()
    except Exception as e:
        logger.exception(f"process_and_save_oi_data: error for {symbol}: {e}")
    finally:
        db.close()

def save_option_chain_data(symbol, data):
    """Save per-strike option chain data for OI change analysis."""
    from database import OptionChainData
    
    db = SessionLocal()
    try:
        # Get or create stock
        from database import Stock
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if not stock:
            stock = Stock(symbol=symbol)
            db.add(stock)
            db.commit()
            db.refresh(stock)
        
        if not data or 'records' not in data:
            return
        
        records = data['records']
        
        # Get current expiry (first in list)
        if 'expiryDates' not in records or not records['expiryDates']:
            return
        
        current_expiry = records['expiryDates'][0]
        option_data = [r for r in records['data'] if r.get('expiryDate') == current_expiry]
        
        current_date = datetime.now().date()
        current_time = datetime.now().strftime("%H:%M")
        
        # Save each strike's data
        for record in option_data:
            strike = record.get('strikePrice')
            if not strike:
                continue
            
            ce_data = record.get('CE', {})
            pe_data = record.get('PE', {})
            
            chain_entry = OptionChainData(
                stock_id=stock.id,
                date=current_date,
                timestamp=current_time,
                expiry_date=current_expiry,
                strike_price=strike,
                call_oi=ce_data.get('openInterest', 0),
                call_oi_change=ce_data.get('changeinOpenInterest', 0),
                call_volume=ce_data.get('totalTradedVolume', 0),
                put_oi=pe_data.get('openInterest', 0),
                put_oi_change=pe_data.get('changeinOpenInterest', 0),
                put_volume=pe_data.get('totalTradedVolume', 0)
            )
            db.add(chain_entry)
        
        db.commit()
        logger.debug(f"Saved option chain data for {symbol}: {len(option_data)} strikes")
        
    except Exception as e:
        logger.exception(f"save_option_chain_data: error for {symbol}: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    nifty_data = fetch_oi_data("NIFTY")
    process_and_save_oi_data("NIFTY", nifty_data)
    print("Data fetched and saved for NIFTY.")

UNDERLYING_URL = "https://www.nseindia.com/api/underlying-information"

def fetch_fno_symbols():
    """Fetch underlying information from NSE and return a list of symbols
    that are available for F&O. This is resilient to small changes in the
    NSE response structure and falls back to a default list on failure."""
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36',
        'accept-language': 'en,gu;q=0.9,hi;q=0.8'
    }
    try:
        cookies = get_nse_cookies()
        resp = requests_get_with_retry(UNDERLYING_URL, headers=headers, cookies=cookies, max_retries=4, backoff_factor=0.8, timeout=10)
        data = resp.json()

        found = []

        # Preferred parsing for the structure you provided:
        # { "data": { "IndexList": [...], "UnderlyingList": [...] } }
        if isinstance(data, dict) and 'data' in data and isinstance(data['data'], dict):
            inner = data['data']
            for list_key in ('IndexList', 'UnderlyingList'):
                if list_key in inner and isinstance(inner[list_key], list):
                    for item in inner[list_key]:
                        if not isinstance(item, dict):
                            continue
                        sym = item.get('symbol') or item.get('underlying') or item.get('name')
                        if sym:
                            found.append(sym)

        # Fallback: try other common layouts (flat list or dict with 'symbols'/'underlyings')
        if not found:
            candidates = []
            if isinstance(data, dict):
                for k in ('symbols', 'data', 'underlyings'):
                    if k in data and isinstance(data[k], list):
                        candidates = data[k]
                        break
            if not candidates and isinstance(data, list):
                candidates = data
            
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                sym = item.get('symbol') or item.get('underlying') or item.get('name')
                # include if present; for underlying-information we assume returned lists are already F&O relevant
                if sym:
                    found.append(sym)

        # Final fallback
        if not found:
            found = ["NIFTY", "BANKNIFTY", "RELIANCE"]

        # Remove duplicates while preserving order
        unique = list(dict.fromkeys(found))
        logger.info(f"Discovered {len(unique)} F&O symbols from NSE (seed).")
        return unique
    except Exception as e:
        logger.exception(f"Failed to fetch F&O symbols from NSE: {e}")
        return ["NIFTY", "BANKNIFTY", "RELIANCE"]
