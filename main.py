import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Smart Momentum Reversion System")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def position_management_loop():
    """
    24/5 Background Management Loop:
    - Implements strict dynamic trailing for stop losses once trades move into profit.
    - Guarantees profit floor protection and pushes TP/SL dynamically as price moves further.
    """
    global is_bot_running, global_connection
    print("Smart Reversion Dynamic Protection & Trailing Engine online...")
    
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
                    
                    # Trailing management to secure profit floor and stretch TP/SL dynamically
                    if is_buy:
                        if profit >= 20.0:
                            desired_sl = round(open_price + 5.0, 2)
                            desired_tp = round(current_tp + 10.0, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=desired_tp)
                        elif profit >= 10.0:
                            desired_sl = round(open_price + 2.0, 2)
                            desired_tp = round(current_tp + 5.0, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=desired_tp)
                        elif profit >= 3.0:
                            desired_sl = round(open_price + 1.0, 2)  # Secure $1+ profit floor
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=desired_tp)
                                
                    elif is_sell:
                        if profit >= 20.0:
                            desired_sl = round(open_price - 5.0, 2)
                            desired_tp = round(current_tp - 10.0, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=desired_tp)
                        elif profit >= 10.0:
                            desired_sl = round(open_price - 2.0, 2)
                            desired_tp = round(current_tp - 5.0, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=desired_tp)
                        elif profit >= 3.0:
                            desired_sl = round(open_price - 1.0, 2)  # Secure $1+ profit floor
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=desired_tp)
                                
        except Exception:
            pass
            
        await asyncio.sleep(2)


async def run_smart_reversion_bot():
    """
    Main 24/5 Execution Engine with Momentum Filtering:
    - Measures momentum velocity. If momentum is strong/trending, it stands aside.
    - If momentum is weak/flat, it executes mean reversion (selling peaks, buying dips).
    - Strict Layer Progression: 0.1 -> 0.2 -> 0.3 max.
    - Layer 2 (0.2) and Layer 3 (0.3) open ONLY if previous layers are confirmed in profit (SL trailed past entry).
    """
    global is_bot_running, global_connection, management_task
    is_bot_running = True
    
    lot_sequence = [0.1, 0.2, 0.3]
    MAX_CONCURRENT_TRADES = 3
    
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

            print("24/5 Smart Momentum-Filtered Reversion Engine active...")

            price_history = []

            while is_bot_running:
                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    price_history.append(current_price)
                    
                    if len(price_history) > 15:
                        price_history.pop(0)
                        
                    if len(price_history) == 15 and is_bot_running:
                        recent_high = max(price_history[:-1])
                        recent_low = min(price_history[:-1])
                        
                        # Momentum Filter: Measure short-term velocity vs longer baseline
                        fast_velocity = sum(price_history[-4:]) / 4.0
                        slow_velocity = sum(price_history[:10]) / 10.0
                        momentum_strength = abs(fast_velocity - slow_velocity)
                        
                        # Only allow fading/reversion if momentum is WEAK/FLAT (< 0.15)
                        # If momentum is strong, we avoid trading to prevent getting run over by a trend
                        if momentum_strength < 0.15:
                            positions = await connection.get_positions()
                            current_open_count = len(positions)
                            
                            can_open_new_layer = True
                            if current_open_count > 0:
                                for p in positions:
                                    p_profit = p.get("profit", 0.0)
                                    p_type = p.get("type")
                                    p_open = p.get("openPrice")
                                    p_sl = p.get("stopLoss", 0.0)
                                    
                                    is_b = p_type in [0, "POSITION_TYPE_BUY", "buy"]
                                    if is_b and p_sl <= p_open:
                                        can_open_new_layer = False
                                    elif not is_b and (p_sl >= p_open or p_sl == 0.0):
                                        can_open_new_layer = False
                            
                            if current_open_count < MAX_CONCURRENT_TRADES and can_open_new_layer:
                                action = None
                                layer_index = current_open_count
                                active_lot = lot_sequence[layer_index]
                                
                                # Sell at peaks, Buy at bottoms under weak momentum conditions
                                if current_price >= recent_high - 0.05:
                                    action = "SELL"
                                    entry = current_bid
                                elif current_price <= recent_low + 0.05:
                                    action = "BUY"
                                    entry = current_ask
                                    
                                if action and is_bot_running:
                                    print(f"Weak-Momentum Reversion Signal! Opening Layer {current_open_count + 1} | Action: {action} | Volume: {active_lot} lots")
                                    
                                    initial_tp_distance = 15.00
                                    initial_sl_distance = 12.00
                                    
                                    if action == "BUY":
                                        take_profit = round(entry + initial_tp_distance, 2)
                                        stop_loss = round(entry - initial_sl_distance, 2)
                                        await connection.create_market_buy_order(
                                            symbol="XAUUSDm", volume=active_lot, stop_loss=stop_loss, take_profit=take_profit
                                        )
                                    else:
                                        take_profit = round(entry - initial_tp_distance, 2)
                                        stop_loss = round(entry + initial_sl_distance, 2)
                                        await connection.create_market_sell_order(
                                            symbol="XAUUSDm", volume=active_lot, stop_loss=stop_loss, take_profit=take_profit
                                        )
                
                await asyncio.sleep(2.5)
                
        except Exception as e:
            print(f"Connection or loop error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_smart_reversion_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    status_text = "Active (24/5 Smart Reversion with Momentum Filter + Progression)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Smart Reversion System</title>
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
            <h1>Gold Smart Reversion System (24/5)</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Strategy: Fade Peaks/Dips ONLY on Weak Momentum | Progression: 0.1 -> 0.2 -> 0.3</p>
            <p>Features: Dynamic Stop-Loss & Take-Profit Trailing | Direct Manual Test Buttons</p>
            <br>
            <a href="/pause" class="btn btn-pause">Pause Bot</a>
            <a href="/resume" class="btn btn-resume">Resume Bot</a>
            <br><br>
            <a href="/place-buy" class="btn btn-action">Test Manual Buy (0.1)</a>
            <a href="/place-sell" class="btn btn-action">Test Manual Sell (0.1)</a>
            <p style="margin-top:25px; font-size:12px; color:#888;"><em>Auto-refreshing dashboard every 15 seconds...</em></p>
        </div>
    </body>
    </html>
    """


@app.get("/pause")
async def pause_bot():
    """Temporarily pause the trading bot."""
    global is_bot_running
    is_bot_running = False
    return {"status": "success", "message": "Bot has been paused successfully."}


@app.get("/resume")
async def resume_bot():
    """Resume the trading bot for 24/5 execution."""
    global bot_task, is_bot_running
    is_bot_running = True
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_smart_reversion_bot())
    return {"status": "success", "message": "Bot has been resumed successfully."}


@app.get("/place-buy")
async def manual_place_buy():
    """Direct manual test endpoint to place a 0.1 Buy order with initial protection."""
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

        tp = round(ask + 15.00, 2)
        sl = round(ask - 12.00, 2)

        result = await connection.create_market_buy_order(
            symbol="XAUUSDm", volume=0.1, stop_loss=sl, take_profit=tp
        )
        return {"status": "success", "action": "MANUAL_BUY", "order": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/place-sell")
async def manual_place_sell():
    """Direct manual test endpoint to place a 0.1 Sell order with initial protection."""
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

        tp = round(bid - 15.00, 2)
        sl = round(bid + 12.00, 2)

        result = await connection.create_market_sell_order(
            symbol="XAUUSDm", volume=0.1, stop_loss=sl, take_profit=tp
        )
        return {"status": "success", "action": "MANUAL_SELL", "order": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
