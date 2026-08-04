import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Adaptive Scalper with Ultra-Fast Trailing SL & Max 75 Cap")

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

            print("Adaptive Scalper + Ultra-Fast Trailing SL Manager active (3s Loop | Max 75 Cap)...")

            price_history = []
            MAX_CONCURRENT_TRADES = 75  # Updated max cap to 75 trades

            while is_bot_running:
                # 1. Ultra-Fast Position Management Loop (Checks every 3 seconds inline)
                for _ in range(11): # Run management sub-loop 11 times (3 seconds * 11 = 33s, matching loop rhythm)
                    if not is_bot_running:
                        break
                    try:
                        positions = await connection.get_positions()
                        for pos in positions:
                            pos_id = pos.get("id")
                            profit = pos.get("profit", 0.0)
                            pos_type = pos.get("type") # 0 for BUY, 1 for SELL
                            open_price = pos.get("openPrice")
                            current_sl = pos.get("stopLoss", 0)
                            current_tp = pos.get("takeProfit", 0)
                            
                            lot_size = 0.03
                            
                            # Dynamic Trailing Stop Logic for BUY orders
                            if pos_type == 0: 
                                if profit >= 6.0:
                                    desired_sl = round(open_price + 1.50, 2) # Secure higher profit lock if hitting $6+
                                    if current_sl < desired_sl:
                                        await connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                        print(f"Trailing SL Updated [BUY {pos_id}]: Profit at ${profit:.2f} -> Locked SL to {desired_sl}")
                                elif profit >= 4.0:
                                    desired_sl = round(open_price + 1.00, 2) # Secure profit lock at $4+
                                    if current_sl < desired_sl:
                                        await connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                        print(f"Trailing SL Updated [BUY {pos_id}]: Profit at ${profit:.2f} -> Locked SL to {desired_sl}")
                                elif profit >= 1.25:
                                    desired_sl = round(open_price + 0.35, 2) # Secure breakeven + spread ($1.25+)
                                    if current_sl < desired_sl:
                                        await connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                        print(f"Secured [BUY {pos_id}]: Moved SL to Breakeven/Profit ({desired_sl})")
                                        
                            # Dynamic Trailing Stop Logic for SELL orders
                            elif pos_type == 1: 
                                if profit >= 6.0:
                                    desired_sl = round(open_price - 1.50, 2) # Secure higher profit lock if hitting $6+
                                    if current_sl > desired_sl or current_sl == 0:
                                        await connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                        print(f"Trailing SL Updated [SELL {pos_id}]: Profit at ${profit:.2f} -> Locked SL to {desired_sl}")
                                elif profit >= 4.0:
                                    desired_sl = round(open_price - 1.00, 2) # Secure profit lock at $4+
                                    if current_sl > desired_sl or current_sl == 0:
                                        await connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                        print(f"Trailing SL Updated [SELL {pos_id}]: Profit at ${profit:.2f} -> Locked SL to {desired_sl}")
                                elif profit >= 1.25:
                                    desired_sl = round(open_price - 0.35, 2) # Secure breakeven + spread ($1.25+)
                                    if current_sl > desired_sl or current_sl == 0:
                                        await connection.modify_position(pos_id, stop_loss=desired_sl, take_profit=current_tp)
                                        print(f"Secured [SELL {pos_id}]: Moved SL to Breakeven/Profit ({desired_sl})")
                                        
                    except Exception as pos_err:
                        print(f"Position stop-loss management error: {pos_err}")
                    
                    await asyncio.sleep(3) # Check every 3 seconds!

                # 2. Fetch current price feed for new entries after management block
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
                        
                        current_open_count = len(await connection.get_positions())
                        
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
    status_text = "Active (Ultra-Fast Trailing SL | Max 75 Cap)" if is_bot_running else "Paused"
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
            <p>Execution: 3s Trailing SL Loop | 0.03 Lots | Max Cap: 75 Orders</p>
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
