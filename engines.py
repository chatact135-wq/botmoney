import os
import pandas as pd
import requests

def fetch_live_tick_data(symbol="XAU/USD"):
    api_key = os.getenv("TWELVEDATA_API_KEY", "demo")
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1min&outputsize=30&apikey={api_key}"
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
    Hyper-aggressive scalper: triggers whenever price stretches away from the 5 EMA,
    targeting a quick $0.55 net profit capture.
    """
    if df is None or len(df) < 15:
        return None

    df['ema_fast'] = df['close'].ewm(span=5, adjust=False).mean()
    
    current_price = df['close'].iloc[-1]
    current_ema = df['ema_fast'].iloc[-1]
    
    spread_offset = 0.27
    net_profit_target = 0.55
    total_tp_distance = spread_offset + net_profit_target

    # Distance deviation check: if price dips below EMA fast by $0.20+, trigger a aggressive BUY
    if current_price < current_ema - 0.20:
        return {
            "action": "BUY",
            "entry": current_price,
            "stop_loss": round(current_price - 10.00, 2),
            "take_profit": round(current_price + total_tp_distance, 2),
            "lot_size": 0.01,
            "reason": "Aggressive Dip Buy"
        }

    # If price surges above EMA fast by $0.20+, trigger an aggressive SELL
    elif current_price > current_ema + 0.20:
        return {
            "action": "SELL",
            "entry": current_price,
            "stop_loss": round(current_price + 10.00, 2),
            "take_profit": round(current_price - total_tp_distance, 2),
            "lot_size": 0.01,
            "reason": "Aggressive Surge Sell"
        }

    return None
