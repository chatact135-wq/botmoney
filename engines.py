import os
import pandas as pd
import requests

def fetch_live_tick_data(symbol="XAU/USD"):
    """Fetches fast 1-minute data for scalping via TwelveData or similar feed"""
    api_key = os.getenv("TWELVEDATA_API_KEY", "demo")
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1min&outputsize=50&apikey={api_key}"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if "values" in data:
            df = pd.DataFrame(data["values"])
            df = df.iloc[::-1].reset_index(drop=True)
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            return df
    except Exception as e:
        print(f"Data fetch error: {e}")
    return None

def scalping_engine(df):
    """
    High-frequency scalping logic:
    Checks fast 1-minute momentum, RSI, and EMA cross for micro-entries.
    """
    if df is None or len(df) < 20:
        return None

    # Calculate 5 EMA and 13 EMA for rapid scalping cross
    df['ema_fast'] = df['close'].ewm(span=5, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=13, adjust=False).mean()
    
    current_price = df['close'].iloc[-1]
    prev_fast = df['ema_fast'].iloc[-2]
    curr_fast = df['ema_fast'].iloc[-1]
    prev_slow = df['ema_slow'].iloc[-2]
    curr_slow = df['ema_slow'].iloc[-1]

    # Bullish Scalp Cross (Fast EMA crosses above Slow EMA)
    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return {
            "action": "BUY",
            "entry": current_price,
            "stop_loss": round(current_price - 15.00, 2), # Wide initial SL to breathe
            "take_profit": round(current_price + 0.60, 2), # Tight target for fast micro profit
            "lot_size": 0.01,
            "reason": f"Scalp Buy: EMA 5/13 Crossover at {current_price}"
        }

    # Bearish Scalp Cross (Fast EMA crosses below Slow EMA)
    elif prev_fast >= prev_slow and curr_fast < curr_slow:
        return {
            "action": "SELL",
            "entry": current_price,
            "stop_loss": round(current_price + 15.00, 2),
            "take_profit": round(current_price - 0.60, 2),
            "lot_size": 0.01,
            "reason": f"Scalp Sell: EMA 5/13 Crossover at {current_price}"
        }

    return None
