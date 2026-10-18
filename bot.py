import requests
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime

# --- CONFIG ---
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
LEVERAGE = 5
FEE_RATE = 0.0004
ADX_THRESHOLD = 25
VOL_MULT = 1.2
TIMEFRAME = '1h'
CHECK_INTERVAL = 3600
DORMANT_CANDLES = 3

SYMBOL_PRIORITY = [
    ('DOTUSDT', 1.0),
    ('MANAUSDT', 1.0),
    ('SOLUSDT', 1.0),
    ('AXSUSDT', 1.0)
]

BASE_URL = 'https://api.binance.com' 

def get_balance():
    params = {'timestamp': int(time.time()*1000)}
    # Note: requires signature, simplified here for structure
    resp = requests.get(f'{BASE_URL}/fapi/v2/account', params=params, 
                       headers={'X-MBX-APIKEY': API_KEY})
    if resp.status_code == 200:
        return float(resp.json()['totalWalletBalance'])
    print(f"balance error: {resp.text}")
    return 0.0

def set_leverage(symbol, lev):
    params = {'symbol': symbol, 'leverage': lev, 'timestamp': int(time.time()*1000)}
    requests.post(f'{BASE_URL}/fapi/v1/leverage', params=params,
                 headers={'X-MBX-APIKEY': API_KEY})

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
    adx = pd.Series(dx).rolling(period).mean().values
    
    result = np.full(len(df), np.nan)
    result[period+1:] = adx[:len(adx)]
    return result

def get_signal_and_adx(symbol):
    try:
        resp = requests.get(f'{BASE_URL}/fapi/v1/klines', 
                           params={'symbol': symbol, 'interval': TIMEFRAME, 'limit': 100})
        data = resp.json()
        
        df = pd.DataFrame(data, columns=['t','o','h','l','c','v','ct','qv','trades','tbv','tbq','i'])
        for col in ['o','h','l','c','v']:
            df[col] = df[col].astype(float)
        
        sma7 = df['c'].rolling(7).mean()
        sma25 = df['c'].rolling(25).mean()
        vol_sma = df['v'].rolling(20).mean()
        adx = calculate_adx(df)
        
        last_idx = len(df) - 1
        prev_idx = last_idx - 1
        
        if np.isnan(adx[last_idx]) or adx[last_idx] < ADX_THRESHOLD:
            return None, 0
        
        vol_ok = df['v'].iloc[last_idx] > vol_sma.iloc[last_idx] * VOL_MULT
        
        signal = None
        if sma7.iloc[prev_idx] <= sma25.iloc[prev_idx] and sma7.iloc[last_idx] > sma25.iloc[last_idx] and vol_ok:
            signal = 'LONG'
        elif sma7.iloc[prev_idx] >= sma25.iloc[prev_idx] and sma7.iloc[last_idx] < sma25.iloc[last_idx] and vol_ok:
            signal = 'SHORT'
            
        return signal, adx[last_idx]
        
    except Exception as e:
        print(f"signal error {symbol}: {e}")
        return None, 0

def execute_trade(symbol, signal, balance):
    try:
        ticker = requests.get(f'{BASE_URL}/fapi/v1/ticker/price', params={'symbol': symbol}).json()
        price = float(ticker['price'])
        qty = round((balance * LEVERAGE) / price, 3)
        
        side = 'BUY' if signal == 'LONG' else 'SELL'
        params = {
            'symbol': symbol, 'side': side, 'type': 'MARKET',
            'quantity': qty, 'timestamp': int(time.time()*1000)
        }
        resp = requests.post(f'{BASE_URL}/fapi/v1/order', params=params,
                            headers={'X-MBX-APIKEY': API_KEY})
        print(f"[{datetime.now()}] {symbol} {signal} | ${balance:.2f} @ ${price}")
        return resp.json()
    except Exception as e:
        print(f"trade error {symbol}: {e}")
        return None

def close_position(symbol):
    try:
        pos = requests.get(f'{BASE_URL}/fapi/v2/positionRisk', 
                          params={'symbol': symbol, 'timestamp': int(time.time()*1000)},
                          headers={'X-MBX-APIKEY': API_KEY}).json()
        if pos and float(pos[0].get('positionAmt', 0)) != 0:
            amt = abs(float(pos[0]['positionAmt']))
            side = 'SELL' if float(pos[0]['positionAmt']) > 0 else 'BUY'
            params = {'symbol': symbol, 'side': side, 'type': 'MARKET',
                     'quantity': amt, 'timestamp': int(time.time()*1000)}
            requests.post(f'{BASE_URL}/fapi/v1/order', params=params,
                         headers={'X-MBX-APIKEY': API_KEY})
            print(f"[{datetime.now()}] closed {symbol}")
    except:
        pass

if __name__ == "__main__":
    print("=== LIGHTWEIGHT ROTATION BOT ===")
    active_symbol = None
    dormant_count = {s: 0 for s, _ in SYMBOL_PRIORITY}
    position_open = False
    
    while True:
        try:
            balance = get_balance()
            print(f"\n[{datetime.now()}] balance: ${balance:.2f} | active: {active_symbol}")
            
            best_candidate = None
            for sym, _ in SYMBOL_PRIORITY:
                signal, adx = get_signal_and_adx(sym)
                if adx >= ADX_THRESHOLD:
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