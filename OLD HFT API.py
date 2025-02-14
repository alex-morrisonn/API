import asyncio
import time
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
        stop_loss_perc_input=0.2  # Risk distance as a percentage (e.g., 0.2% risk)
    ):
        # Trading parameters
        self.symbol = symbol
        self.ma_length = ma_length
        self.threshold_perc = threshold_perc
        self.risk_perc = risk_perc_input / 100.0
        self.stop_loss_perc = stop_loss_perc_input / 100.0  # Converted to decimal

        # Position tracking
        self.position = None             # Current position type ("long" or "short")
        self.position_size = 0.0         # Size of the open position
        self.position_avg_price = None   # Average entry price (using mid-price)
        self.effective_stop = None       # Calculated stop loss price
        self.effective_profit = None     # Calculated profit target price

        # Data for indicators and logging
        self.closes = []                 # List of recent mid-prices for SMA calculation
        self.trade_log = []              # Log of executed trades
        self.step_size_str = "0.001"     # Step size for rounding order quantities

        # SMA and stop loss timing
        self.current_sma = None          # Current Simple Moving Average
        self.last_stop_loss_check = None # Timestamp of the last stop loss check
        self.stop_loss_check_interval = 300  # Check stop loss every 5 minutes

        # Track consecutive stop-loss hits for each direction
        self.stop_loss_count = {"long": 0, "short": 0}

    def get_timestamp(self):
        """Return the current time as a formatted string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def calculate_sma(self):
        """
        Calculate the Simple Moving Average (SMA) using the last `ma_length` mid-price values.
        Returns None if there isn't enough data.
        """
        if len(self.closes) < self.ma_length:
            return None
        return np.mean(self.closes[-self.ma_length:])

    async def update_sma_periodically(self):
        """
        Update the SMA every 5 minutes.
        Logs the updated SMA or indicates if there's insufficient data.
        """
        while True:
            sma = self.calculate_sma()
            if sma is not None:
                self.current_sma = sma
                print(f"{self.get_timestamp()} - Updated SMA: {self.current_sma:.2f}")
            else:
                print(f"{self.get_timestamp()} - Not enough data to update SMA.")
            await asyncio.sleep(300)  # Wait 5 minutes

    async def get_futures_balance(self, client, asset):
        """
        Fetch the futures account balance for the specified asset.
        Returns the balance as a float, or 0.0 if there's an error.
        """
        try:
            balances = await client.futures_account_balance()
            for b in balances:
                if b['asset'] == asset:
                    return float(b['balance'])
        except Exception as e:
            print(f"{self.get_timestamp()} - Error fetching balance: {e}")
        return 0.0

    def round_step_size(self, quantity: float) -> float:
        """
        Round the given quantity to conform to the exchange's step size requirements.
        """
        return float(Decimal(str(quantity)).quantize(Decimal(self.step_size_str), rounding=ROUND_DOWN))

    async def create_order_with_retry(self, client, max_retries=3, **order_params):
        """
        Place an order using retry logic.
        Retries up to `max_retries` times if an exception is encountered.
        Returns the order response, or None if all attempts fail.
        """
        for attempt in range(max_retries):
            try:
                order = await client.futures_create_order(**order_params)
                return order
            except Exception as e:
                print(f"{self.get_timestamp()} - Order attempt {attempt+1} failed: {e}")
                await asyncio.sleep(1)
        print(f"{self.get_timestamp()} - Order failed after {max_retries} attempts: {order_params}")
        return None

    async def wait_until_order_filled(self, client, order, max_wait=5, poll_interval=0.5):
        """
        Poll the order status until it is filled or until a maximum wait time elapses.
        Returns the most recent order status, which may indicate a partial fill.
        """
        start_time = time.monotonic()
        order_id = order.get("orderId")
        while time.monotonic() - start_time < max_wait:
            try:
                current_order = await client.futures_get_order(symbol=self.symbol, orderId=order_id)
                if current_order.get("status") == "FILLED":
                    return current_order
            except Exception as e:
                print(f"{self.get_timestamp()} - Error checking order status: {e}")
            await asyncio.sleep(poll_interval)
        # Return last known order status (could be a partial fill)
        return order

    async def on_price_update(self, msg, client):
        """
        Process each price update received from the Binance WebSocket.
        - Computes the mid-price from best bid and ask.
        - Updates the price list for SMA calculation.
        - Determines whether to open a new position or manage an existing one.
        """
        data = msg["data"]
        if "b" not in data or "a" not in data:
            return  # Skip messages with incomplete data

        best_bid = float(data["b"])
        best_ask = float(data["a"])
        mid_price = (best_bid + best_ask) / 2  # Compute the mid-price

        # Save the mid-price for SMA computation
        self.closes.append(mid_price)

        # Only proceed if the SMA has been computed
        if self.current_sma is None:
            return

        # Determine percentage deviation from the SMA
        deviation_perc = ((mid_price - self.current_sma) / self.current_sma) * 100

        # Entry logic: if no position is open, check for a valid entry signal.
        if self.position is None:
            if deviation_perc < -self.threshold_perc:
                # Signal for a LONG position.
                if self.stop_loss_count.get("long", 0) >= 2:
                    print(f"{self.get_timestamp()} - Skipping LONG entry due to 2 consecutive stop-loss hits.")
                else:
                    # Reversal signal resets the opposing (short) counter.
                    if self.stop_loss_count.get("short", 0) > 0:
                        self.stop_loss_count["short"] = 0
                    await self.enter_position("long", best_ask, mid_price, client)
            elif deviation_perc > self.threshold_perc:
                # Signal for a SHORT position.
                if self.stop_loss_count.get("short", 0) >= 2:
                    print(f"{self.get_timestamp()} - Skipping SHORT entry due to 2 consecutive stop-loss hits.")
                else:
                    # Reversal signal resets the opposing (long) counter.
                    if self.stop_loss_count.get("long", 0) > 0:
                        self.stop_loss_count["long"] = 0
                    await self.enter_position("short", best_bid, mid_price, client)
        else:
            # Manage the open position.
            await self.manage_position(best_bid, best_ask, client)

    async def enter_position(self, position_type, exec_price, effective_entry, client):
        """
        Open a new position ("long" or "short") based on market conditions.
        - For a LONG, place a market order at the best ask.
        - For a SHORT, place a market order at the best bid.
        The effective entry (mid-price) is used to compute stop loss and profit target.
        """
        usdt_balance = await self.get_futures_balance(client, 'USDT')
        risk_amount = usdt_balance * self.risk_perc
        risk_distance = effective_entry * self.stop_loss_perc
        requested_qty = risk_amount / risk_distance
        requested_qty = self.round_step_size(requested_qty)

        if requested_qty <= 0:
            return

        if position_type == "long":
            order = await self.create_order_with_retry(
                client,
                symbol=self.symbol,
                side=Client.SIDE_BUY,
                type=Client.ORDER_TYPE_MARKET,
                quantity=requested_qty
            )
            if order is None:
                print(f"{self.get_timestamp()} - Failed to place LONG order. Aborting entry.")
                return
            # Set stop loss and profit target for LONG
            self.effective_stop = effective_entry - risk_distance
            self.effective_profit = effective_entry + 4 * risk_distance
        else:
            order = await self.create_order_with_retry(
                client,
                symbol=self.symbol,
                side=Client.SIDE_SELL,
                type=Client.ORDER_TYPE_MARKET,
                quantity=requested_qty
            )
            if order is None:
                print(f"{self.get_timestamp()} - Failed to place SHORT order. Aborting entry.")
                return
            # Set stop loss and profit target for SHORT
            self.effective_stop = effective_entry + risk_distance
            self.effective_profit = effective_entry - 4 * risk_distance

        # Wait for order fill (handling partial fills)
        filled_order = await self.wait_until_order_filled(client, order)
        executed_qty = float(filled_order.get("executedQty", requested_qty))
        if executed_qty < requested_qty:
            print(f"{self.get_timestamp()} - Partial fill on {position_type.upper()} entry: requested {requested_qty}, executed {executed_qty}")
        else:
            print(f"{self.get_timestamp()} - Full fill on {position_type.upper()} entry: executed {executed_qty}")
        self.position = position_type
        self.position_size = executed_qty
        self.position_avg_price = effective_entry

        print(f"{self.get_timestamp()} - Entered {position_type.upper()} at exec price {exec_price:.2f} (Effective entry: {effective_entry:.2f})")
        print(f"  - Stop loss: {self.effective_stop:.2f}, Profit target: {self.effective_profit:.2f}")

    async def manage_position(self, best_bid, best_ask, client):
        """
        Monitor the open position and exit if profit target or stop loss levels are reached.
        Checks are performed at defined intervals.
        """
        now = datetime.now()
        effective_exit_price = best_bid if self.position == "long" else best_ask

        # Exit if profit target is reached.
        if (self.position == "long" and effective_exit_price >= self.effective_profit) or \
           (self.position == "short" and effective_exit_price <= self.effective_profit):
            await self.exit_position(effective_exit_price, "Profit target reached", client)
            return

        # Check stop loss condition at the specified interval.
        if self.last_stop_loss_check is None or (now - self.last_stop_loss_check).total_seconds() >= self.stop_loss_check_interval:
            if (self.position == "long" and effective_exit_price <= self.effective_stop) or \
               (self.position == "short" and effective_exit_price >= self.effective_stop):
                await self.exit_position(effective_exit_price, "Stop loss hit", client)
            self.last_stop_loss_check = now

    async def exit_position(self, price, reason, client):
        """
        Close the active position using market orders.
        Continues issuing exit orders until the entire position is closed,
        thereby handling partial fills.
        Updates the stop-loss counter based on the exit reason.
        """
        remaining_qty = self.round_step_size(self.position_size)
        side = Client.SIDE_SELL if self.position == "long" else Client.SIDE_BUY

        print(f"{self.get_timestamp()} - Initiating exit of {self.position.upper()} position for {remaining_qty} units due to: {reason}")
        
        # Store the position direction before exiting
        direction = self.position

        # Loop until the full position is liquidated.
        while remaining_qty > 0:
            order = await self.create_order_with_retry(
                client,
                symbol=self.symbol,
                side=side,
                type=Client.ORDER_TYPE_MARKET,
                quantity=remaining_qty
            )
            if order is None:
                print(f"{self.get_timestamp()} - Failed to exit {direction.upper()} position for {remaining_qty} units")
                return

            filled_order = await self.wait_until_order_filled(client, order)
            executed_qty = float(filled_order.get("executedQty", remaining_qty))
            if executed_qty < remaining_qty:
                print(f"{self.get_timestamp()} - Partial exit fill: requested {remaining_qty}, executed {executed_qty}")
            else:
                print(f"{self.get_timestamp()} - Full exit fill: executed {executed_qty}")
            remaining_qty -= executed_qty
            remaining_qty = self.round_step_size(remaining_qty)
            if remaining_qty < 1e-8:
                remaining_qty = 0

        # Update the stop-loss counter based on exit reason.
        if reason == "Stop loss hit":
            self.stop_loss_count[direction] = self.stop_loss_count.get(direction, 0) + 1
            print(f"{self.get_timestamp()} - {direction.upper()} stop loss count is now {self.stop_loss_count[direction]}")
        elif reason == "Profit target reached":
            self.stop_loss_count[direction] = 0

        print(f"{self.get_timestamp()} - Exited {direction.upper()} position at {price:.2f} - {reason}")
        self.position = None
        self.position_size = 0.0

async def ticker_listener(strategy):
    """
    Connect to Binance Futures WebSocket to receive book ticker updates.
    If the connection is lost, the function attempts to reconnect after a delay.
    """
    while True:
        client = None
        try:
            client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
            bm = BinanceSocketManager(client)
            print(f"{strategy.get_timestamp()} - Connected to Binance Book Ticker WebSocket!")
            # Connect to the multiplex socket for the specified symbol.
            async with bm.futures_multiplex_socket([f"{strategy.symbol.lower()}@bookTicker"]) as stream:
                while True:
                    msg = await stream.recv()
                    await strategy.on_price_update(msg, client)
        except Exception as e:
            print(f"{strategy.get_timestamp()} - WebSocket connection error: {e}. Reconnecting in 5 seconds.")
            await asyncio.sleep(5)
        finally:
            if client:
                try:
                    await client.close_connection()
                except Exception as e:
                    print(f"{strategy.get_timestamp()} - Error closing client connection: {e}")

async def main():
    strategy = HFTMeanReversionStrategy()
    await asyncio.gather(
        strategy.update_sma_periodically(),
        ticker_listener(strategy)
    )

if __name__ == "__main__":
    asyncio.run(main())
