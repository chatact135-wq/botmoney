import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Professional Scalping System")

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

            print("Bot connected to MetaApi feed. Monitoring micro-swings (35s loop | 0.03 Lots | $3.50 Target)...")

            last_known_price = None
            MAX_CONCURRENT_TRADES = 50

            while is_bot_running:
                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    
                    if last_known_price is not None:
                        price_delta = current_price - last_known_price
                        print(f"Current Price: {current_price} | Delta: {price_delta:.3f}")
                        
                        positions = await connection.get_positions()
                        current_open_count = len(positions)
                        
                        if current_open_count < MAX_CONCURRENT_TRADES:
                            spread_offset = 0.27
                            net_profit_target = 3.50  # Updated to $3.50 net profit target
                            total_tp_distance = spread_offset + net_profit_target
                            
                            action = None
                            if price_delta <= -0.02:
                                action = "BUY"
                                entry = current_ask
                                stop_loss = round(entry - 15.00, 2)
                                take_profit = round(entry + total_tp_distance, 2)
                            elif price_delta >= 0.02:
                                action = "SELL"
                                entry = current_bid
                                stop_loss = round(entry + 15.00, 2)
                                take_profit = round(entry - total_tp_distance, 2)
                                
                            if action:
                                slots_available = MAX_CONCURRENT_TRADES - current_open_count
                                burst_count = min(slots_available, 5) # Scale in waves of 5
                                
                                print(f"Trigger! Delta: {price_delta:.3f} -> Opening {burst_count} {action} orders at 0.03 lots.")
                                
                                if action == "BUY":
                                    tasks = [
                                        connection.create_market_buy_order(
                                            symbol="XAUUSDm", volume=0.03, stop_loss=stop_loss, take_profit=take_profit
                                        ) for _ in range(burst_count)
                                    ]
                                else:
                                    tasks = [
                                        connection.create_market_sell_order(
                                            symbol="XAUUSDm", volume=0.03, stop_loss=stop_loss, take_profit=take_profit
                                        ) for _ in range(burst_count)
                                    ]
                                    
                                await asyncio.gather(*tasks)
                    
                    last_known_price = current_price
                
                # Precise 35-second polling interval
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
    status_text = "Active (35s Loop | 0.03 Lots | $3.50 Target | Max 50)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Professional Scalper</title>
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
            <h1>Gold Professional Scalping System</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Execution: Live Broker Feed | 0.03 Lots | $3.50 Net Target | Max Cap: 50 Orders | 35s Loop</p>
            <p><em>Auto-refreshing dashboard every 15 seconds...</em></p>
        </div>
    </body>
    </html>
    """


@app.get("/test-order")
async def test_order():
    """Manual batch test endpoint verifying 0.03 lot execution."""
    try:
        metaapi = MetaApi(TOKEN)
        account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

        if account.state != "DEPLOYED":
            await account.deploy()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        tasks = [
            connection.create_market_buy_order(
                symbol="XAUUSDm", volume=0.03, stop_loss=0, take_profit=0
            ) for _ in range(3)
        ]
        results = await asyncio.gather(*tasks)
        return {"status": "success", "batch_count": len(results), "orders": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
