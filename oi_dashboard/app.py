from flask import Flask, render_template, request, jsonify
from database import SessionLocal, Stock, OIData, init_db, get_meta, set_meta, Meta
from data_fetcher import fetch_oi_data, process_and_save_oi_data, save_option_chain_data, fetch_fno_symbols
from seed import seed_stocks
import threading
import time
import random
import os
from datetime import datetime
import logging

from dashboard import init_dashboard

# Configure logging for the application
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger('oi_dashboard')

app = Flask(__name__)

# Initialize Dash App
dash_app = init_dashboard(app)


@app.route('/stock/<symbol>')
def stock_analysis(symbol):
    db = SessionLocal()
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if not stock:
        db.close()
        return render_template('404.html', message=f"Stock {symbol} not found"), 404

    # Load today's data for the stock
    from datetime import datetime, timedelta
    records = db.query(OIData).filter(OIData.stock_id == stock.id).order_by(OIData.id).all()

    # Helper: parse HH:MM or ISO timestamps
    def parse_time(row):
        t = getattr(row, 'timestamp', None)
        if t and isinstance(t, str):
            try:
                return datetime.fromisoformat(t)
            except Exception:
                try:
                    today = datetime.now().date()
                    hour, minute = map(int, t.split(':'))
                    return datetime(today.year, today.month, today.day, hour, minute)
                except Exception:
                    return None
        return t

    # Resample into 3-minute bins using last value in each bin
    from collections import OrderedDict
    sampled = []
    if records:
        data_sorted = sorted(records, key=lambda r: parse_time(r) or datetime.min)
        intervals = OrderedDict()
        for row in data_sorted:
            t = parse_time(row)
            if not t:
                continue
            bin_start = t.replace(second=0, microsecond=0, minute=(t.minute // 3) * 3)
            intervals.setdefault(bin_start, []).append(row)
        logger.info(f"Resampling bins for {symbol}: {[f'{k.strftime('%H:%M')}: {len(v)}' for k,v in intervals.items()]}")
        sampled = [rows[-1] for rows in intervals.values()]
        # ensure last row present
        if data_sorted and (not sampled or sampled[-1].id != data_sorted[-1].id):
            sampled.append(data_sorted[-1])
        if len(sampled) == 1 and len(data_sorted) > 1:
            logger.warning(f"Resampling for {symbol} resulted in only one row, falling back to all rows.")
            sampled = data_sorted

    # Determine day-start totals from earliest raw data
    if records:
        data_sorted_for_start = sorted(records, key=lambda r: (r.date, r.id))
        day_start_row = data_sorted_for_start[0]
        day_start_put_oi = getattr(day_start_row, 'put_oi', None)
        day_start_call_oi = getattr(day_start_row, 'call_oi', None)
    else:
        day_start_put_oi = None
        day_start_call_oi = None

    # Build result rows with rolling & total changes
    result = []
    first_put_oi = sampled[0].put_oi if sampled else None
    first_call_oi = sampled[0].call_oi if sampled else None
    for i, row in enumerate(sampled):
        if i == 0:
            change_in_ltp = 0.0
            rolling_put_oi_change = None
            rolling_call_oi_change = None
        else:
            prev = sampled[i-1]
            change_in_ltp = float(row.ltp) - float(prev.ltp)
            rolling_put_oi_change = row.put_oi - prev.put_oi
            rolling_call_oi_change = row.call_oi - prev.call_oi

        total_put_oi_change = (row.put_oi - day_start_put_oi) if day_start_put_oi is not None else (row.put_oi - first_put_oi if first_put_oi is not None else None)
        total_call_oi_change = (row.call_oi - day_start_call_oi) if day_start_call_oi is not None else (row.call_oi - first_call_oi if first_call_oi is not None else None)

        result.append({
            'row': row,
            'change_in_ltp': change_in_ltp,
            'rolling_put_oi_change': rolling_put_oi_change,
            'total_put_oi_change': total_put_oi_change,
            'rolling_call_oi_change': rolling_call_oi_change,
            'total_call_oi_change': total_call_oi_change,
            'put_vs_call_total': (total_put_oi_change - total_call_oi_change) if (total_put_oi_change is not None and total_call_oi_change is not None) else None,
            'put_vs_call_rolling': (rolling_put_oi_change - rolling_call_oi_change) if (rolling_put_oi_change is not None and rolling_call_oi_change is not None) else None,
        })

    db.close()
    return render_template('stock_analysis.html', symbol=symbol, data=result)

# Old routes replaced by Dash
# @app.route('/stock/<symbol>')
# ...
# @app.route('/')
# ...

 

@app.route('/fetch_and_update_data')
def fetch_and_update_data():
    db = SessionLocal()
    stocks = db.query(Stock).all()
    for stock in stocks:
        try:
            oi_data = fetch_oi_data(stock.symbol)
            process_and_save_oi_data(stock.symbol, oi_data)
            print(f"Successfully fetched and saved data for {stock.symbol}")
        except Exception as e:
            print(f"Error fetching or saving data for {stock.symbol}: {e}")
    db.close()
    return "Data fetch and update complete."
    

def background_scan_loop(interval_seconds=20, batch_size=20):
    """Background loop that scans a batch of stocks every `interval_seconds` seconds.

    Behavior:
    - Processes `batch_size` symbols per cycle.
    - Persists rotation index in the `meta` table under key 'last_scan_index'.
    - Adds small jitter between per-symbol requests and shuffles order within the batch.
    """
    logger.info(f"Background scanner started, interval={interval_seconds}s, batch_size={batch_size}")
    while True:
        start = time.time()
        try:
            db = SessionLocal()
            # Read runtime-configurable interval and batch size from meta table if present
            try:
                meta_interval = db.query(Meta).filter(Meta.key == 'oi_scan_interval').first()
                if meta_interval and meta_interval.value is not None:
                    runtime_interval = int(meta_interval.value)
                else:
                    runtime_interval = interval_seconds
            except Exception:
                runtime_interval = interval_seconds

            try:
                meta_batch = db.query(Meta).filter(Meta.key == 'oi_batch_size').first()
                if meta_batch and meta_batch.value is not None:
                    runtime_batch = int(meta_batch.value)
                else:
                    runtime_batch = batch_size
            except Exception:
                runtime_batch = batch_size
            total = db.query(Stock).count()
            if total == 0:
                db.close()
                time.sleep(runtime_interval)
                continue

            # Read last index from meta table
            last_index_val = db.query(Meta).filter(Meta.key == 'last_scan_index').first()
            try:
                last_index = int(last_index_val.value) if last_index_val and last_index_val.value is not None else 0
            except Exception:
                last_index = 0

            # Compute batch slice with wrap-around
            start_idx = last_index % total
            end_idx = start_idx + runtime_batch
            batch = []
            if end_idx <= total:
                batch = db.query(Stock).order_by(Stock.id).offset(start_idx).limit(runtime_batch).all()
            else:
                first_part = db.query(Stock).order_by(Stock.id).offset(start_idx).limit(total - start_idx).all()
                second_part = db.query(Stock).order_by(Stock.id).limit(end_idx - total).all()
                batch = first_part + second_part

            # Shuffle the batch to avoid fixed ordering of requests
            random.shuffle(batch)

            processed = 0
            for stock in batch:
                if not stock.symbol:
                    continue
                try:
                    oi_data = fetch_oi_data(stock.symbol)
                    process_and_save_oi_data(stock.symbol, oi_data)
                    save_option_chain_data(stock.symbol, oi_data)
                    logger.info(f"[scanner] fetched and saved {stock.symbol}")
                except Exception as e:
                    logger.warning(f"[scanner] error for {stock.symbol}: {e}")

                processed += 1
                # Small jitter between requests to be NSE-friendly
                time.sleep(random.uniform(0.5, 2.0))

            # Update last_scan_index and last_run_time in meta table
            new_index = (start_idx + processed) % total
            try:
                set_meta(db, 'last_scan_index', str(new_index))
                set_meta(db, 'last_run_time', datetime.now().isoformat())
            except Exception:
                # Fallback to manual update if set_meta fails
                meta_item = db.query(Meta).filter(Meta.key == 'last_scan_index').first()
                if not meta_item:
                    meta_item = Meta(key='last_scan_index', value=str(new_index))
                    db.add(meta_item)
                else:
                    meta_item.value = str(new_index)
                # last_run_time manual
                meta_time = db.query(Meta).filter(Meta.key == 'last_run_time').first()
                if not meta_time:
                    meta_time = Meta(key='last_run_time', value=datetime.now().isoformat())
                    db.add(meta_time)
                else:
                    meta_time.value = datetime.now().isoformat()
                db.commit()
            finally:
                db.close()

        except Exception as e:
            logger.exception(f"[scanner] unexpected error: {e}")

        elapsed = time.time() - start
        sleep_for = max(0, runtime_interval - elapsed)
        if sleep_for > 0:
            # Add a small random element to the sleep to avoid strict periodicity
            time.sleep(sleep_for + random.uniform(0, 1.0))


if __name__ == '__main__':
    init_db()
    seed_stocks()

    # Read configuration from environment variables (defaults preserved)
    try:
        INTERVAL_SECONDS = int(os.getenv('OI_SCAN_INTERVAL', '10'))
    except Exception:
        INTERVAL_SECONDS = 10
    try:
        BATCH_SIZE = int(os.getenv('OI_BATCH_SIZE', '30'))
    except Exception:
        BATCH_SIZE = 30

    # Start background scanner thread (daemon so it won't block shutdown)
    scanner_thread = threading.Thread(target=background_scan_loop, args=(INTERVAL_SECONDS, BATCH_SIZE), daemon=True)
    scanner_thread.start()

    # Add a simple status endpoint to inspect scanner progress
    @app.route('/status')
    def status():
        db = SessionLocal()
        total = db.query(Stock).count()
        last_scan_index = db.query(Meta).filter(Meta.key == 'last_scan_index').first()
        last_run_time = db.query(Meta).filter(Meta.key == 'last_run_time').first()
        result = {
            'total_symbols': total,
            'last_scan_index': int(last_scan_index.value) if last_scan_index and last_scan_index.value is not None else None,
            'last_run_time': last_run_time.value if last_run_time and last_run_time.value is not None else None,
            'interval_seconds': INTERVAL_SECONDS,
            'batch_size': BATCH_SIZE
        }
        db.close()
        return jsonify(result)


        @app.route('/admin', methods=['GET', 'POST'])
        def admin():
            """GET returns current runtime config; POST accepts JSON or form to update
            'oi_scan_interval' and 'oi_batch_size' values which are stored in the meta table.
            """
            db = SessionLocal()
            if request.method == 'POST':
                # Support JSON and form
                payload = request.get_json(silent=True) or request.form
                interval = payload.get('oi_scan_interval') or payload.get('interval_seconds') or payload.get('interval')
                batch = payload.get('oi_batch_size') or payload.get('batch_size') or payload.get('batch')
                resp = {}
                try:
                    if interval is not None:
                        set_meta(db, 'oi_scan_interval', str(int(interval)))
                        resp['oi_scan_interval'] = int(interval)
                    if batch is not None:
                        set_meta(db, 'oi_batch_size', str(int(batch)))
                        resp['oi_batch_size'] = int(batch)
                    # return current values
                    total = db.query(Stock).count()
                    last_scan_index = db.query(Meta).filter(Meta.key == 'last_scan_index').first()
                    resp.update({'total_symbols': total, 'last_scan_index': int(last_scan_index.value) if last_scan_index and last_scan_index.value is not None else None})
                    db.close()
                    logger.info(f"Admin updated settings: {resp}")
                    return jsonify(resp)
                except Exception as e:
                    db.close()
                    logger.exception(f"Admin update failed: {e}")
                    return jsonify({'error': str(e)}), 400

            # GET: show current values
            last_scan_index = db.query(Meta).filter(Meta.key == 'last_scan_index').first()
            meta_interval = db.query(Meta).filter(Meta.key == 'oi_scan_interval').first()
            meta_batch = db.query(Meta).filter(Meta.key == 'oi_batch_size').first()
            result = {
                'oi_scan_interval': int(meta_interval.value) if meta_interval and meta_interval.value is not None else INTERVAL_SECONDS,
                'oi_batch_size': int(meta_batch.value) if meta_batch and meta_batch.value is not None else BATCH_SIZE,
                'last_scan_index': int(last_scan_index.value) if last_scan_index and last_scan_index.value is not None else None
            }
            db.close()
            return jsonify(result)

    # Run Flask app
    app.run(debug=True, host='0.0.0.0', port=5080)
