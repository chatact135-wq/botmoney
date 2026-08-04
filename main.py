import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Fast-Scalp Grid (25-50$ Target)")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def position_management_loop():
    """1-second dynamic profit-locking engine: Trails stop losses upward when trades go green."""
    global is_bot_running, global_connection
    print("Dynamic Profit-Lock Trailing Engine online...")
    
    while is_bot_running:
        try:
            if global_connection and is_bot_running:
                positions = await global_connection.get_positions()
                for pos in positions:
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    pos_type = pos.get("type")
                    open_py = pos.get("openPrice")
                    current_sl = pos.get("stopLoss", 0.0)
                    current_tp = pos.get("takeProfit", 0.0)
                    
                    is_buy = pos_type in [0, "POSITION_TYPE_BUY", "buy"]
                    is_sell = pos_type in [1, "POSITION_TYPE_SELL", "sell"]
                    
                    # Quick profit-locking tiers for fast scalps
                    if is_buy:
                        if profit >= 2.0:
                            desired_sl = round(open_py + 0.20, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                    elif is_sell:
                        if profit >= 2.0:
                            desired_sl = round(open_py - 0.20, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                
        except Exception:
            pass
            
        await asyncio.sleep(1)


async def run_scalping_bot():
    global is_bot_running, global_connection, management_task
    is_bot_running = True
    
    lot_sequence = [0.1, 0.2, 0.5]
    MAX_CONCURRENT_TRADES = 10
    
    # Fast-Scalp Parameters: Responsive short window, lower trigger for quick $25-$50 hits
    FAST_WINDOW = 8            # Short lookback for rapid reaction
    MIN_POINT_TRIGGER = 0.35   # Triggers on a fast 0.35+ point push (clears spread quickly)
    
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

            print("Fast-Scalp Grid Active (Targeting Quick $25-$50 Moves)...")

            price_history = []

            while is_bot_running:
                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    price_history.append(current_price)
                    
                    if len(price_history) > FAST_WINDOW:
                        price_history.pop(0)
                        
                    if len(price_history) == FAST_WINDOW and is_bot_running:
                        # Compare current price to 5 ticks ago for fast momentum
                        baseline_price = price_history[0]
                        point_movement = current_price - baseline_price
                        
                        positions = await connection.get_positions()
                        current_open_count = len(positions)
                        
                        if current_open_count < MAX_CONCURRENT_TRADES:
                            action = None
                            layer_index = min(current_open_count, len(lot_sequence) - 1)
                            active_lot_size = lot_sequence[layer_index]
                            
                            if point_movement >= MIN_POINT_TRIGGER:
                                action = "BUY"
                                entry = current_ask
                            elif point_movement <= -MIN_POINT_TRIGGER:
                                action = "SELL"
                                entry = current_bid
                                
                            if action and is_bot_running:
                                print(f"Fast Scalp Trigger | Step {current_open_count + 1} | Action: {action} | Vol: {active_lot_size} | Move: {point_movement:.2f}pts")
                                
                                spread_offset = 0.27
                                # Target optimized for quick $25-$50 payout depending on lot size
                                net_dollar_target = 35.0  
                                price_move_target = net_dollar_target / (active_lot_size * 100)
                                total_tp_distance = round(spread_offset + price_move_target, 2)
                                
                                # Capped risk safety stop at 4.0 points max
                                max_sl_distance = 4.0 
                                
                                if action == "BUY":
                                    take_profit = round(entry + total_tp_distance, 2)
                                    stop_loss = round(entry - max_sl_distance, 2)
                                    await connection.create_market_buy_order(
                                        symbol="XAUUSDm", volume=active_lot_size, stop_loss=stop_loss, take_profit=take_profit
                                    )
                                else:
                                    take_profit = round(entry - total_tp_distance, 2)
                                    stop_loss = round(entry + max_sl_distance, 2)
                                    await connection.create_market_sell_order(
                                        symbol="XAUUSDm", volume=active_lot_size, stop_loss=stop_loss, take_profit=take_profit
                                    )
                
                await asyncio.sleep(1.0)  # Fast polling for rapid execution
                
        except Exception as e:
            print(f"Connection or loop error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_scalping_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    status_text = "Active (Fast-Scalp Grid ($25-$50 Target) | Max Cap: 10)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Fast-Scalp Bot</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body {{ background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; text-align: center; padding-top: 50px; }}
            h1 {{ color: #f39c12; }}
            .container {{ max-width: 800px; margin: auto; background: #1e1e1e; padding: 20px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }}
            .status {{ color: #2ecc71; font-weight: bold; }}
            .btn {{ display: inline-block; padding: 10px 20px; margin: 10px; font-size: 16px; font-weight: bold; color: #fff; text-decoration: none; border-radius: 5px; }}
            .btn-pause {{ background-color: #e74c3c; }}
            .btn-resume {{ background-color: #2ecc71; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Gold Fast-Scalp Bot (10 Cap)</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Execution: Volume Steps (0.1 -> 0.2 -> 0.5 max) | Fast 0.35pt Trigger for Quick $25-$50 Payouts</p>
            <br>
            <a href="/pause" class="btn btn-pause">Pause Bot</a>
            <a href="/resume" class="btn btn-resume">Resume Bot</a>
            <p style="margin-top:20px; font-size:12px; color:#888;"><em>Auto-refreshing dashboard every 15 seconds...</em></p>
        </div>
    </body>
    </html>
    """


@app.get("/pause")
async def pause_bot():
    """Temporarily pause the trading bot."""
    global is_bot_running
    is_bot_running = False
    return {"status": "success", "message": "Bot has been paused."}


@app.get("/resume")
async def resume_bot():
    """Resume the trading bot."""
    global bot_task, is_bot_running
    is_bot_running = True
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_scalping_bot())
    return {"status": "success", "message": "Bot has been resumed."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
