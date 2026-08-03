import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi
from engines import fetch_live_tick_data, scalping_engine

app = FastAPI(title="Gold Aggressive Scalping System")

# MetaApi Credentials & Configuration
TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

# Global tracking for background bot loop
bot_task = None
is_bot_running = False

async def run_scalping_bot():
    """Background task for high-frequency multi-position execution."""
    global is_bot_running
    is_bot_running = True
    
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != "DEPLOYED":
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    print("Aggressive Scalping bot background loop started.")

    MAX_CONCURRENT_TRADES = 10  # Target up to 8-10 trades at once

    while is_bot_running:
        try:
            # 1. Fetch live 1-minute tick data for Gold
            df = fetch_live_tick_data(symbol="XAU/USD")
            
            if df is not None and len(df) >= 20:
                # Check current open positions on Exness
                positions = await connection.get_positions()
                current_open_count = len(positions)
                
                # Optional: Manage profit checks or let MetaTrader TP handle the $0.55 target automatically.
                # Since we set exact Take Profit points in the engine, MT5 will auto-close them at target.

                # 2. If we have room for more trades, look for signals
                if current_open_count < MAX_CONCURRENT_TRADES:
                    signal = scalping_engine(df)
                    
                    if signal:
                        action = signal["action"]
                        lot_size = signal["lot_size"]
                        stop_loss = signal["stop_loss"]
                        take_profit = signal["take_profit"]
                        
                        # Calculate how many trades to open in a burst to reach target count (up to 8-10)
                        slots_available = MAX_CONCURRENT_TRADES - current_open_count
                        burst_count = min(slots_available, 4) # Open up to 4 per signal wave to fill up quickly
                        
                        print(f"Signal found: {action}. Opening burst of {burst_count} trades...")
                        
                        for _ in range(burst_count):
                            if action == "BUY":
                                await connection.create_market_buy_order(
                                    symbol="XAUUSDm",
                                    volume=lot_size,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                )
                            elif action == "SELL":
                                await connection.create_market_sell_order(
                                    symbol="XAUUSDm",
                                    volume=lot_size,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                )
                            # Tiny pause between rapid orders to prevent broker rejection rate limits
                            await asyncio.sleep(0.5)
                            
        except Exception as e:
            print(f"Error in aggressive bot loop: {e}")
            
        # Check market every 10 seconds for high-frequency reaction speed
        await asyncio.sleep(10)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_scalping_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    status_text = "Running Aggressive Mode (8-10 Trades)" if is_bot_running else "Paused"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Aggressive Scalper</title>
        <meta http-equiv="refresh" content="10">
        <style>
            body {{ background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; text-align: center; padding-top: 50px; }}
            h1 {{ color: #e74c3c; }}
            .container {{ max-width: 800px; margin: auto; background: #1e1e1e; padding: 20px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }}
            .status {{ color: #f39c12; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Gold Aggressive Multi-Trade Scalper</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Target: 8-10 simultaneous trades | Spread-adjusted $0.55 net profit target per trade</p>
            <p><em>Auto-refreshing every 10 seconds...</em></p>
        </div>
    </body>
    </html>
    """
    return html_content


@app.get("/test-order")
async def test_order():
    """Test manual batch execution."""
    try:
        metaapi = MetaApi(TOKEN)
        account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

        if account.state != "DEPLOYED":
            await account.deploy()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        results = []
        for _ in range(3):
            res = await connection.create_market_buy_order(
                symbol="XAUUSDm",
                volume=0.01,
                stop_loss=0,
                take_profit=0,
            )
            results.append(res)
            await asyncio.sleep(0.3)

        return {"status": "success", "batch_orders": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
