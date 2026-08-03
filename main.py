import asyncio
from fastapi import FastAPI
from metaapi_cloud_sdk import MetaApi

app = FastAPI()

TOKEN = "YOUR_METAAPI_TOKEN"
ACCOUNT_ID = "152756f9-2ced-4f37-b2f7-6d2f56b3..."


@app.get("/test-order")
async def test_order():
  metaapi = MetaApi(TOKEN)
  account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

  if account.state != "DEPLOYED":
    await account.deploy()

  connection = account.get_rpc_connection()
  await connection.connect()
  await connection.wait_synchronized()

  result = await connection.create_market_buy_order(
      symbol="EURUSD",
      volume=0.01,
      stop_loss=0,
      take_profit=0,
      comment="FastAPI Test Order",
  )
  return {"status": "success", "order": result}
