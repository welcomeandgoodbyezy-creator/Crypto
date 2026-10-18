import ccxt
import pandas as pd
import pandas_ta as ta
import time
import os
from datetime import datetime

# --- LIVE ROTATION CONFIG ---
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
LEVERAGE = 5
FEE_RATE = 0.0004
ADX_THRESHOLD = 25
VOL_MULT = 1.2
TIMEFRAME = '1h'
CHECK_INTERVAL = 3600
DORMANT_CANDLES = 3  # switch after 3 consecutive low-ADX candles

SYMBOL_PRIORITY = [
    ('DOTUSDT', 1.0),
    ('MANAUSDT', 1.0),
    ('SOLUSDT', 1.0),
    ('AXSUSDT', 1.0)
]

exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

def get_balance():
    try:
        bal = exchange.fetch_balance()
        return float(bal['info']['totalWalletBalance'])
    except Exception as e:
        print(f"balance error: {e}")
        return 0.0

def set_leverage(symbol, lev):
    try:
        exchange.set_leverage(lev, symbol)
    except Exception as e:
        print(f"leverage error {symbol}: {e}")

def calculate_indicators(df):
    if df is None or len(df) < 30:
        return None
    df.ta.sma(length=7, append=True)
    df.ta.sma(length=25, append=True)
    df.ta.adx(length=14, append=True)
    df['VOL_SMA'] = df['volume'].rolling(window=20).mean()
    df.rename(columns={'SMA_7':'SMA_SHORT','SMA_25':'SMA_LONG','ADX_14':'ADX'}, inplace=True)
    return df

def get_signal_and_adx(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        for c in ['open','high','low','close','volume']:
            df[c] = df[c].astype(float)
        df = calculate_indicators(df)
        if df is None:
            return None, 0
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        vol_ok = last['volume'] > last['VOL_SMA'] * VOL_MULT
        
        signal = None
        if prev['SMA_SHORT'] <= prev['SMA_LONG'] and last['SMA_SHORT'] > last['SMA_LONG'] and vol_ok:
            signal = 'LONG'
        elif prev['SMA_SHORT'] >= prev['SMA_LONG'] and last['SMA_SHORT'] < last['SMA_LONG'] and vol_ok:
            signal = 'SHORT'
        
        return signal, last['ADX']
    except Exception as e:
        print(f"signal error {symbol}: {e}")
        return None, 0

def execute_trade(symbol, signal, balance):
    try:
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        amount = (balance * LEVERAGE) / price
        side = 'buy' if signal == 'LONG' else 'sell'
        order = exchange.create_market_order(symbol, side, amount)
        print(f"[{datetime.now()}] {symbol} {signal} | ${balance:.2f} @ ${price}")
        return order
    except Exception as e:
        print(f"trade error {symbol}: {e}")
        return None

def close_position(symbol):
    try:
        pos = exchange.fetch_position(symbol)
        if pos and float(pos.get('contracts', 0)) != 0:
            side = 'sell' if pos['side'] == 'long' else 'buy'
            amt = abs(float(pos['contracts']))
            exchange.create_market_order(symbol, side, amt)
            print(f"[{datetime.now()}] closed {symbol} {pos['side']}")
    except:
        pass

def main():
    print("=== LIVE SYMBOL ROTATION BOT ===")
    print(f"priority: {[s[0] for s in SYMBOL_PRIORITY]}")
    print(f"dormant threshold: {DORMANT_CANDLES} consecutive low-ADX candles\n")
    
    active_symbol = None
    dormant_count = {}
    position_open = False
    
    for sym, _ in SYMBOL_PRIORITY:
        dormant_count[sym] = 0
    
    while True:
        try:
            balance = get_balance()
            print(f"\n[{datetime.now()}] balance: ${balance:.2f} | active: {active_symbol}")
            
            # Check all symbols for ADX status
            best_candidate = None
            for sym, _ in SYMBOL_PRIORITY:
                signal, adx = get_signal_and_adx(sym)
                
                if adx >= ADX_THRESHOLD:
                    dormant_count[sym] = 0
                    if best_candidate is None:
                        best_candidate = sym
                else:
                    dormant_count[sym] += 1
            
            # Rotate if current symbol goes dormant or better candidate emerges
            if active_symbol and dormant_count.get(active_symbol, 0) >= DORMANT_CANDLES:
                print(f"⚠️  {active_symbol} dormant ({dormant_count[active_symbol]} candles) - rotating")
                if position_open:
                    close_position(active_symbol)
                    position_open = False
                active_symbol = None
            
            if active_symbol is None and best_candidate:
                active_symbol = best_candidate
                print(f">>> SWITCHING TO {active_symbol}")
            
            # Execute on active symbol
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

if __name__ == "__main__":
    main()