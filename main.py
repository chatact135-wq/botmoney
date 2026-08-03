import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi
from engines import fetch_live_tick_data, scalping_engine

app = FastAPI(title="Gold Scalping System")

# MetaApi Credentials & Configuration
TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

# Global tracking for background bot loop
bot_task = None
is_bot_running = False

async def run_scalping_bot():
    """Background task that continuously scans the market and executes trades based on the EMA strategy."""
    global is_bot_running
    is_bot_running = True
    
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != "DEPLOYED":
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    print("Scalping bot background loop started successfully.")

    while is_bot_running:
        try:
            # 1. Fetch live 1-minute tick data for Gold
            df = fetch_live_tick_data(symbol="XAUUSD")
            
            if df is not None and len(df) >= 20:
                # 2. Run your scalping strategy (5/13 EMA cross)
                signal = scalping_engine(df)
                
                if signal:
                    action = signal["action"]
                    lot_size = signal["lot_size"]
                    stop_loss = signal["stop_loss"]
                    take_profit = signal["take_profit"]
                    reason = signal["reason"]
                    
                    print(f"Signal detected: {action} | Reason: {reason}")
                    
                    # Check if any positions are already open to avoid over-trading
                    positions = await connection.get_positions()
                    if not positions:
                        if action == "BUY":
                            result = await connection.create_market_buy_order(
                                symbol="XAUUSDm",
                                volume=lot_size,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                            )
                            print(f"Executed BUY Order: {result}")
                        elif action == "SELL":
                            result = await connection.create_market_sell_order(
                                symbol="XAUUSDm",
                                volume=lot_size,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                            )
                            print(f"Executed SELL Order: {result}")
                    else:
                        print("Position already open. Skipping signal.")
                        
        except Exception as e:
            print(f"Error in scalping bot loop: {e}")
            
        # Poll the market every 30 seconds for quick scalping reactions
        await asyncio.sleep(30)


@app.on_event("startup")
async def startup_event():
    global bot_task
    init_db()
    # Automatically kick off the background trading loop when the app starts
    if not bot_task or bot_task.done():
        bot_task = asyncio.create_task(run_scalping_bot())


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    """Main monitoring dashboard displaying active and past scalp trades."""
    status_text = "Running & Scanning Market" if is_bot_running else "Paused/Stopped"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Scalping System</title>
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
            <h1>Gold Scalping System Dashboard</h1>
            <p>Bot Status: <span class="status">{status_text}</span></p>
            <p>Strategy: 5/13 EMA Cross with Spread-Adjusted Target (~$0.55 net target)</p>
            <p><em>Auto-refreshing every 15 seconds...</em></p>
        </div>
    </body>
    </html>
    """
    return html_content


@app.get("/test-order")
async def test_order():
    """Manual test endpoint to verify immediate trade execution."""
    try:
        metaapi = MetaApi(TOKEN)
        account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

        if account.state != "DEPLOYED":
            await account.deploy()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        result = await connection.create_market_buy_order(
            symbol="XAUUSDm",
            volume=0.01,
            stop_loss=0,
            take_profit=0,
        )
        return {"status": "success", "order": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
