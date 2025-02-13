import asyncio
from binance import AsyncClient

API_KEY = "bctp79wHBoCazYcyarsKDUuPcZmiGsBlfU6JU8Sd5zUur7i1eaCmDvZemzOIVyOi"
API_SECRET = "sUlASCwTqN41AcveviyJZSgxIGHSiCgSAekf8tFW0gYndZu8UMV6fmttiRtPs7TN"

async def check_balance():
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    account_info = await client.get_account()
    # Print the entire account info or parse out specific balances
    print("Account Info:", account_info)
    await client.close_connection()

if __name__ == "__main__":
    asyncio.run(check_balance())
    