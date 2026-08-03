import asyncio
import os
from database import init_db
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from metaapi_cloud_sdk import MetaApi

app = FastAPI(title="Gold Scalping System")

# MetaApi Credentials & Configuration
TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = "152756f9-2ced-4f37-b2f7-6d2f56b3..."


@app.on_event("startup")
async def startup_event():
  # Initialize your database on app startup
  init_db()


@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
  """Main monitoring dashboard displaying active and past scalp trades."""
  html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Scalping System</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body { background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; text-align: center; padding-top: 50px; }
            h1 { color: #f39c12; }
            .container { max-width: 800px; margin: auto; background: #1e1e1e; padding: 20px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Gold Scalping System Dashboard</h1>
            <p>System is online, polling market data, and monitoring trades.</p>
            <p><em>Auto-refreshing every 15 seconds...</em></p>
        </div>
    </body>
    </html>
    """
  return html_content


@app.get("/test-order")
async def test_order():
  """Temporary test endpoint to verify MetaApi/Exness trade execution."""
  try:
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != "DEPLOYED":
      await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    # Execute a tiny 0.01 lot test buy on Gold (XAUUSD)
    result = await connection.create_market_buy_order(
        symbol="XAUUSD",
        volume=0.01,
        stop_loss=0,
        take_profit=0,
        comment="Test Scalper Order",
    )
    return {"status": "success", "order": result}
  except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
  import uvicorn

  uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
