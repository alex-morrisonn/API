import asyncio
import numpy as np
from datetime import datetime, timedelta
from binance import AsyncClient, BinanceSocketManager
from binance.client import Client
from decimal import Decimal, ROUND_DOWN

API_KEY = "affa119c5030ae4d6020b5e6fcb6aaec0185e70196da9b5c3dfcb5263bba6c30"
API_SECRET = "1751534fcc7b87b6378566b848df117ed94daf406364054db55acd0272643fc2"

class HFTMeanReversionStrategy:
    def __init__(
        self,
        symbol='BTCUSDT',
        ma_length=20,
        threshold_perc=0.2,
        risk_perc_input=1.0,
        stop_loss_perc_input=0.2  # Defines the risk distance percentage (e.g., 0.2% risk)
    ):
        self.symbol = symbol
        self.ma_length = ma_length
        self.threshold_perc = threshold_perc
        self.risk_perc = risk_perc_input / 100.0
        self.stop_loss_perc = stop_loss_perc_input / 100.0  # Risk distance % per trade

        self.position = None  # Only one open position at a time
        self.position_size = 0.0
        self.position_avg_price = None  # Effective entry (mid-price)
        self.effective_stop = None  # Stop loss level (ignoring spread)
        self.effective_profit = None  # Profit target (ignoring spread)
        self.closes = []  # Stores mid-prices for SMA calculation
        self.trade_log = []
        self.step_size_str = "0.001"

        self.current_sma = None
        self.last_stop_loss_check = None
        self.stop_loss_check_interval = 300  # Check stop loss every 5 minutes

    def get_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def calculate_sma(self):
        """Calculate SMA based on the last `ma_length` mid prices."""
        if len(self.closes) < self.ma_length:
            return None
        return np.mean(self.closes[-self.ma_length:])

    async def update_sma_periodically(self):
        """Update the SMA every 5 minutes."""
        while True:
            sma = self.calculate_sma()
            if sma is not None:
                self.current_sma = sma
                print(f"{self.get_timestamp()} - Updated SMA: {self.current_sma:.2f}")
            else:
                print(f"{self.get_timestamp()} - Not enough data to update SMA.")
            await asyncio.sleep(300)  # 5 minutes

    async def get_futures_balance(self, client, asset):
        """Return the futures balance for a specific asset."""
        balances = await client.futures_account_balance()
        for b in balances:
            if b['asset'] == asset:
                return float(b['balance'])
        return 0.0

    def round_step_size(self, quantity: float) -> float:
        """Round the quantity to match the step size."""
        return float(Decimal(str(quantity)).quantize(Decimal(self.step_size_str), rounding=ROUND_DOWN))

    async def on_price_update(self, msg, client):
        """
        Process each tick update.
        Uses `@bookTicker` to get:
          - `b`: best bid (sell price for closing long or entering short)
          - `a`: best ask (buy price for entering long or closing short)
          - `mid_price`: (best_bid + best_ask)/2 used for indicator and risk-reward.
        """
        data = msg["data"]
        if "b" not in data or "a" not in data:
            return  # Ignore if data is incomplete

        best_bid = float(data["b"])
        best_ask = float(data["a"])
        mid_price = (best_bid + best_ask) / 2  # Fair mid-price for calculations

        # Store mid price for SMA calculation
        self.closes.append(mid_price)

        if self.current_sma is None:
            return  # Wait until SMA is updated

        # Compute deviation from SMA using mid price
        deviation_perc = ((mid_price - self.current_sma) / self.current_sma) * 100

        # Entry logic: only one open position at a time
        if self.position is None:
            if deviation_perc < -self.threshold_perc:
                await self.enter_position("long", best_ask, mid_price, client)
            elif deviation_perc > self.threshold_perc:
                await self.enter_position("short", best_bid, mid_price, client)
        else:
            # Manage open position
            await self.manage_position(best_bid, best_ask, client)

    async def enter_position(self, position_type, exec_price, effective_entry, client):
        """
        Enter a new position.
          - LONG: executed at best ask, effective entry = mid-price.
          - SHORT: executed at best bid, effective entry = mid-price.
        Risk-reward is computed using effective_entry.
        """
        usdt_balance = await self.get_futures_balance(client, 'USDT')
        risk_amount = usdt_balance * self.risk_perc
        risk_distance = effective_entry * self.stop_loss_perc
        entry_qty = risk_amount / risk_distance
        entry_qty = self.round_step_size(entry_qty)

        if entry_qty <= 0:
            return

        if position_type == "long":
            order = await client.futures_create_order(
                symbol=self.symbol, side=Client.SIDE_BUY, type=Client.ORDER_TYPE_MARKET, quantity=entry_qty
            )
            self.effective_stop = effective_entry - risk_distance
            self.effective_profit = effective_entry + 4 * risk_distance
        else:
            order = await client.futures_create_order(
                symbol=self.symbol, side=Client.SIDE_SELL, type=Client.ORDER_TYPE_MARKET, quantity=entry_qty
            )
            self.effective_stop = effective_entry + risk_distance
            self.effective_profit = effective_entry - 4 * risk_distance

        self.position = position_type
        self.position_size = entry_qty
        self.position_avg_price = effective_entry

        print(f"{self.get_timestamp()} - Entered {position_type.upper()} at execution price {exec_price:.2f} (Effective entry: {effective_entry:.2f})")
        print(f"  - Stop loss set at {self.effective_stop:.2f}, Profit target at {self.effective_profit:.2f}")

    async def manage_position(self, best_bid, best_ask, client):
        """Manage the open position."""
        now = datetime.now()
        effective_exit_price = best_bid if self.position == "long" else best_ask

        if (self.position == "long" and effective_exit_price >= self.effective_profit) or \
           (self.position == "short" and effective_exit_price <= self.effective_profit):
            await self.exit_position(effective_exit_price, "Profit target reached", client)
            return

        if self.last_stop_loss_check is None or (now - self.last_stop_loss_check).total_seconds() >= self.stop_loss_check_interval:
            if (self.position == "long" and effective_exit_price <= self.effective_stop) or \
               (self.position == "short" and effective_exit_price >= self.effective_stop):
                await self.exit_position(effective_exit_price, "Stop loss hit", client)
            self.last_stop_loss_check = now

    async def exit_position(self, price, reason, client):
        """Exit the position at market price."""
        exit_qty = self.round_step_size(self.position_size)
        side = Client.SIDE_SELL if self.position == "long" else Client.SIDE_BUY

        await client.futures_create_order(symbol=self.symbol, side=side, type=Client.ORDER_TYPE_MARKET, quantity=exit_qty)
        print(f"{self.get_timestamp()} - Exited {self.position.upper()} at {price:.2f} - {reason}")

        self.position = None

async def ticker_listener(strategy):
    """
    Connects to Binance Futures WebSocket and listens for book ticker updates.
    The book ticker message includes best bid ("b") and best ask ("a").
    """
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    bm = BinanceSocketManager(client)

    print(f"{strategy.get_timestamp()} - ✅ Binance Book Ticker WebSocket Connected!")

    async with bm.futures_multiplex_socket([f"{strategy.symbol.lower()}@bookTicker"]) as stream:
        while True:
            msg = await stream.recv()
            await strategy.on_price_update(msg, client)

async def main():
    strategy = HFTMeanReversionStrategy()
    await asyncio.gather(strategy.update_sma_periodically(), ticker_listener(strategy))

if __name__ == "__main__":
    asyncio.run(main())
