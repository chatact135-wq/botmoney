import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Dynamic Progression System")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def position_management_loop():
    """
    High-Speed Trailing Engine (Checked every 0.3 seconds):
    - Instantly shifts Stop Loss to lock in profit the moment any trade moves into green.
    """
    global is_bot_running, global_connection
    print("High-Speed Trailing Engine online (0.3s interval)...")
    
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
                    
                    # High-speed lock as soon as profit is reached
                    if is_buy and profit >= 0.80:
                        desired_sl = round(open_price + 0.10, 2)
                        if current_sl < desired_sl:
                            await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                    elif is_sell and profit >= 0.80:
                        desired_sl = round(open_price - 0.10, 2)
                        if current_sl > desired_sl or current_sl == 0:
                            await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
        except Exception:
            pass
            
        await asyncio.sleep(0.3)


async def run_dynamic_bot():
    """
    Execution Engine:
    - Sequence: 0.1 -> 0.2 -> 0.3 max.
    - Opens layers based on market signals without waiting for prior trades to hit profit first.
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

            print("Dynamic Progression Engine active...")

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
                    
                    if len(price_history) > 20:
                        price_history.pop(0)
                        
                    if len(price_history) == 20 and is_bot_running:
                        recent_high = max(price_history[:-1])
                        recent_low = min(price_history[:-1])
                        
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
                            layer_index = current_open_count
                            active_lot = lot_sequence[layer_index]
                            
                            if current_open_count > 0:
                                # Match existing direction for the basket layers
                                action = active_direction
                                entry = current_ask if action == "BUY" else current_bid
                            else:
                                # New base signal from reversion extremes
                                if current_price >= recent_high - 0.02:
                                    action = "SELL"
                                    entry = current_bid
                                elif current_price <= recent_low + 0.02:
                                    action = "BUY"
                                    entry = current_ask
                                    
                            if action and is_bot_running:
                                print(f"Opening Layer {current_open_count + 1} | Action: {action} | Volume: {active_lot}")
                                
                                tp_dist = 20.00
                                sl_dist = 25.00
                                
                                if action == "BUY":
                                    tp = round(entry + tp_dist, 2)
                                    sl = round(entry - sl_dist, 2)
                                    await connection.create_market_buy_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                                else:
                                    tp = round(entry - tp_dist, 2)
                                    sl = round(entry + sl_dist, 2)
                                    await connection.create_market_sell_order(symbol="XAUUSDm", volume=active_lot, stop_loss=sl, take_profit=tp)
                                
                                cooldown_timer = 10
                
                await asyncio.sleep(1.0)
                
        except Exception as e:
            print(f"Error: {e}. Reconnecting...")
            await asyncio.sleep(5)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_dynamic_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    status_text = "Active (Dynamic Progression + 0.3s Trailing)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Dynamic Progression System</title>
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
            <h1>Gold Dynamic Progression System (24/5)</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Progression: 0.1 -> 0.2 -> 0.3 | 0.3s High-Speed Trailing</p>
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
        bot_task = asyncio.create_task(run_dynamic_bot())
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
        res = await connection.create_market_buy_order(symbol="XAUUSDm", volume=0.1, stop_loss=round(ask-25,2), take_profit=round(ask+20,2))
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
        res = await connection.create_market_sell_order(symbol="XAUUSDm", volume=0.1, stop_loss=round(bid+25,2), take_profit=round(bid-20,2))
        return {"status": "success", "order": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
