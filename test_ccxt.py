import ccxt

# PASTE YOUR KEYS DIRECTLY HERE. DO NOT USE ENV VARIABLES FOR THIS TEST.
API_KEY = "6C7unYqvxp9jYhpBuuv1DfZKiY3eHkYOvx9ZFJEgLgz9jR4uGlQUb1rwbMGZOosQ"
API_SECRET = "bENq7ijRcc3MuaJwcRflYZLpkMICm4PE2jLgtL2o8FOD7BlifurhOLxWk6k0lL3j"

exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'options': {
        'defaultType': 'future',
    }
})

try:
    print("Testing connection to Binance Futures...")
    balance = exchange.fetch_balance()
    print("✅ SUCCESS! Total USDT Balance:", balance['total'].get('USDT', 0))
except Exception as e:
    print("❌ FAILED:", e)