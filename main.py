import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold 250-Cap Profit-Lock Scalper")

TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

bot_task = None
management_task = None
is_bot_running = False
global_connection = None

async def position_management_loop():
    """1-second trailing SL engine: ONLY locks in profit milestones, never cuts losing trades early."""
    global is_bot_running, global_connection
    print("Profit-Lock Trailing Engine online...")
    
    while is_bot_running:
        try:
            if global_connection:
                positions = await global_connection.get_positions()
                for pos in positions:
                    pos_id = pos.get("id")
                    profit = pos.get("profit", 0.0)
                    pos_type = pos.get("type")
                    open_py = pos.get("openPrice")
                    current_sl = pos.get("stopLoss", 0.0)
                    current_tp = pos.get("takeProfit", 0.0)
                    
                    is_buy = pos_type in [0, "POSITION_TYPE_BUY", "buy"]
                    is_sell = pos_type in [1, "POSITION_TYPE_SELL", "sell"]
                    
                    # Only modify stop loss upward/downward to lock profit when trade is GREEN
                    if is_buy:
                        if profit >= 4.0:
                            desired_sl = round(open_py + 0.80, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                        elif profit >= 2.0:
                            desired_sl = round(open_py + 0.40, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                        elif profit >= 0.80:
                            desired_sl = round(open_py + 0.15, 2)
                            if current_sl < desired_sl:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                
                    elif is_sell:
                        if profit >= 4.0:
                            desired_sl = round(open_py - 0.80, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                        elif profit >= 2.0:
                            desired_sl = round(open_py - 0.40, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                        elif profit >= 0.80:
                            desired_sl = round(open_py - 0.15, 2)
                            if current_sl > desired_sl or current_sl == 0:
                                await global_connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                
        except Exception:
            pass
            
        await asyncio.sleep(1)


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
                management_task = asyncio.create_task(position_management_loop())

            print("Profit-Lock Scalper active with Max Cap: 250...")

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
                            spread_offset = 0.27
                            net_dollar_target = 3.0  
                            
                            price_move_target = net_dollar_target / (lot_size * 100)
                            total_tp_distance = round(spread_offset + price_move_target, 2)
                            
                            action = None
                            if tick_move >= 0.03:
                                action = "BUY"
                                entry = current_ask
                                stop_loss = round(entry - 12.00, 2)
                                take_profit = round(entry + total_tp_distance, 2)
                            elif tick_move <= -0.03:
                                action = "SELL"
                                entry = current_bid
                                stop_loss = round(entry + 12.00, 2)
                                take_profit = round(entry - total_tp_distance, 2)
                                
                            if action:
                                slots_available = MAX_CONCURRENT_TRADES - current_open_count
                                burst_count = min(slots_available, 2)
                                
                                print(f"Opening {burst_count} {action} orders with Profit-Lock SL. Active count: {current_open_count}")
                                
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
    status_text = "Active (Profit-Lock Trailing Engine | Max Cap: 250)" if is_bot_running else "Paused"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Profit-Lock 250-Cap Scalper</title>
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
            <h1>Gold Profit-Lock Scalper (250 Cap)</h1>
            <p>Status: <span class="status">{status_text}</span></p>
            <p>Execution: 1s Profit-Lock Worker | Initial SL/TP | Max Cap: 250 Orders</p>
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
        test_sl = round(ask - 12.00, 2)

        tasks = [
            connection.create_market_buy_order(
                symbol="XAUUSDm", volume=0.03, stop_loss=test_sl, take_profit=test_tp
            ) for _ in range(2)
        ]
        results = await asyncio.gather(*tasks)
        return {"status": "success", "batch_count": len(results), "orders": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
