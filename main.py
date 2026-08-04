import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Zero-Stop 250-Cap Micro-Scalper")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def zero_stop_closer_loop():
    """Ultra-fast background loop (0.3s) with NO stop loss. Closes every single trade the millisecond it touches green."""
    global is_bot_running, global_connection
    print("Zero-Stop Instant Micro-Closer online...")
    
    while is_bot_running:
        try:
            if global_connection:
                positions = await global_connection.get_positions()
                for pos in positions:
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    
                    # Close the moment profit is greater than or equal to $0.01
                    if profit >= 0.01:
                        await global_connection.close_position(pos_id)
        except Exception:
            pass
            
        await asyncio.sleep(0.3)


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
                management_task = asyncio.create_task(zero_stop_closer_loop())

            print("Zero-Stop Scalper active with Max Cap: 250...")

            price_history = []
            MAX_CONCURRENT_TRADES = 250

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
                        tick_move = current_price - price_history[0]
                        
                        positions = await connection.get_positions()
                        current_open_count = len(positions)
                        
                        if current_open_count < MAX_CONCURRENT_TRADES:
                            lot_size = 0.03
                            fake_tp = 20.00
                            
                            action = None
                            if tick_move >= 0.03:
                                action = "BUY"
                                entry = current_ask
                                stop_loss = 0.0  # NO STOP LOSS
                                take_profit = round(entry + fake_tp, 2)
                            elif tick_move <= -0.03:
                                action = "SELL"
                                entry = current_bid
                                stop_loss = 0.0  # NO STOP LOSS
                                take_profit = round(entry - fake_tp, 2)
                                
                            if action:
                                slots_available = MAX_CONCURRENT_TRADES - current_open_count
                                burst_count = min(slots_available, 2)
                                
                                print(f"Opening {burst_count} {action} orders with ZERO stop loss. Active count: {current_open_count}")
                                
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
                
                await asyncio.sleep(0.8)
                
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
    status_text = "Active (Zero-Stop | Instant $0.01 Closer | Max Cap: 250)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Zero-Stop 250-Cap Scalper</title>
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
            <h1>Gold Zero-Stop Micro-Scalper (250 Cap)</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Execution: No Stop Loss | 0.3s Instant Closer ($0.01+) | Max Cap: 250 Orders</p>
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

        tasks = [
            connection.create_market_buy_order(
                symbol="XAUUSDm", volume=0.03, stop_loss=0.0, take_profit=test_tp
            ) for _ in range(2)
        ]
        results = await asyncio.gather(*tasks)
        return {"status": "success", "batch_count": len(results), "orders": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
