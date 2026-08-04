import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Adaptive Scalper with Dedicated 3s Trailing Engine")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def position_management_loop():
    """Dedicated background worker running strictly every 3 seconds to update trailing stop losses."""
    global is_bot_running, global_connection
    print("Dedicated 3-Second Trailing SL Manager started...")
    
    while is_bot_running:
        try:
            if global_connection:
                positions = await global_connection.get_positions()
                for pos in positions:
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    pos_type = pos.get("type") # Can be string or numeric depending on SDK payload
                    open_py = pos.get("openPrice")
                    current_sl = pos.get("stopLoss", 0.0)
                    current_tp = pos.get("takeProfit", 0.0)
                    
                    # Normalize position type check (handles both string and int representations from MetaApi)
                    is_buy = pos_type in [0, "POSITION_TYPE_BUY", "buy"]
                    is_sell = pos_type in [1, "POSITION_TYPE_SELL", "sell"]
                    
                    if is_buy:
                        if profit >= 6.0:
                            desired_sl = round(open_py + 1.50, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                print(f"[3s Trailing] BUY {pos_id} profit reached ${profit:.2f} -> Trailed SL to {desired_sl}")
                        elif profit >= 4.0:
                            desired_sl = round(open_py + 1.00, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                print(f"[3s Trailing] BUY {pos_id} profit reached ${profit:.2f} -> Trailed SL to {desired_sl}")
                        elif profit >= 1.25:
                            desired_sl = round(open_py + 0.35, 2) # Breakeven + spread buffer
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                print(f"[3s Trailing] BUY {pos_id} secured at breakeven/profit SL -> {desired_sl}")
                                
                    elif is_sell:
                        if profit >= 6.0:
                            desired_sl = round(open_py - 1.50, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                print(f"[3s Trailing] SELL {pos_id} profit reached ${profit:.2f} -> Trailed SL to {desired_sl}")
                        elif profit >= 4.0:
                            desired_sl = round(open_py - 1.00, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                print(f"[3s Trailing] SELL {pos_id} profit reached ${profit:.2f} -> Trailed SL to {desired_sl}")
                        elif profit >= 1.25:
                            desired_sl = round(open_py - 0.35, 2) # Breakeven + spread buffer
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                print(f"[3s Trailing] SELL {pos_id} secured at breakeven/profit SL -> {desired_sl}")
                                
        except Exception as e:
            # Suppress minor connection blips during fast checks
            pass
            
        await asyncio.sleep(3)  # Hard 3-second cadence


async def run_scalping_bot():
    global is_bot_running, global_connection, management_task
    is_bot_running = True
    
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

            # Start the independent 3-second trailing engine if not running
            if not management_task or management_task.done():
                management_task = asyncio.create_task(position_management_loop())

            print("Adaptive Scalper active. Monitoring trends and entries (Max Cap: 75)...")

            price_history = []
            MAX_CONCURRENT_TRADES = 75  # Updated cap to 75 trades

            while is_bot_running:
                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    price_history.append(current_price)
                    
                    if len(price_history) > 10:
                        price_history.pop(0)
                        
                    if len(price_history) == 10:
                        micro_ema = sum(price_history[-5:]) / 5.0
                        price_deviation = current_price - micro_ema
                        net_trend_move = current_price - price_history[0]
                        
                        is_aggressive_uptrend = net_trend_move >= 0.25
                        is_aggressive_downtrend = net_trend_move <= -0.25
                        
                        positions = await connection.get_positions()
                        current_open_count = len(positions)
                        
                        if current_open_count < MAX_CONCURRENT_TRADES:
                            lot_size = 0.03
                            spread_offset = 0.27
                            net_dollar_target = 5.00  
                            
                            price_move_target = net_dollar_target / (lot_size * 100)
                            total_tp_distance = round(spread_offset + price_move_target, 2)
                            
                            action = None
                            
                            if is_aggressive_uptrend:
                                if price_deviation <= -0.02:
                                    action = "BUY"
                                    entry = current_ask
                                    stop_loss = round(entry - 15.00, 2)
                                    take_profit = round(entry + total_tp_distance, 2)
                            elif is_aggressive_downtrend:
                                if price_deviation >= 0.02:
                                    action = "SELL"
                                    entry = current_bid
                                    stop_loss = round(entry + 15.00, 2)
                                    take_profit = round(entry - total_tp_distance, 2)
                            else:
                                if price_deviation >= 0.08:
                                    action = "SELL"
                                    entry = current_bid
                                    stop_loss = round(entry + 15.00, 2)
                                    take_profit = round(entry - total_tp_distance, 2)
                                elif price_deviation <= -0.08:
                                    action = "BUY"
                                    entry = current_ask
                                    stop_loss = round(entry - 15.00, 2)
                                    take_profit = round(entry + total_tp_distance, 2)
                                    
                            if action:
                                slots_available = MAX_CONCURRENT_TRADES - current_open_count
                                burst_count = min(slots_available, 3)
                                
                                print(f"Executing {burst_count} {action} orders at {lot_size} lots.")
                                
                                if action == "BUY":
                                    tasks = [
                                        connection.create_market_buy_order(
                                            symbol="XAUUSDm", volume=lot_size, stop_loss=stop_loss, take_profit=take_profit
                                        ) for _ in range(burst_count)
                                    ]
                                else:
                                    tasks = [
                                        connection.create_market_sell_order(
                                            symbol="XAUUSDm", volume=lot_size, stop_loss=stop_loss, take_profit=take_profit
                                        ) for _ in range(burst_count)
                                    ]
                                    
                                await asyncio.gather(*tasks)
                
                # Main entry loop polling interval
                await asyncio.sleep(35)
                
        except Exception as e:
            print(f"Connection or loop error: {e}. Reconnecting in 10 seconds...")
            await asyncio.sleep(10)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_scalping_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    status_text = "Active (Dedicated 3s Trailing SL Engine | Max 75 Cap)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Adaptive Scalper</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body {{ background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; text-align: center; padding-top: 50px; }}
            h1 {{ color: #f39c12; }}
            .container {{ max-width: 800px; margin: auto; background: #1e1e1e; padding: 20px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }}
            .status {{ color: #2ecc71; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Gold Adaptive Scalping System</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Execution: Dedicated 3s Trailing SL Worker | 0.03 Lots | Max Cap: 75 Orders</p>
            <p><em>Auto-refreshing dashboard every 15 seconds...</em></p>
        </div>
    </body>
    </html>
    """


@app.get("/test-order")
async def test_order():
    """Manual batch test endpoint."""
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

        test_tp = round(ask + 2.00, 2)
        test_sl = round(ask - 15.00, 2)

        tasks = [
            connection.create_market_buy_order(
                symbol="XAUUSDm", volume=0.03, stop_loss=test_sl, take_profit=test_tp
            ) for _ in range(3)
        ]
        results = await asyncio.gather(*tasks)
        return {"status": "success", "batch_count": len(results), "orders": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
