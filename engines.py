import os
import pandas as pd
import requests

def fetch_live_tick_data(symbol="XAU/USD"):
    """Fetches fast 1-minute data for short-term micro-swings"""
    api_key = os.getenv("TWELVEDATA_API_KEY", "demo")
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1min&outputsize=20&apikey={api_key}"
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
    Detects short-term price swings (up or down) without needing a full trend.
    Calculates exact Take Profit to lock in a net $1.00 profit after spread.
    """
    if df is None or len(df) < 5:
        return None

    current_price = df['close'].iloc[-1]
    prev_close = df['close'].iloc[-2]
    
    # Micro-movement delta (price change from previous candle)
    price_delta = current_price - prev_close

    # Gold parameters
    spread_offset = 0.27
    net_profit_target = 1.00  # $1.00 net profit target per order
    total_tp_distance = spread_offset + net_profit_target

    # Detect downward momentum / micro-swing down -> Buy the dip
    if price_delta <= -0.15:
        return {
            "action": "BUY",
            "entry": current_price,
            "stop_loss": round(current_price - 12.00, 2),
            "take_profit": round(current_price + total_tp_distance, 2),
            "lot_size": 0.01,
            "reason": "Micro-Swing Dip Buy"
        }

    # Detect upward momentum / micro-swing up -> Sell the spike
    elif price_delta >= 0.15:
        return {
            "action": "SELL",
            "entry": current_price,
            "stop_loss": round(current_price + 12.00, 2),
            "take_profit": round(current_price - total_tp_distance, 2),
            "lot_size": 0.01,
            "reason": "Micro-Swing Spike Sell"
        }

    return None
