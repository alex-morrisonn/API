import asyncio
import numpy as np
from datetime import datetime
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
        stop_loss_perc_input=0.2,
        profit_target_perc_input=0.8,
        cooldown_bars=3
    ):
        self.ma_length = ma_length
        self.threshold_perc = threshold_perc
        self.risk_perc = risk_perc_input / 100.0
        self.stop_loss_perc = stop_loss_perc_input / 100.0
        self.profit_target_perc = profit_target_perc_input / 100.0
        self.cooldown_bars = cooldown_bars

        self.symbol = symbol
        self.position = None       # "long" or "short"
        self.position_size = 0.0
        self.position_avg_price = None
        self.current_stop = None

        self.closes = []
        self.trade_log = []

        self.bar_count = 0
        self.last_trade_bar_index = -cooldown_bars

        # Typical step size for BTCUSDT is 0.000001 (6 decimals).
        # Adjust if your symbol has different rules.
        self.step_size_str = "0.001"

    def get_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def calculate_sma(self):
        if len(self.closes) < self.ma_length:
            print(f"{self.get_timestamp()} ⚠️ Not enough data to calculate SMA ({len(self.closes)}/{self.ma_length})")
            return None
        sma_value = np.mean(self.closes[-self.ma_length:])
        print(f"{self.get_timestamp()} 📊 SMA calculated: {sma_value:.2f}")
        return sma_value

    async def get_futures_balance(self, client, asset):
        """
        Query your USDT futures wallet balance via futures_account_balance.
        Returns float balance of the specified asset.
        """
        balances = await client.futures_account_balance()
        for b in balances:
            if b['asset'] == asset:
                return float(b['balance'])
        return 0.0

    def round_step_size(self, quantity: float) -> float:
        """
        Truncate/round the quantity to match the futures lot size (6 decimals for BTCUSDT).
        """
        return float(
            Decimal(str(quantity)).quantize(Decimal(self.step_size_str), rounding=ROUND_DOWN)
        )

    async def on_new_bar(self, bar, client):
        self.bar_count += 1
        close = bar['close']
        self.closes.append(close)

        sma = self.calculate_sma()
        if sma is None:
            return

        deviation_perc = ((close - sma) / sma) * 100
        print(f"{self.get_timestamp()} 📈 Bar {self.bar_count}: Close={close:.2f}, SMA={sma:.2f}, Deviation={deviation_perc:.2f}%")

        if self.position is None:
            # Check if cooldown is over
            if (self.bar_count - self.last_trade_bar_index) >= self.cooldown_bars:
                if deviation_perc < -self.threshold_perc:
                    print(f"{self.get_timestamp()} 🟢 Buy signal detected! Deviation {deviation_perc:.2f}%")
                    await self.enter_position("long", close, deviation_perc, client)
                elif deviation_perc > self.threshold_perc:
                    print(f"{self.get_timestamp()} 🔴 Sell signal detected! Deviation {deviation_perc:.2f}%")
                    await self.enter_position("short", close, deviation_perc, client)
        else:
            await self.manage_position(close, sma, client)

    async def enter_position(self, position_type, entry_price, deviation_perc, client):
        # 1) Check USDT futures balance
        usdt_balance = await self.get_futures_balance(client, 'USDT')
        risk_amount = usdt_balance * self.risk_perc
        stop_distance = entry_price * self.stop_loss_perc

        if stop_distance == 0:
            print(f"{self.get_timestamp()} ⚠️ Stop distance is zero, skipping trade!")
            return

        # 2) Calculate quantity based on risk
        entry_qty = risk_amount / stop_distance
        entry_qty = self.round_step_size(entry_qty)
        if entry_qty <= 0:
            print(f"{self.get_timestamp()} ⚠️ Insufficient balance to enter trade, skipping...")
            return

        # 3) Place a MARKET order
        timestamp = self.get_timestamp()
        if position_type == "long":
            print(f"{timestamp} Placing MARKET BUY order for {entry_qty:.6f} {self.symbol} on Futures")
            order = await client.futures_create_order(
                symbol=self.symbol,
                side=Client.SIDE_BUY,
                type=Client.ORDER_TYPE_MARKET,
                quantity=entry_qty
            )
        else:
            print(f"{timestamp} Placing MARKET SELL order for {entry_qty:.6f} {self.symbol} on Futures")
            order = await client.futures_create_order(
                symbol=self.symbol,
                side=Client.SIDE_SELL,
                type=Client.ORDER_TYPE_MARKET,
                quantity=entry_qty
            )

        print(f"{timestamp} ✅ Order executed: {order}")

        # 4) Update internal position tracking
        self.position = position_type
        self.position_size = entry_qty
        self.position_avg_price = entry_price
        self.current_stop = (
            entry_price * (1 - self.stop_loss_perc)
            if position_type == "long" 
            else entry_price * (1 + self.stop_loss_perc)
        )
        self.trade_log.append({
            'time': timestamp,
            'action': f'enter {position_type}',
            'price': entry_price,
            'qty': entry_qty,
            'bar_index': self.bar_count,
            'deviation_perc': deviation_perc,
            'order': order
        })

    async def manage_position(self, close, sma, client):
        # 1) Calculate profit target
        profit_target = (
            self.position_avg_price * (1 + self.profit_target_perc)
            if self.position == "long"
            else self.position_avg_price * (1 - self.profit_target_perc)
        )

        # 2) Optional example: move stop to break-even after 2x stop distance
        if self.position == "long" and close >= self.position_avg_price * (1 + 2 * self.stop_loss_perc):
            self.current_stop = self.position_avg_price
        elif self.position == "short" and close <= self.position_avg_price * (1 - 2 * self.stop_loss_perc):
            self.current_stop = self.position_avg_price

        # 3) Check exit conditions
        exit_reason = None
        if (self.position == "long" and close >= profit_target) or (self.position == "short" and close <= profit_target):
            exit_reason = "Profit target reached"
        elif (self.position == "long" and close <= self.current_stop) or (self.position == "short" and close >= self.current_stop):
            exit_reason = "Stop loss hit (or break-even triggered)"
        elif (self.position == "long" and close >= sma) or (self.position == "short" and close <= sma):
            exit_reason = "Price reversion to SMA"

        if exit_reason:
            timestamp = self.get_timestamp()
            exit_qty = self.round_step_size(self.position_size)

            # 4) Place a MARKET order to close
            if self.position == "long":
                print(f"{timestamp} Closing LONG → placing MARKET SELL for {exit_qty:.6f} {self.symbol}")
                order = await client.futures_create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_SELL,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=exit_qty
                )
            else:
                print(f"{timestamp} Closing SHORT → placing MARKET BUY for {exit_qty:.6f} {self.symbol}")
                order = await client.futures_create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=exit_qty
                )

            print(f"{timestamp} 🚀 Bar {self.bar_count}: Exiting {self.position.upper()} at {close:.2f} - {exit_reason}")
            self.trade_log.append({
                'time': timestamp,
                'action': f'exit {self.position}',
                'price': close,
                'qty': exit_qty,
                'bar_index': self.bar_count,
                'exit_reason': exit_reason,
                'order': order
            })

            # 5) Show updated USDT balance
            usdt_balance = await self.get_futures_balance(client, 'USDT')
            print(f"{timestamp} Updated USDT Futures Balance: {usdt_balance:.2f}")

            # 6) Reset internal state
            self.reset_position()

    def reset_position(self):
        self.position = None
        self.position_size = 0.0
        self.position_avg_price = None
        self.current_stop = None
        self.last_trade_bar_index = self.bar_count

