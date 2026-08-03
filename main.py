import os
from metaapi_cloud_sdk import MetaApi

# Initialize MetaApi client using environment variables
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

async def execute_metaapi_trade(signal):
    """Bridges the algorithmic signal directly to MT5 via MetaApi cloud WebSocket"""
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        print("[METAFAPI WARNING]: Token or Account ID not found in environment variables.")
        return

    try:
        metaapi = MetaApi(METAAPI_TOKEN)
        account = await metaapi.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
        
        # Ensure account is connected
        if account.state != 'DEPLOYED':
            await account.deploy()
        
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        # Execute market order with MetaApi
        if signal["action"] == "BUY":
            result = await connection.create_market_buy_order(
                symbol="XAUUSD",
                volume=signal["lot_size"],
                stop_loss=signal["stop_loss"],
                take_profit=signal["take_profit"]
            )
        else:
            result = await connection.create_market_sell_order(
                symbol="XAUUSD",
                volume=signal["lot_size"],
                stop_loss=signal["stop_loss"],
                take_profit=signal["take_profit"]
            )
        
        print(f"[METAFAPI SUCCESS]: Order executed. Result: {result}")
    except Exception as e:
        print(f"[METAAPI EXECUTION ERROR]: {e}")
