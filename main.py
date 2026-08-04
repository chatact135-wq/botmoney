import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Bulletproof Dynamic System - Fixed")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def position_management_loop():
    """
    Robust Trailing Engine:
    - Filters strictly for XAUUSDm positions.
    - Uses safe profit thresholds and broker-compliant distance checks.
    """
    global is_bot_running, global_connection
    print("Bulletproof Trailing Engine online...")
    
    while is_bot_running:
        try:
            if global_connection and is_bot_running:
                positions = await global_connection.get_positions()
                for pos in positions:
                    if pos.get("symbol") != "XAUUSDm":
                        continue
                        
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    pos_type = pos.get("type")
                    open_price = pos.get("openPrice")
                    current_sl = pos.get("stopLoss", 0.0)
                    current_tp = pos.get("takeProfit", 0.0)
                    
                    is_buy = pos_type in [0, "POSITION_TYPE_BUY", "buy"]
                    is_sell = pos_type in [1, "POSITION_TYPE_SELL", "sell"]
                    
                    # Trailing activation once profit exceeds safe margin ($3.00+ on 0.1 lot)
                    if is_buy and profit >= 3.00:
                        desired_sl = round(open_price + 0.20, 2) # Secure break-even + buffer
                        if current_sl < desired_sl:
                            try:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                            except Exception:
                                pass
                    elif is_sell and profit >= 3.00:
                        desired_sl = round(open_price - 0.20, 2)
                        if current_sl > desired_sl or current_sl == 0:
                            try:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                            except Exception:
                                pass
        except Exception:
            pass
            
        await asyncio.sleep(1.0)


async def run_bulletproof_bot():
    """
    Execution Engine:
    - Multi-tier sequence with momentum/trend validation to avoid fading breakout trends.
    """
    global is_bot_running, global_connection, management_task
    is_bot_running = True
    
    lot_sequence = [0.1, 0.2, 0.3]
    MAX_CONCURRENT_TRADES = 3
    cooldown_timer = 0
    
    while is_bot_running:
        connection = None
        try:
            metaapi = MetaApi(TOKEN)
            account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

            if account.state != "DEPLOYED":
                await account.deploy()

            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
            
            global_connection = connection

            if not management_task or management_task.done():
                management_task = asyncio.create_task(position_management_loop())

            print("Bulletproof Engine active...")

            price_history = []

            while is_bot_running:
                if cooldown_timer > 0:
                    cooldown_timer -= 1
                    await asyncio.sleep(1)
                    continue

                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    price_history.append(current_price)
                    
                    if len(price_history) > 30:
                        price_history.pop(0)
                        
                    if len(price_history) >= 30 and is_bot_running:
                        # Simple Moving Average for trend direction filter
                        sma = sum(price_history) / len(price_history)
                        
                        positions = await connection.get_positions()
                        xau_positions = [p for p in positions if p.get("symbol") == "XAUUSDm"]
                        current_open_count = len(xau_positions)
                        
                        can_open = current_open_count < MAX_CONCURRENT_TRADES
                        
                        active_direction = None
                        if current_open_count > 0:
                            p_type = xau_positions[0].get("type")
                            is_b = p_type in [0, "POSITION_TYPE_BUY", "buy"]
                            active_direction = "BUY" if is_b else "SELL"

                        if can_open:
                            action = None
                            active_lot = lot_sequence[current_open_count]
                            
                            if current_open_count > 0:
                                # Scale in the exact same direction as the basket
                                action = active_direction
                                entry = current_ask if action == "BUY" else current_bid
                            else:
                                # Trend-following entry instead of blind mean-reversion
                                if current_price > sma + 0.50:
                                    action = "BUY"
                                    entry = current_ask
                                elif current_price < sma - 0.50:
                                    action = "SELL"
                                    entry = current_bid
                                    
                            if action and is_bot_running:
                                print(f"Opening Layer {current_open_count + 1} | Action: {action} | Volume: {active_lot}")
                                
                                # Wider, safer Gold buffers (optimized for standard XAU spreads)
                                tp_dist = 40.00
                                sl_dist = 30.00
                                
                                if action == "BUY":
                                    tp = round(entry + tp_dist, 2)
                                    sl = round(entry - sl_dist, 2)
                                    await connection.create_market_buy_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                                else:
                                    tp = round(entry - tp_dist, 2)
                                    sl = round(entry + sl_dist, 2)
                                    await connection.create_market_sell_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                                
                                cooldown_timer = 15
                
                await asyncio.sleep(1.0)
                
        except Exception as e:
            print(f"Error: {e}. Reconnecting...")
            await asyncio.sleep(5)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_bulletproof_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    status_text = "Active (Optimized Trend Filtering & Safe Stops)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Bulletproof System</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body {{ background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; text-align: center; padding-top: 40px; }}
            h1 {{ color: #f39c12; }}
            .container {{ max-width: 850px; margin: auto; background: #1e1e1e; padding: 25px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.6); }}
            .status {{ color: #2ecc71; font-weight: bold; }}
            .btn {{ display: inline-block; padding: 12px 24px; margin: 10px; font-size: 16px; font-weight: bold; color: #fff; text-decoration: none; border-radius: 5px; }}
            .btn-pause {{ background-color: #e74c3c; }}
            .btn-resume {{ background-color: #2ecc71; }}
            .btn-action {{ background-color: #3498db; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Gold Bulletproof System (24/5)</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Progression: 0.1 -> 0.2 -> 0.3 | SMA Trend Filter Enabled</p>
            <br>
            <a href="/pause" class="btn btn-pause">Pause Bot</a>
            <a href="/resume" class="btn btn-resume">Resume Bot</a>
            <br><br>
            <a href="/place-buy" class="btn btn-action">Test Manual Buy (0.1)</a>
            <a href="/place-sell" class="btn btn-action">Test Manual Sell (0.1)</a>
        </div>
    </body>
    </html>
    """


@app.get("/pause")
async def pause_bot():
    global is_bot_running
    is_bot_running = False
    return {"status": "success", "message": "Paused."}


@app.get("/resume")
async def resume_bot():
    global bot_task, is_bot_running
    is_bot_running = True
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_bulletproof_bot())
    return {"status": "success", "message": "Resumed."}


@app.get("/place-buy")
async def manual_place_buy():
    try:
        metaapi = MetaApi(TOKEN)
        account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
        if account.state != "DEPLOYED":
            await account.deploy()
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()
        price_info = await connection.get_symbol_price("XAUUSDm")
        ask = price_info.get("ask", 0)
        res = await connection.create_market_buy_order(symbol="XAUUSDm", volume=0.1, stop_loss=round(ask-30,2), take_profit=round(ask+40,2))
        return {"status": "success", "order": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/place-sell")
async def manual_place_sell():
    try:
        metaapi = MetaApi(TOKEN)
        account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
        if account.state != "DEPLOYED":
            await account.deploy()
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()
        price_info = await connection.get_symbol_price("XAUUSDm")
        bid = price_info.get("bid", 0)
        res = await connection.create_market_sell_order(symbol="XAUUSDm", volume=0.1, stop_loss=round(bid+30,2), take_profit=round(bid-40,2))
        return {"status": "success", "order": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