# --------------------------------------------------------------------------------
# UPDATED kline_listener + process_message using futures_multiplex_socket
# --------------------------------------------------------------------------------
async def kline_listener(strategy):
    # 1) Create the futures client
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    # Point to the Futures Testnet
    client.API_URL = 'https://testnet.binancefuture.com'
    
    bm = BinanceSocketManager(client)
    print(f"{strategy.get_timestamp()} ✅ Binance Futures WebSocket connected via multiplex socket!")

    # 2) Build the channel name for 1-minute klines on your symbol
    #    e.g. 'btcusdt@kline_1m'
    channel = f"{strategy.symbol.lower()}@kline_5m"

    # 3) Subscribe to the multiplex socket with the single channel
    async with bm.futures_multiplex_socket([channel]) as stream:
        while True:
            msg = await stream.recv()
            await process_message(msg, strategy, client)

    await client.close_connection()

async def process_message(msg, strategy, client):
    try:
        # The kline data is under msg['data']['k']
        kline = msg['data']['k']
        # Check if the candle just closed
        if kline['x']:
            bar = {'close': float(kline['c'])}
            await strategy.on_new_bar(bar, client)
    except Exception as e:
        print(f"{strategy.get_timestamp()} ❌ Error processing message: {e}")

# --------------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------------
async def main():
    strategy = HFTMeanReversionStrategy(
        symbol='BTCUSDT',
        ma_length=20,
        threshold_perc=0.2,
        risk_perc_input=1.0,
        stop_loss_perc_input=0.2,
        profit_target_perc_input=0.8,
        cooldown_bars=3
    )

    # Optional: verify connectivity with the futures testnet
    temp_client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    temp_client.API_URL = 'https://testnet.binancefuture.com'

    try:
        account_info = await temp_client.futures_account()
        print("✅ Binance Futures Testnet Account Verified! API is working correctly.")
    except Exception as e:
        print(f"❌ Error verifying Futures Testnet connection: {e}")

    await temp_client.close_connection()

    # Run the main kline listener (multiplex version)
    await kline_listener(strategy)

if __name__ == "__main__":
    asyncio.run(main())
