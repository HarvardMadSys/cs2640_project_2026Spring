from flask import Flask, Response
import yfinance as yf
import json
from flask_apscheduler import APScheduler
import sys
from flask import request

app = Flask(__name__)
scheduler = APScheduler()
cache = {}

def update_ticker_data():
    print("\n--- [POLLING START] ---", flush=True)
    try:
        with open('.config', 'r') as f:
            tickers = [line.strip() for line in f if line.strip()]
        
        for symbol in tickers:
            symbol = symbol.upper()
            print(f"DEBUG: Attempting to fetch {symbol}...", flush=True)
            
            ticker_obj = yf.Ticker(symbol)
            
            # Using fast_info instead of info to prevent hanging
            # This gets price, currency, and basic market data
            basic_info = dict(ticker_obj.fast_info)
            
            # If you REALLY need the full 'info' (the big blob), 
            # let's only try it if the basic info works.
            # basic_info['longName'] = ticker_obj.info.get('shortName') 

            cache[symbol] = basic_info
            print(f"SUCCESS: Cached {symbol}", flush=True)
            
        print(f"--- [COMPLETE] Keys: {list(cache.keys())} ---", flush=True)

    except Exception as e:
        print(f"ERROR: {e}", flush=True)

@app.route('/')
def get_data():
    # 1. Look for 'ticker' in the URL (e.g., ?ticker=TSLA)
    # 2. If not provided, default to the first key in the cache
    ticker_query = request.args.get('ticker')
    
    if not ticker_query:
        if cache:
            symbol = list(cache.keys())[0]
        else:
            return Response("Cache is empty. Waiting for poll...", status=503)
    else:
        symbol = ticker_query.upper()

    # 3. Check if the requested symbol exists in our polled data
    if symbol in cache:
        output = json.dumps(cache[symbol], indent=4, sort_keys=True)
        return Response(output, mimetype='application/json')
    else:
        error_payload = {
            "error": f"Symbol '{symbol}' is not being polled.",
            "available_tickers": list(cache.keys()),
            "note": "Only tickers listed in .config are available via this API."
        }
        return Response(json.dumps(error_payload, indent=4), mimetype='application/json', status=404)

if __name__ == "__main__":
    # Start the scheduler first
    print("hello")
    scheduler.add_job(id='TickerPoller', func=update_ticker_data, trigger='interval', minutes=5)
    print("hi")
    scheduler.start()
    
    # Run once manually
    update_ticker_data()
    
    app.run(host='0.0.0.0', port=5000, use_reloader=False)
