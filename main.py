import asyncio
import os
from metaapi_cloud_sdk import MetaApi

# Replace with your token and the ID from your screenshot
TOKEN = os.getenv("METAAPI_TOKEN", "YOUR_METAAPI_TOKEN")
ACCOUNT_ID = "152756f9-2ced-4f37-b2f7-6d2f56b3..."


async def test_order():
  metaapi = MetaApi(TOKEN)
  account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

  if account.state != "DEPLOYED":
    await account.deploy()

  connection = account.get_rpc_connection()
  await connection.connect()
  await connection.wait_synchronized()

  print("Connected successfully! Placing test market order...")
  result = await connection.create_market_buy_order(
      symbol="EURUSD",
      volume=0.01,
      stop_loss=0,
      take_profit=0,
      comment="Test Bot Order",
  )
  print(f"Order executed successfully: {result}")


if __name__ == "__main__":
  asyncio.run(test_order())
