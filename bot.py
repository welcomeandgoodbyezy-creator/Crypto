import ccxt
import pandas as pd
import numpy as np
import os
import time
import platform
from datetime import datetime

API_KEY = os.getenv('BINANCE_API_KEY', '').strip()
API_SECRET = os.getenv('BINANCE_API_SECRET', '').strip()

# Fallback keys for local testing if env vars aren't set
if not API_KEY or not API_SECRET:
    API_KEY = "6C7unYqvxp9jYhpBuuv1DfZKiY3eHkYOvx9ZFJEgLgz9jR4uGlQUb1rwbMGZOosQ"
    API_SECRET = "bENq7ijRcc3MuaJwcRflYZLpkMICm4PE2jLgtL2o8FOD7BlifurhOLxWk6k0lL3j"

LEVERAGE = 5
ADX_THRESHOLD = 25
VOL_MULT = 1.2
TIMEFRAME = '1h'
CHECK_INTERVAL = 3600
DORMANT_CANDLES = 3

SYMBOL_PRIORITY = ['DOTUSDT', 'MANAUSDT', 'SOLUSDT', 'AXSUSDT']

# Detect PythonAnywhere environment to apply mandatory proxy
proxies = {}
if os.path.exists('/home/Zakkai') or os.environ.get('PYTHONANYWHERE_DOMAIN'):
    proxies = {
        'http': 'http://proxy.server:3128',
        'https': 'http://proxy.server:3128'
    }
    print("[INFO] PythonAnywhere environment detected. Routing through proxy.")

# Initialize CCXT with proxy if needed
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'options': {'defaultType': 'future'},
    'enableRateLimit': True,
    'proxies': proxies
})

# CRITICAL: Syncs local time with Binance server to fix clock drift
exchange.load_time_difference()

def get_balance():
    try:
        balance = exchange.fetch_balance()
        return float(balance['total'].get('USDT', 0))
    except Exception as e:
        print(f"balance error: {e}")
        return 0.0

def set_leverage(symbol, lev):
    try:
        exchange.set_leverage(lev, symbol)
    except Exception as e:
        print(f"leverage error: {e}")

def calculate_adx(df, period=14):
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    tr = np.maximum(high[1:] - low[1:],
           np.maximum(np.abs(high[1:] - close[:-1]),
                     np.abs(low[1:] - close[:-1])))
    plus_dm = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
              np.maximum(high[1:] - high[:-1], 0), 0)
    minus_dm = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
               np.maximum(low[:-1] - low[1:], 0), 0)
    atr = pd.Series(tr).rolling(period).mean().values
    plus_di = 100 * pd.Series(plus_dm).rolling(period).mean().values / atr
    minus_di = 100 * pd.Series(minus_dm).rolling(period).mean().values / atr
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx_rolled = pd.Series(dx).rolling(period).mean().values
    padded = np.full(len(df), np.nan)
    offset = len(df) - len(adx_rolled)
    padded[offset:] = adx_rolled
    return padded

def get_signal_and_adx(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        sma7 = df['close'].rolling(7).mean()
        sma25 = df['close'].rolling(25).mean()
        vol_sma = df['volume'].rolling(20).mean()
        adx = calculate_adx(df)

        last_idx = len(df) - 1
        prev_idx = last_idx - 1

        if np.isnan(adx[last_idx]) or adx[last_idx] < ADX_THRESHOLD:
            return None, 0

        vol_ok = df['volume'].iloc[last_idx] > vol_sma.iloc[last_idx] * VOL_MULT

        signal = None
        if (sma7.iloc[prev_idx] <= sma25.iloc[prev_idx] and
            sma7.iloc[last_idx] > sma25.iloc[last_idx] and vol_ok):
            signal = 'LONG'
        elif (sma7.iloc[prev_idx] >= sma25.iloc[prev_idx] and
              sma7.iloc[last_idx] < sma25.iloc[last_idx] and vol_ok):
            signal = 'SHORT'

        return signal, adx[last_idx]
    except Exception as e:
        print(f"signal error {symbol}: {e}")
        return None, 0

def execute_trade(symbol, signal, balance):
    try:
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        qty = round((balance * LEVERAGE) / price, 3)
        side = 'buy' if signal == 'LONG' else 'sell'
        
        order = exchange.create_market_order(symbol, side, qty)
        print(f"[{datetime.now()}] {symbol} {signal} EXECUTED | ${balance:.2f} @ ${price}")
        return order
    except Exception as e:
        print(f"trade error {symbol}: {e}")
        return None

def close_position(symbol):
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            if pos['symbol'] == symbol and float(pos['contracts']) != 0:
                amt = abs(float(pos['contracts']))
                side = 'sell' if float(pos['contracts']) > 0 else 'buy'
                order = exchange.create_market_order(symbol, side, amt)
                print(f"[{datetime.now()}] closed {symbol}")
    except Exception as e:
        print(f"close position error: {e}")

if __name__ == "__main__":
    print("=== CCXT ROTATION BOT ===")
    
    active_symbol = None
    dormant_count = {s: 0 for s in SYMBOL_PRIORITY}
    position_open = False

    while True:
        try:
            balance = get_balance()
            print(f"\n[{datetime.now()}] balance: ${balance:.2f} | active: {active_symbol}")

            if balance <= 0:
                print("⚠️ NO BALANCE - skipping cycle")
                time.sleep(CHECK_INTERVAL)
                continue

            best_candidate = None
            for sym in SYMBOL_PRIORITY:
                signal, adx_val = get_signal_and_adx(sym)
                if adx_val >= ADX_THRESHOLD:
                    dormant_count[sym] = 0
                    if best_candidate is None:
                        best_candidate = sym
                else:
                    dormant_count[sym] += 1

            if active_symbol and dormant_count.get(active_symbol, 0) >= DORMANT_CANDLES:
                print(f"⚠️  {active_symbol} dormant - rotating")
                if position_open:
                    close_position(active_symbol)
                    position_open = False
                active_symbol = None

            if active_symbol is None and best_candidate:
                active_symbol = best_candidate
                print(f">>> SWITCHING TO {active_symbol}")

            if active_symbol and not position_open:
                set_leverage(active_symbol, LEVERAGE)
                signal, _ = get_signal_and_adx(active_symbol)
                if signal and balance > 0:
                    execute_trade(active_symbol, signal, balance)
                    position_open = True
            elif active_symbol and position_open:
                signal, _ = get_signal_and_adx(active_symbol)
                if signal is None:
                    close_position(active_symbol)
                    position_open = False

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\nshutting down...")
            if active_symbol and position_open:
                close_position(active_symbol)
            break
        except Exception as e:
            print(f"loop error: {e}")
            time.sleep(CHECK_INTERVAL)