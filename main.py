import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold EMA Pullback & Safe Trailing Scalper")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def position_management_loop():
    """High-frequency 0.5-second trailing stop-loss engine with broker distance safety checks."""
    global is_bot_running, global_connection
    print("Safe High-Frequency Trailing Stop-Loss Engine online (0.5s)...")
    
    while is_bot_running:
        try:
            if global_connection and is_bot_running:
                positions = await global_connection.get_positions()
                
                # Fetch current market prices to validate stop-loss placement safety distances
                symbol_price = await global_connection.get_symbol_price("XAUUSDm")
                current_bid = symbol_price.get("bid")
                current_ask = symbol_price.get("ask")
                
                for pos in positions:
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    pos_type = pos.get("type")
                    open_py = pos.get("openPrice")
                    current_sl = pos.get("stopLoss", 0.0)
                    current_tp = pos.get("takeProfit", 0.0)
                    
                    is_buy = pos_type in [0, "POSITION_TYPE_BUY", "buy"]
                    is_sell = pos_type in [1, "POSITION_TYPE_SELL", "sell"]
                    
                    # Safe trailing logic with broker distance validation
                    if is_buy and current_bid:
                        desired_sl = None
                        if profit >= 25.0:
                            desired_sl = round(open_py + 1.20, 2)
                        elif profit >= 12.0:
                            desired_sl = round(open_py + 0.50, 2)
                        elif profit >= 1.0:
                            desired_sl = round(open_py + 0.05, 2)  # Break-even + buffer
                            
                        if desired_sl and current_sl < desired_sl:
                            # Ensure stop loss is safely below current bid price to prevent broker rejection
                            if desired_sl < current_bid - 0.20:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                
                    elif is_sell and current_ask:
                        desired_sl = None
                        if profit >= 25.0:
                            desired_sl = round(open_py - 1.20, 2)
                        elif profit >= 12.0:
                            desired_sl = round(open_py - 0.50, 2)
                        elif profit >= 1.0:
                            desired_sl = round(open_py - 0.05, 2)  # Break-even + buffer
                            
                        if desired_sl and (current_sl > desired_sl or current_sl == 0):
                            # Ensure stop loss is safely above current ask price to prevent broker rejection
                            if desired_sl > current_ask + 0.20:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                
        except Exception:
            pass
            
        await asyncio.sleep(0.5)


async def run_scalping_bot():
    global is_bot_running, global_connection, management_task
    is_bot_running = True
    
    lot_sequence = [0.1, 0.2, 0.5]
    MAX_CONCURRENT_TRADES = 10
    
    EMA_PERIOD = 14            
    PULLBACK_THRESHOLD = 0.15  
    
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

            print("EMA Pullback Scalper Active (Safe 0.5s Trailing SL & Fixed Target TP)...")

            price_history = []
            current_ema = None

            while is_bot_running:
                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    price_history.append(current_price)
                    
                    if len(price_history) > EMA_PERIOD * 2:
                        price_history.pop(0)
                        
                    if len(price_history) >= EMA_PERIOD and is_bot_running:
                        multiplier = 2 / (EMA_PERIOD + 1)
                        if current_ema is None:
                            current_ema = sum(price_history[-EMA_PERIOD:]) / EMA_PERIOD
                        else:
                            current_ema = (current_price * multiplier) + (current_ema * (1 - multiplier))
                            
                        macro_baseline = price_history[-min(len(price_history), 10)]
                        is_uptrend = current_price > macro_baseline
                        is_downtrend = current_price < macro_baseline
                        
                        distance_from_ema = current_price - current_ema
                        
                        positions = await connection.get_positions()
                        current_open_count = len(positions)
                        
                        if current_open_count < MAX_CONCURRENT_TRADES:
                            action = None
                            layer_index = min(current_open_count, len(lot_sequence) - 1)
                            active_lot_size = lot_sequence[layer_index]
                            
                            if is_uptrend and -0.05 <= distance_from_ema <= PULLBACK_THRESHOLD:
                                action = "BUY"
                                entry = current_ask
                            elif is_downtrend and -PULLBACK_THRESHOLD <= distance_from_ema <= 0.05:
                                action = "SELL"
                                entry = current_bid
                                
                            if action and is_bot_running:
                                print(f"EMA Pullback Trigger | Step {current_open_count + 1} | Action: {action} | Vol: {active_lot_size}")
                                
                                spread_offset = 0.27
                                net_dollar_target = 35.0  
                                price_move_target = net_dollar_target / (active_lot_size * 100)
                                total_tp_distance = round(spread_offset + price_move_target, 2)
                                
                                max_sl_distance = 8.0  # $400 risk cap per leg
                                
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
                
                await asyncio.sleep(1.0)  
                
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
    status_text = "Active (Safe 0.5s Trailing SL Engine + Capital Protection | Max Cap: 10)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Safe Trailing Scalper</title>
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
            <h1>Gold Safe Trailing Scalper (10 Cap)</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Execution: Volume Steps (0.1 -> 0.2 -> 0.5 max) | Safe 0.5s Real-Time Trailing Stop-Loss</p>
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
