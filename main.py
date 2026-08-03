import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi
from engines import fetch_live_tick_data, scalping_engine

app = FastAPI(title="Gold Professional Scalping System")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
is_bot_running = False

async def run_scalping_bot():
    global is_bot_running
    is_bot_running = True
    
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != "DEPLOYED":
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    print("Professional Scalping Bot loop initialized. Refresh rate: 30 seconds.")

    MAX_CONCURRENT_TRADES = 10  # Strict cap of 10 orders max

    while is_bot_running:
        try:
            df = fetch_live_tick_data(symbol="XAU/USD")
            
            if df is not None and len(df) >= 5:
                positions = await connection.get_positions()
                current_open_count = len(positions)
                
                # Check if we are safely below our 10-trade limit
                if current_open_count < MAX_CONCURRENT_TRADES:
                    signal = scalping_engine(df)
                    
                    if signal:
                        action = signal["action"]
                        lot_size = signal["lot_size"]
                        stop_loss = signal["stop_loss"]
                        take_profit = signal["take_profit"]
                        
                        slots_available = MAX_CONCURRENT_TRADES - current_open_count
                        # Open up to 3 orders per swing trigger to maintain smooth scaling
                        burst_count = min(slots_available, 3)
                        
                        print(f"Signal detected ({signal['reason']}). Executing parallel batch of {burst_count} orders...")
                        
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
                            
                        # Execute concurrently via asyncio to prevent network latency bottlenecks
                        await asyncio.gather(*tasks)
                        print("Batch execution successful.")
                        
        except Exception as e:
            print(f"Error in professional bot loop: {e}")
            
        # Precise 30-second loop refresh interval as requested
        await asyncio.sleep(30)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_scalping_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    status_text = "Active (30s Refresh | Max 10 Orders | $1 Target)" if is_bot_running else "Paused"
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
            <p>Strategy: Micro-swing up/down detection | $1.00 Net Target | 30s Refresh Loop</p>
            <p><em>Auto-refreshing dashboard every 15 seconds...</em></p>
        </div>
    </body>
    </html>
    """


@app.get("/test-order")
async def test_order():
    """Manual test endpoint to check execution functionality."""
    try:
        metaapi = MetaApi(TOKEN)
        account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

        if account.state != "DEPLOYED":
            await account.deploy()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        result = await connection.create_market_buy_order(
            symbol="XAUUSDm", volume=0.01, stop_loss=0, take_profit=0
        )
        return {"status": "success", "order": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
