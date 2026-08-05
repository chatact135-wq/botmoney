import os
import time
import asyncio
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import engine, SessionLocal, Base, ScalpJournal, get_db
from metaapi_cloud_sdk import MetaApi

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY_HERE")

# MetaApi Credentials for Live Execution
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

PAIRS = ["XAU/USD"]

SYSTEM_KEYS = ["breakout", "pullback", "fvg", "adx_rsi", "asian_sweep", "mss", "volume_profile", "candlesticks"]

LATEST_SIGNALS = {
    pair: {
        sys_key: {
            "action": "WAIT", "reason": "Initializing quantitative scan...",
            "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0
        } for sys_key in SYSTEM_KEYS
    } for pair in PAIRS
}

last_logged_signal = {sys_key: {} for sys_key in SYSTEM_KEYS}
signal_timestamps = {}

# Global execution states for background bot management
is_bot_running = True
global_connection = None
management_task = None

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=150&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
        if "status" in response and response["status"] == "error":
            return f"API Error: {response.get('message', 'Unknown')}"
        if "values" not in response:
            return "API Error: No data returned."
        
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        
        cols_to_convert = ["open", "high", "low", "close"]
        if "volume" in df.columns:
            cols_to_convert.append("volume")
            
        for col in cols_to_convert:
            df[col] = df[col].astype(float)
            
        if "volume" not in df.columns:
            df["volume"] = 1.0
            
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        return f"Fetch Exception: {str(e)}"

# Indicator Helper Functions
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

