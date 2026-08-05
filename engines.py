import numpy as np
import pandas as pd

def calc_ema(df, span):
    return df['close'].ewm(span=span, adjust=False).mean()

def calc_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calc_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_adx(df, period=14):
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)
    
    atr = calc_atr(df, period)
    plus_di = 100 * (pd.Series(df['plus_dm']).ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (pd.Series(df['minus_dm']).ewm(alpha=1/period, adjust=False).mean() / atr)
    
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx, plus_di, minus_di

def analyze_breakout(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    period = 55
    df_period = data.iloc[-period-2:-2]
    highest_high, lowest_low = df_period['high'].max(), df_period['min'] if 'min' in df_period else df_period['low'].min()
    current = data.iloc[-2]
    close = float(current["close"])
    if close > highest_high:
        return {"action": "BUY", "reason": f"Breakout: Ceiling Broken (${highest_high})"}
    elif close < lowest_low:
        return {"action": "SELL", "reason": f"Breakout: Floor Broken (${lowest_low})"}
    return {"action": "WAIT", "reason": "Scanning breakout range"}

def analyze_pullback(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    df = data.copy()
    df['ema_50'] = calc_ema(df, 50)
    df['ema_20'] = calc_ema(df, 20)
    current = df.iloc[-2]
    close, open_p, low, high = float(current["close"]), float(current["open"]), float(current["low"]), float(current["high"])
    ema_50, ema_20 = float(current["ema_50"]), float(current["ema_20"])
    if close > ema_50 and low <= ema_20 and close > open_p:
        return {"action": "BUY", "reason": "Fast Pullback: Bounce off 20 EMA"}
    elif close < ema_50 and high >= ema_20 and close < open_p:
        return {"action": "SELL", "reason": "Fast Pullback: Rejection at 20 EMA"}
    return {"action": "WAIT", "reason": "Monitoring EMA alignment"}

def analyze_fvg(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    df = data.copy()
    df['atr'] = calc_atr(df, 14)
    c1, c3 = df.iloc[-4], df.iloc[-2]
    atr = float(c3["atr"])
    if c3["low"] > c1["high"] and (c3["low"] - c1["high"]) > (atr * 0.3):
        return {"action": "BUY", "reason": "Bullish FVG Imbalance"}
    elif c3["high"] < c1["low"] and (c1["low"] - c3["high"]) > (atr * 0.3):
        return {"action": "SELL", "reason": "Bearish FVG Imbalance"}
    return {"action": "WAIT", "reason": "No active FVG"}

def analyze_adx_rsi(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    df = data.copy()
    df['rsi'] = calc_rsi(df, 14)
    df['adx'], df['p_di'], df['m_di'] = calc_adx(df, 14)
    curr = df.iloc[-2]
    adx, p_di, m_di, rsi = float(curr["adx"]), float(curr["p_di"]), float(curr["m_di"]), float(curr["rsi"])
    if adx >= 25 and p_di > m_di and rsi >= 55:
        return {"action": "BUY", "reason": f"ADX Momentum Strong ({adx:.1f})"}
    elif adx >= 25 and m_di > p_di and rsi <= 45:
        return {"action": "SELL", "reason": f"ADX Breakdown Strong ({adx:.1f})"}
    return {"action": "WAIT", "reason": "ADX below threshold"}

def analyze_asian_sweep(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    df = data.copy()
    asian_df = df[df['datetime'].dt.hour < 8]
    if len(asian_df) < 10:
        return {"action": "WAIT", "reason": "Building Asian range"}
    asian_high = float(asian_df.iloc[-32:]['high'].max())
    asian_low = float(asian_df.iloc[-32:]['low'].min())
    curr = df.iloc[-2]
    close, open_p, high, low = float(curr["close"]), float(curr["open"]), float(curr["high"]), float(curr["low"])
    if low < asian_low and close > asian_low and close > open_p:
        return {"action": "BUY", "reason": "Sweep below Asian Low"}
    elif high > asian_high and close < asian_high and close < open_p:
        return {"action": "SELL", "reason": "Sweep above Asian High"}
    return {"action": "WAIT", "reason": "Within Asian range"}

def analyze_mss(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    df = data.copy()
    lookback = 10
    df['swing_high'] = df['high'].shift(1).rolling(window=lookback).max()
    df['swing_low'] = df['low'].shift(1).rolling(window=lookback).min()
    curr = df.iloc[-2]
    close, sh, sl = float(curr['close']), float(curr['swing_high']), float(curr['swing_low'])
    if close > sh:
        return {"action": "BUY", "reason": "Bullish Market Structure Shift"}
    elif close < sl:
        return {"action": "SELL", "reason": "Bearish Market Structure Shift"}
    return {"action": "WAIT", "reason": "Monitoring swing points"}

def analyze_volume(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    df = data.copy()
    recent_df = df.tail(50).copy()
    recent_df['price_bin'] = pd.cut(recent_df['close'], bins=10)
    vp = recent_df.groupby('price_bin')['volume'].sum()
    poc_mid = vp.idxmax().mid
    curr = df.iloc[-2]
    close, low, high = float(curr["close"]), float(curr["low"]), float(curr["high"])
    if low <= poc_mid and close > poc_mid:
        return {"action": "BUY", "reason": "Rejection at Volume POC"}
    elif high >= poc_mid and close < poc_mid:
        return {"action": "SELL", "reason": "Rejection at Volume POC"}
    return {"action": "WAIT", "reason": "Price away from POC"}

def analyze_candlesticks(data):
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles..."}
    prev, curr = data.iloc[-3], data.iloc[-2]
    p_open, p_close = float(prev['open']), float(prev['close'])
    c_open, c_close, c_high, c_low = float(curr['open']), float(curr['close']), float(curr['high']), float(curr['low'])
    body = abs(c_open - c_close)
    lower_wick = min(c_open, c_close) - c_low
    upper_wick = c_high - max(c_open, c_close)
    if p_close < p_open and c_close > c_open and c_close > p_open:
        return {"action": "BUY", "reason": "Bullish Engulfing"}
    elif lower_wick > (2 * body) and upper_wick < (0.5 * body):
        return {"action": "BUY", "reason": "Bullish Pin Bar"}
    elif p_close > p_open and c_close < c_open and c_close < p_open:
        return {"action": "SELL", "reason": "Bearish Engulfing"}
    elif upper_wick > (2 * body) and lower_wick < (0.5 * body):
        return {"action": "SELL", "reason": "Bearish Pin Bar"}
    return {"action": "WAIT", "reason": "No candlestick trigger"}
