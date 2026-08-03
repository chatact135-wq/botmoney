import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Professional Adaptive Scalper")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
is_bot_running = False

async def run_scalping_bot():
    global is_bot_running
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

            print("Adaptive Scalper active. Monitoring trend strength and micro-swings...")

            price_history = []
            MAX_CONCURRENT_TRADES = 50

            while is_bot_running:
                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    price_history.append(current_price)
                    
                    # Keep a rolling window of the last 10 ticks to track momentum and trend
                    if len(price_history) > 10:
                        price_history.pop(0)
                        
                    if len(price_history) == 10:
                        # Fast Micro-EMA (5) and Trend Baseline (10)
                        micro_ema = sum(price_history[-5:]) / 5.0
                        trend_baseline = sum(price_history) / 10.0
                        
                        price_deviation = current_price - micro_ema
                        
                        # Trend Detection: Check if recent price action is aggressively moving away from baseline
                        net_trend_move = current_price - price_history[0]
                        
                        # Thresholds for Aggressive Trend Detection
                        is_aggressive_uptrend = net_trend_move >= 0.25  # Market pushed up aggressively over recent checks
                        is_aggressive_downtrend = net_trend_move <= -0.25 # Market pushed down aggressively
                        
                        print(f"Price: {current_price} | Net Trend Move: {net_trend_move:.2f} | Uptrend Filter: {is_aggressive_uptrend}")
                        
                        positions = await connection.get_positions()
                        current_open_count = len(positions)
                        
                        if current_open_count < MAX_CONCURRENT_TRADES:
                            lot_size = 0.03
                            spread_offset = 0.27
                            net_dollar_target = 12.50
                            
                            price_move_target = net_dollar_target / (lot_size * 100)
                            total_tp_distance = round(spread_offset + price_move_target, 2)
                            
                            action = None
                            
                            if is_aggressive_uptrend:
                                # AGGRESSIVE UPTREND DETECTED: Block Sells, only take BUY momentum/pullbacks
                                if price_deviation <= -0.02: # Buy the tiny dips in a bull trend
                                    action = "BUY"
                                    entry = current_ask
                                    stop_loss = round(entry - 15.00, 2)
                                    take_profit = round(entry + total_tp_distance, 2)
                                    print("Trend-Protected Mode: Aggressive Uptrend -> Taking BUY only.")
                                    
                            elif is_aggressive_downtrend:
                                # AGGRESSIVE DOWNTREND DETECTED: Block Buys, only take SELL momentum/rallies
                                if price_deviation >= 0.02: # Sell the tiny rallies in a bear trend
                                    action = "SELL"
                                    entry = current_bid
                                    stop_loss = round(entry + 15.00, 2)
                                    take_profit = round(entry - total_tp_distance, 2)
                                    print("Trend-Protected Mode: Aggressive Downtrend -> Taking SELL only.")
                                    
                            else:
                                # SIDEWAYS / NORMAL MARKET: Safe standard scalping (Fade micro-swings)
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
                                    print("Sideways Mode: Standard micro-swing scalping active.")
                                    
                            if action:
                                slots_available = MAX_CONCURRENT_TRADES - current_open_count
                                burst_count = min(slots_available, 5)
                                
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
                
                # 35-second high-frequency loop check
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
    status_text = "Active (35s Loop | Trend-Adaptive Scalper | Max 50)" if is_bot_running else "Paused"
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
            <p>Execution: Trend-Aware Filter | 0.03 Lots | $12.50 Target | Max Cap: 50 Orders | 35s Loop</p>
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

        test_tp = round(ask + 4.43, 2)
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