# 1. Breakout System
def analyze_breakout(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        period = 55
        df_period = data.iloc[-period-2:-2]
        highest_high = df_period['high'].max()
        lowest_low = df_period['low'].min()
        
        current = data.iloc[-2]
        close = float(current["close"])
        candle_time = current["datetime"]
        
        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": round(lowest_low, decimals), "resistance": round(highest_high, decimals), "timestamp": 0}

        action = "WAIT"
        reason = f"Scanning Breakout. Ceiling: ${round(highest_high, decimals)} | Floor: ${round(lowest_low, decimals)}"

        if close > highest_high:
            action = "BUY"
            reason = f"Breakout: 14-Hour Ceiling Broken (${round(highest_high, decimals)})"
        elif close < lowest_low:
            action = "SELL"
            reason = f"Breakout: 14-Hour Floor Broken (${round(lowest_low, decimals)})"

        return process_signal("breakout", pair, action, close, lowest_low if action=="BUY" else highest_high, 
                              close + (highest_high - lowest_low)*1.5 if action=="BUY" else close - (highest_high - lowest_low)*1.5,
                              lowest_low, highest_high, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 2. EMA Pullback System
def analyze_pullback(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['ema_50'] = calc_ema(df, 50)
        df['ema_20'] = calc_ema(df, 20)
        df['atr'] = calc_atr(df, 14)

        current = df.iloc[-2]
        close, open_p, low, high = float(current["close"]), float(current["open"]), float(current["low"]), float(current["high"])
        candle_time = current["datetime"]
        ema_50, ema_20, atr = float(current["ema_50"]), float(current["ema_20"]), float(current["atr"])

        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        action = "WAIT"
        reason = f"Trend: {'BULLISH' if close > ema_50 else 'BEARISH'} (50 EMA: ${round(ema_50, decimals)}) | 20 EMA: ${round(ema_20, decimals)}"

        if close > ema_50 and low <= ema_20 and close > open_p:
            action = "BUY"
            reason = f"Fast Pullback: Bullish bounce off 20 EMA (${round(ema_20, decimals)})"
        elif close < ema_50 and high >= ema_20 and close < open_p:
            action = "SELL"
            reason = f"Fast Pullback: Bearish rejection at 20 EMA (${round(ema_20, decimals)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("pullback", pair, action, close, sl, tp, ema_50, ema_20, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 3. Fair Value Gap (FVG) System
def analyze_fvg(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['atr'] = calc_atr(df, 14)
        
        c1, c2, c3 = df.iloc[-4], df.iloc[-3], df.iloc[-2]
        close = float(c3["close"])
        atr = float(c3["atr"])
        candle_time = c3["datetime"]

        action = "WAIT"
        reason = "Scanning Price Action for Fair Value Imbalances..."
        sl, tp, supp, res = 0.0, 0.0, 0.0, 0.0

        if c3["low"] > c1["high"]:
            fvg_size = c3["low"] - c1["high"]
            if fvg_size > (atr * 0.3):
                action = "BUY"
                supp = float(c1["high"])
                res = float(c3["low"])
                sl = supp - (atr * 1.0)
                tp = close + (atr * 2.0)
                reason = f"Bullish FVG Identified: Gap between ${round(supp, decimals)} - ${round(res, decimals)}"

        elif c3["high"] < c1["low"]:
            fvg_size = c1["low"] - c3["high"]
            if fvg_size > (atr * 0.3):
                action = "SELL"
                res = float(c1["low"])
                supp = float(c3["high"])
                sl = res + (atr * 1.0)
                tp = close - (atr * 2.0)
                reason = f"Bearish FVG Identified: Gap between ${round(supp, decimals)} - ${round(res, decimals)}"

        return process_signal("fvg", pair, action, close, sl, tp, supp, res, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 4. ADX + RSI Momentum System
def analyze_adx_rsi(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['rsi'] = calc_rsi(df, 14)
        df['adx'], df['p_di'], df['m_di'] = calc_adx(df, 14)
        df['atr'] = calc_atr(df, 14)

        current = df.iloc[-2]
        close = float(current["close"])
        rsi = float(current["rsi"])
        adx = float(current["adx"])
        p_di = float(current["p_di"])
        m_di = float(current["m_di"])
        atr = float(current["atr"])
        candle_time = current["datetime"]

        action = "WAIT"
        reason = f"Adx Trend Power: {round(adx, 1)} ({'STRONG' if adx > 25 else 'WEAK'}) | RSI: {round(rsi, 1)}"

        if adx >= 25 and p_di > m_di and rsi >= 55:
            action = "BUY"
            reason = f"Bullish Momentum: ADX Power ({round(adx,1)}) + RSI Expansion ({round(rsi,1)})"
        elif adx >= 25 and m_di > p_di and rsi <= 45:
            action = "SELL"
            reason = f"Bearish Momentum: ADX Power ({round(adx,1)}) + RSI Breakdown ({round(rsi,1)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("adx_rsi", pair, action, close, sl, tp, "-", "-", reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 5. Asian Session Liquidity Sweep
def analyze_asian_sweep(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['atr'] = calc_atr(df, 14)
        
        asian_df = df[df['datetime'].dt.hour < 8]
        if len(asian_df) < 10:
            return {"action": "WAIT", "reason": "Building Asian Session Range...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        asian_high = float(asian_df.iloc[-32:]['high'].max())
        asian_low = float(asian_df.iloc[-32:]['low'].min())

        current = df.iloc[-2]
        close, open_p, high, low = float(current["close"]), float(current["open"]), float(current["high"]), float(current["low"])
        atr = float(current["atr"])
        candle_time = current["datetime"]

        action = "WAIT"
        reason = f"Asian High: ${round(asian_high, decimals)} | Asian Low: ${round(asian_low, decimals)}"

        if low < asian_low and close > asian_low and close > open_p:
            action = "BUY"
            reason = f"Liquidity Sweep: False breakdown below Asian Low (${round(asian_low, decimals)})"
        elif high > asian_high and close < asian_high and close < open_p:
            action = "SELL"
            reason = f"Liquidity Sweep: False breakout above Asian High (${round(asian_high, decimals)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("asian_sweep", pair, action, close, sl, tp, asian_low, asian_high, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 6. Market Structure Shift (MSS) System
def analyze_mss(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['atr'] = calc_atr(df, 14)
        lookback = 10
        
        df['swing_high'] = df['high'].shift(1).rolling(window=lookback).max()
        df['swing_low'] = df['low'].shift(1).rolling(window=lookback).min()

        current = df.iloc[-2]
        close = float(current['close'])
        swing_high = float(current['swing_high'])
        swing_low = float(current['swing_low'])
        atr = float(current['atr'])
        candle_time = current["datetime"]

        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        action = "WAIT"
        reason = f"Monitoring MSS Range: {round(swing_low, decimals)} - {round(swing_high, decimals)}"

        if close > swing_high:
            action = "BUY"
            reason = f"Bullish MSS: Break above previous high (${round(swing_high, decimals)})"
        elif close < swing_low:
            action = "SELL"
            reason = f"Bearish MSS: Break below previous low (${round(swing_low, decimals)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("mss", pair, action, close, sl, tp, swing_low, swing_high, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 7. Volume Profile System
def analyze_volume(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['atr'] = calc_atr(df, 14)
        
        recent_df = df.tail(50).copy()
        recent_df['price_bin'] = pd.cut(recent_df['close'], bins=10)
        volume_profile = recent_df.groupby('price_bin')['volume'].sum()
        
        poc_bin = volume_profile.idxmax()
        poc_mid = poc_bin.mid

        current = df.iloc[-2]
        close, low, high = float(current["close"]), float(current["low"]), float(current["high"])
        atr = float(current["atr"])
        candle_time = current["datetime"]

        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(close, decimals), "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        action = "WAIT"
        reason = f"Tracking Volume Node / POC at ${round(poc_mid, decimals)}"

        if low <= poc_mid and close > poc_mid:
            action = "BUY"
            reason = f"Bullish Defense: Rejection at Volume POC (${round(poc_mid, decimals)})"
        elif high >= poc_mid and close < poc_mid:
            action = "SELL"
            reason = f"Bearish Defense: Rejection at Volume POC (${round(poc_mid, decimals)})"

        sl = close - (atr * 1.5) if action == "BUY" else close + (atr * 1.5)
        tp = close + (atr * 2.5) if action == "BUY" else close - (atr * 2.5)

        return process_signal("volume_profile", pair, action, close, sl, tp, poc_mid, poc_mid, reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# 8. Candlestick Patterns System
def analyze_candlesticks(data, pair: str, db: Session):
    decimals = 2
    if isinstance(data, str) or data is None or len(data) < 60:
        return {"action": "WAIT", "reason": "Loading candles...", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

    try:
        df = data.copy()
        df['atr'] = calc_atr(df, 14)
        
        prev = df.iloc[-3]
        curr = df.iloc[-2]

        p_open, p_close = float(prev['open']), float(prev['close'])
        c_open, c_close, c_high, c_low = float(curr['open']), float(curr['close']), float(curr['high']), float(curr['low'])
        atr = float(curr['atr'])
        candle_time = curr["datetime"]

        current_time = datetime.utcnow() + timedelta(hours=4)
        if (current_time.hour == 20 and current_time.minute >= 30) or current_time.hour > 20:
            return {"action": "WAIT", "reason": "Paused: Time limit (8:30 PM UAE)", "entry": round(c_close, decimals), "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

        body = abs(c_open - c_close)
        lower_wick = min(c_open, c_close) - c_low
        upper_wick = c_high - max(c_open, c_close)

        action = "WAIT"
        reason = "Scanning for institutional candle signatures..."

        if p_close < p_open and c_close > c_open and c_close > p_open and c_open <= p_close:
            action = "BUY"
            reason = "Candlestick: Bullish Engulfing Pattern"
        elif lower_wick > (2 * body) and upper_wick < (0.5 * body):
            action = "BUY"
            reason = "Candlestick: Bullish Pin Bar (Strong lower rejection)"
        elif p_close > p_open and c_close < c_open and c_close < p_open and c_open >= p_close:
            action = "SELL"
            reason = "Candlestick: Bearish Engulfing Pattern"
        elif upper_wick > (2 * body) and lower_wick < (0.5 * body):
            action = "SELL"
            reason = "Candlestick: Bearish Pin Bar (Strong upper rejection)"

        sl = c_close - (atr * 1.5) if action == "BUY" else c_close + (atr * 1.5)
        tp = c_close + (atr * 2.5) if action == "BUY" else c_close - (atr * 2.5)

        return process_signal("candlesticks", pair, action, c_close, sl, tp, "-", "-", reason, candle_time, db)
    except Exception as e:
        return {"action": "WAIT", "reason": f"Math Error: {str(e)}", "entry": "-", "sl": "-", "tp": "-", "support": "-", "resistance": "-", "timestamp": 0}

# Shared Signal Processing & Database Logging
def process_signal(sys_key, pair, action, entry, sl, tp, support, resistance, reason, candle_time, db: Session):
    global last_logged_signal, signal_timestamps
    decimals = 2

    signal_id = f"{sys_key}_{pair}_{str(candle_time)}_{action}"
    if signal_id not in signal_timestamps and action != "WAIT":
        signal_timestamps[signal_id] = int(time.time())

    signal = {
        "action": action,
        "entry": round(entry, decimals) if entry != "-" else "-",
        "sl": round(sl, decimals) if sl != "-" and sl != 0.0 else "-",
        "tp": round(tp, decimals) if tp != "-" and tp != 0.0 else "-",
        "support": round(support, decimals) if support != "-" and support != 0.0 else "-",
        "resistance": round(resistance, decimals) if resistance != "-" and resistance != 0.0 else "-",
        "reason": reason,
        "timestamp": signal_timestamps.get(signal_id, 0) if action != "WAIT" else 0
    }

    if action != "WAIT":
        try:
            if last_logged_signal[sys_key].get(pair) != str(candle_time):
                sys_label_map = {
                    "breakout": "Breakout", 
                    "pullback": "Pullback", 
                    "fvg": "Fair Value Gap", 
                    "adx_rsi": "ADX Momentum", 
                    "asian_sweep": "Asian Sweep",
                    "mss": "Market Structure",
                    "volume_profile": "Volume Profile",
                    "candlesticks": "Candle Pattern"
                }
                db.add(ScalpJournal(
                    pair=pair,
                    action=f"{action} ({sys_label_map.get(sys_key, sys_key)})",
                    entry_price=signal["entry"],
                    stop_loss=signal["sl"],
                    take_profit=signal["tp"],
                    reason=reason
                ))
                db.commit()
                last_logged_signal[sys_key][pair] = str(candle_time)
        except Exception:
            db.rollback()

    return signal

# -------------------------------------------------------------------------
# Automated Live Trading & 0.3s Dynamic Trailing Engine (Cap: 6, Sequence: 0.1 -> 0.6)
# -------------------------------------------------------------------------
async def position_management_loop():
    global is_bot_running, global_connection
    print("Resilient Trailing Engine online (0.3s interval)...")
    
    while is_bot_running:
        try:
            if global_connection and is_bot_running:
                positions = await global_connection.get_positions()
                for pos in positions:
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    pos_type = pos.get("type")
                    open_price = pos.get("openPrice")
                    current_sl = pos.get("stopLoss", 0.0)
                    current_tp = pos.get("takeProfit", 0.0)
                    
                    is_buy = pos_type in [0, "POSITION_TYPE_BUY", "buy"]
                    is_sell = pos_type in [1, "POSITION_TYPE_SELL", "sell"]
                    
                    if is_buy and profit >= 2.00:
                        desired_sl = round(open_price + 0.80, 2)
                        if current_sl < desired_sl:
                            await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                    elif is_sell and profit >= 2.00:
                        desired_sl = round(open_price - 0.80, 2)
                        if current_sl > desired_sl or current_sl == 0:
                            await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
        except Exception:
            pass
            
        await asyncio.sleep(0.3)

async def run_execution_bot():
    global is_bot_running, global_connection, management_task
    
    lot_sequence = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    MAX_CONCURRENT_TRADES = 6
    cooldown_timer = 0
    
    while is_bot_running:
        connection = None
        try:
            metaapi = MetaApi(METAAPI_TOKEN)
            account = await metaapi.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)

            if account.state != "DEPLOYED":
                await account.deploy()

            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
            
            global_connection = connection

            if not management_task or management_task.done():
                management_task = asyncio.create_task(position_management_loop())

            print("MetaApi Live Execution Bridge active...")

            while is_bot_running:
                if cooldown_timer > 0:
                    cooldown_timer -= 1
                    await asyncio.sleep(1)
                    continue

                positions = await connection.get_positions()
                current_open_count = len(positions)
                
                can_open = True
                if current_open_count >= MAX_CONCURRENT_TRADES:
                    can_open = False
                
                active_direction = None
                if current_open_count > 0:
                    for p in positions:
                        p_type = p.get("type")
                        is_b = p_type in [0, "POSITION_TYPE_BUY", "buy"]
                        active_direction = "BUY" if is_b else "SELL"
                        break

                if can_open:
                    action = None
                    for pair, systems in LATEST_SIGNALS.items():
                        for sys_key, sig in systems.items():
                            if sig["action"] in ["BUY", "SELL"]:
                                action = sig["action"]
                                break
                        if action:
                            break

                    if action:
                        layer_index = current_open_count
                        active_lot = lot_sequence[layer_index]
                        
                        price_info = await connection.get_symbol_price("XAUUSDm")
                        current_bid = price_info.get("bid")
                        current_ask = price_info.get("ask")
                        
                        if current_bid and current_ask:
                            if current_open_count > 0:
                                action = active_direction 
                            
                            entry = current_ask if action == "BUY" else current_bid
                            tp_dist = 25.00
                            sl_dist = 30.00
                            
                            print(f"Executing Live Order | Layer {current_open_count + 1} | Action: {action} | Volume: {active_lot}")
                            
                            if action == "BUY":
                                tp = round(entry + tp_dist, 2)
                                sl = round(entry - sl_dist, 2)
                                await connection.create_market_buy_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                            else:
                                tp = round(entry - tp_dist, 2)
                                sl = round(entry + sl_dist, 2)
                                await connection.create_market_sell_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                            
                            cooldown_timer = 12

                await asyncio.sleep(2.0)
                
        except Exception as e:
            print(f"Execution Bridge Error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

# -------------------------------------------------------------------------
# Background Market Data Polling Loop
# -------------------------------------------------------------------------
async def background_bot_loop():
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = await asyncio.to_thread(fetch_market_data, pair)
                if not isinstance(df, str) and df is not None:
                    LATEST_SIGNALS[pair] = {
                        "breakout": analyze_breakout(df, pair, db),
                        "pullback": analyze_pullback(df, pair, db),
                        "fvg": analyze_fvg(df, pair, db),
                        "adx_rsi": analyze_adx_rsi(df, pair, db),
                        "asian_sweep": analyze_asian_sweep(df, pair, db),
                        "mss": analyze_mss(df, pair, db),
                        "volume_profile": analyze_volume(df, pair, db),
                        "candlesticks": analyze_candlesticks(df, pair, db)
                    }
        except Exception as loop_error:
            print(f"Loop error: {str(loop_error)}")
        finally:
            db.close()
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_bot_loop())
    asyncio.create_task(run_execution_bot())

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request): 
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, db: Session = Depends(get_db)):
    try: 
        trades = db.query(ScalpJournal).order_by(ScalpJournal.timestamp.desc()).limit(100).all()
    except Exception: 
        trades = []
    return templates.TemplateResponse(request=request, name="journal.html", context={"trades": trades})

@app.get("/api/signals")
async def get_signals(): 
    return LATEST_SIGNALS
