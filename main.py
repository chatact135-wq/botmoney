import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Micro-Scalp Instant Profit Engine")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def instant_profit_closer_loop():
    """Ultra-fast background worker running every 0.5 seconds to instantly close ANY trade in micro-profit."""
    global is_bot_running, global_connection
    print("Micro-Profit Instant Closer online...")
    
    while is_bot_running:
        try:
            if global_connection:
                positions = await global_connection.get_positions()
                for pos in positions:
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    
                    # Close the trade the exact microsecond it hits a tiny positive profit (e.g., $0.05 or more)
                    if profit >= 0.05:
                        await global_connection.close_position(pos_id)
        except Exception:
            pass
            
        await asyncio.sleep(0.5)


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

            if not management_task or management_task.done():
                management_task = asyncio.create_task(instant_profit_closer_loop())

            print("Unlimited Micro-Scalper active...")

            price_history = []

            while is_bot_running:
                price_info = await connection.get_symbol_price("XAUUSDm")
                current_bid = price_info.get("bid")
                current_ask = price_info.get("ask")
                
                if current_bid and current_ask:
                    current_price = (current_bid + current_ask) / 2.0
                    price_history.append(current_price)
                    
                    if len(price_history) > 4:
                        price_history.pop(0)
                        
                    if len(price_history) == 4:
                        # Ultra-sensitive micro velocity check to fire trades constantly
                        tick_move = current_price - price_history[0]
                        
                        lot_size = 0.03
                        spread_offset = 0.27
                        # Large take profit so it relies entirely on the instant closer loop for tiny profits
                        fake_tp_distance = 15.00 
                        
                        action = None
                        if tick_move >= 0.02:
                            action = "BUY"
                            entry = current_ask
                            stop_loss = round(entry - 15.00, 2)
                            take_profit = round(entry + fake_tp_distance, 2)
                        elif tick_move <= -0.02:
                            action = "SELL"
                            entry = current_bid
                            stop_loss = round(entry + 15.00, 2)
                            take_profit = round(entry - fake_tp_distance, 2)
                            
                        if action:
                            burst_count = 3
                            print(f"Opening burst of {burst_count} {action} orders (Unlimited Mode).")
                            
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
                
                # Super fast scan interval to feed trades continuously
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
    status_text = "Active (Unlimited Micro-Scalp | Instant Tiny-Profit Closer)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Unlimited Micro-Scalper</title>
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
            <h1>Gold Unlimited Micro-Scalping System</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Execution: No Trade Cap | 0.5s Tiny-Profit Closer ($0.05+) | 0.03 Lots</p>
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

        test_tp = round(ask + 10.00, 2)
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
