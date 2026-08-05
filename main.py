import os
import time
import asyncio
import requests
import pandas as pd
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from metaapi_cloud_sdk import MetaApi

from database import SessionLocal, init_db, ScalpJournal
from engines import (
    analyze_breakout, analyze_pullback, analyze_fvg, analyze_adx_rsi,
    analyze_asian_sweep, analyze_mss, analyze_volume, analyze_candlesticks
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "YOUR_API_KEY")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

PAIRS = ["XAU/USD"]
SYSTEM_KEYS = ["breakout", "pullback", "fvg", "adx_rsi", "asian_sweep", "mss", "volume_profile", "candlesticks"]

LATEST_SIGNALS = {
    pair: {sys_key: {"action": "WAIT", "reason": "Initializing scan..."} for sys_key in SYSTEM_KEYS} 
    for pair in PAIRS
}

is_bot_running = True
global_connection = None
management_task = None

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def fetch_market_data(symbol: str):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=150&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
        if "values" not in response:
            return "API Error"
        df = pd.DataFrame(response["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype(float) if "volume" in df.columns else 1.0
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        return f"Fetch Exception: {str(e)}"

# -------------------------------------------------------------------------
# MetaApi 0.3s Trailing Stop Management Loop
# -------------------------------------------------------------------------
async def position_management_loop():
    global is_bot_running, global_connection
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

# -------------------------------------------------------------------------
# MetaApi Execution Bot (6-Tier Progression: 0.1 -> 0.6 Lots)
# -------------------------------------------------------------------------
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

            while is_bot_running:
                if cooldown_timer > 0:
                    cooldown_timer -= 1
                    await asyncio.sleep(1)
                    continue

                positions = await connection.get_positions()
                current_open_count = len(positions)
                
                can_open = current_open_count < MAX_CONCURRENT_TRADES
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
                        active_lot = lot_sequence[current_open_count]
                        price_info = await connection.get_symbol_price("XAUUSDm")
                        current_bid, current_ask = price_info.get("bid"), price_info.get("ask")
                        
                        if current_bid and current_ask:
                            if current_open_count > 0:
                                action = active_direction 
                            
                            entry = current_ask if action == "BUY" else current_bid
                            tp, sl = (round(entry + 25.0, 2), round(entry - 30.0, 2)) if action == "BUY" else (round(entry - 25.0, 2), round(entry + 30.0, 2))
                            
                            if action == "BUY":
                                await connection.create_market_buy_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                            else:
                                await connection.create_market_sell_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                            cooldown_timer = 12
                await asyncio.sleep(2.0)
        except Exception as e:
            print(f"Execution Bridge Error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

# Background Data Polling Loop
async def background_bot_loop():
    init_db()
    while True:
        db = SessionLocal()
        try:
            for pair in PAIRS:
                df = fetch_market_data(pair)
                if not isinstance(df, str) and df is not None:
                    res = {
                        "breakout": analyze_breakout(df),
                        "pullback": analyze_pullback(df),
                        "fvg": analyze_fvg(df),
                        "adx_rsi": analyze_adx_rsi(df),
                        "asian_sweep": analyze_asian_sweep(df),
                        "mss": analyze_mss(df),
                        "volume_profile": analyze_volume(df),
                        "candlesticks": analyze_candlesticks(df)
                    }
                    LATEST_SIGNALS[pair] = res
                    for sys_key, sig in res.items():
                        if sig["action"] != "WAIT":
                            db.add(ScalpJournal(
                                pair=pair,
                                action=f"{sig['action']} ({sys_key})",
                                entry_price=0.0, stop_loss=0.0, take_profit=0.0,
                                reason=sig["reason"]
                            ))
                            db.commit()
        except Exception as e:
            print(f"Loop error: {e}")
        finally:
            db.close()
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_bot_loop())
    asyncio.create_task(run_execution_bot())

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "signals": LATEST_SIGNALS})

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, db: Session = Depends(get_db)):
    try:
        trades = db.query(ScalpJournal).order_by(ScalpJournal.timestamp.desc()).limit(100).all()
    except Exception:
        trades = []
    return templates.TemplateResponse("journal.html", {"request": request, "trades": trades})

@app.get("/api/signals")
async def get_signals():
    return LATEST_SIGNALS
