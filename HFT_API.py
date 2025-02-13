import asyncio
import numpy as np
from datetime import datetime
from binance import AsyncClient, BinanceSocketManager
from binance.client import Client
from decimal import Decimal, ROUND_DOWN

# API credentials for Binance Futures (Testnet)
API_KEY = "affa119c5030ae4d6020b5e6fcb6aaec0185e70196da9b5c3dfcb5263bba6c30"
API_SECRET = "1751534fcc7b87b6378566b848df117ed94daf406364054db55acd0272643fc2"

class HFTMeanReversionStrategy:
    """
    A High-Frequency Trading (HFT) Mean Reversion Strategy for Binance Futures.
    
    The strategy calculates a simple moving average (SMA) over the last 'ma_length' bars.
    It then compares the current price's deviation from the SMA and, if the deviation
    exceeds a specified threshold, enters a long or short position.
    
    Parameters:
        symbol (str): Trading symbol (default 'BTCUSDT').
        ma_length (int): Number of bars to calculate the SMA.
        threshold_perc (float): Percentage deviation from the SMA required to trigger a trade.
        risk_perc_input (float): Percentage of available balance to risk on each trade.
        stop_loss_perc_input (float): Percentage for stop loss distance from entry price.
        profit_target_perc_input (float): Percentage for profit target distance from entry price.
        cooldown_bars (int): Number of bars to wait after a trade before new signals.
    """
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
        # Strategy parameters
        self.ma_length = ma_length
        self.threshold_perc = threshold_perc
        self.risk_perc = risk_perc_input / 100.0          # Convert risk percentage to decimal
        self.stop_loss_perc = stop_loss_perc_input / 100.0  # Convert stop loss percentage to decimal
        self.profit_target_perc = profit_target_perc_input / 100.0  # Convert profit target to decimal
        self.cooldown_bars = cooldown_bars

        self.symbol = symbol
        self.position = None           # Current position: None, "long", or "short"
        self.position_size = 0.0       # Size/quantity of the open position
        self.position_avg_price = None # Average entry price for the position
        self.current_stop = None       # Current stop-loss price (can be adjusted to break-even)

        self.closes = []   # List to store closing prices for SMA calculation
        self.trade_log = []  # Log of all executed trades with details

        self.bar_count = 0   # Counter for processed bars/candles
        self.last_trade_bar_index = -cooldown_bars  # Bar index when the last trade was executed

        # Step size for rounding order quantities.
        # For BTCUSDT futures, typically the step size is 0.001.
        self.step_size_str = "0.001"

    def get_timestamp(self):
        """
        Get the current timestamp formatted as a string.
        Returns:
            str: Timestamp in "YYYY-MM-DD HH:MM:SS" format.
        """
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def calculate_sma(self):
        """
        Calculate the Simple Moving Average (SMA) of the last `ma_length` closing prices.
        
        Returns:
            float or None: The SMA value if enough data is available; otherwise, None.
        """
        if len(self.closes) < self.ma_length:
            print(f"{self.get_timestamp()} ⚠️ Not enough data to calculate SMA ({len(self.closes)}/{self.ma_length})")
            return None
        sma_value = np.mean(self.closes[-self.ma_length:])
        print(f"{self.get_timestamp()} 📊 SMA calculated: {sma_value:.2f}")
        return sma_value

    async def get_futures_balance(self, client, asset):
        """
        Retrieve the balance for a specific asset from the Binance Futures account.
        
        Args:
            client (AsyncClient): The Binance API client.
            asset (str): The asset symbol (e.g., 'USDT').
        
        Returns:
            float: The available balance for the specified asset.
        """
        balances = await client.futures_account_balance()
        for b in balances:
            if b['asset'] == asset:
                return float(b['balance'])
        return 0.0

    def round_step_size(self, quantity: float) -> float:
        """
        Round or truncate the order quantity to adhere to the symbol's step size.
        
        Args:
            quantity (float): The raw quantity calculated.
            
        Returns:
            float: The quantity rounded down to the nearest allowed step.
        """
        return float(
            Decimal(str(quantity)).quantize(Decimal(self.step_size_str), rounding=ROUND_DOWN)
        )

    async def on_new_bar(self, bar, client):
        """
        Process a new bar/candle. Update closing prices, calculate SMA, 
        generate trade signals, and manage open positions.
        
        Args:
            bar (dict): A dictionary with bar data (must include 'close' key).
            client (AsyncClient): The Binance API client.
        """
        self.bar_count += 1
        close = bar['close']
        self.closes.append(close)

        sma = self.calculate_sma()
        if sma is None:
            return  # Not enough data to generate signals

        # Calculate the percentage deviation from the SMA
        deviation_perc = ((close - sma) / sma) * 100
        print(f"{self.get_timestamp()} 📈 Bar {self.bar_count}: Close={close:.2f}, SMA={sma:.2f}, Deviation={deviation_perc:.2f}%")

        if self.position is None:
            # Only consider entering a new position if cooldown period is over
            if (self.bar_count - self.last_trade_bar_index) >= self.cooldown_bars:
                if deviation_perc < -self.threshold_perc:
                    print(f"{self.get_timestamp()} 🟢 Buy signal detected! Deviation {deviation_perc:.2f}%")
                    await self.enter_position("long", close, deviation_perc, client)
                elif deviation_perc > self.threshold_perc:
                    print(f"{self.get_timestamp()} 🔴 Sell signal detected! Deviation {deviation_perc:.2f}%")
                    await self.enter_position("short", close, deviation_perc, client)
        else:
            # Manage the current open position based on new bar information
            await self.manage_position(close, sma, client)

    async def enter_position(self, position_type, entry_price, deviation_perc, client):
        """
        Enter a new market position based on the provided signal.
        
        Steps:
            1) Check available USDT futures balance.
            2) Calculate the quantity to trade based on the risk amount and stop loss distance.
            3) Place a MARKET order to enter the position.
            4) Update internal state and log the trade.
        
        Args:
            position_type (str): "long" or "short".
            entry_price (float): The price at which the trade is executed.
            deviation_perc (float): The percentage deviation that triggered the trade.
            client (AsyncClient): The Binance API client.
        """
        # 1) Retrieve USDT futures balance and compute the risk amount.
        usdt_balance = await self.get_futures_balance(client, 'USDT')
        risk_amount = usdt_balance * self.risk_perc
        
        # Calculate the stop distance based on the entry price.
        stop_distance = entry_price * self.stop_loss_perc
        if stop_distance == 0:
            print(f"{self.get_timestamp()} ⚠️ Stop distance is zero, skipping trade!")
            return

        # 2) Determine the quantity based on risk management.
        entry_qty = risk_amount / stop_distance
        entry_qty = self.round_step_size(entry_qty)
        if entry_qty <= 0:
            print(f"{self.get_timestamp()} ⚠️ Insufficient balance to enter trade, skipping...")
            return

        # 3) Place a MARKET order to enter the position.
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

        # 4) Update the internal state to track the new position.
        self.position = position_type
        self.position_size = entry_qty
        self.position_avg_price = entry_price
        # Set the initial stop loss level based on the position direction.
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
        """
        Manage an open position by checking for exit signals such as:
            - Profit target reached.
            - Stop loss or break-even triggered.
            - Price reverting back to the SMA.
        
        Additional logic:
            - Move stop loss to break-even after the price has moved favorably by 2x the stop distance.
        
        Args:
            close (float): The current closing price.
            sma (float): The current simple moving average.
            client (AsyncClient): The Binance API client.
        """
        # 1) Calculate the profit target price based on the initial entry price.
        profit_target = (
            self.position_avg_price * (1 + self.profit_target_perc)
            if self.position == "long"
            else self.position_avg_price * (1 - self.profit_target_perc)
        )

        # 2) Adjust stop loss to break-even if the price has moved sufficiently in favor.
        if self.position == "long" and close >= self.position_avg_price * (1 + 2 * self.stop_loss_perc):
            self.current_stop = self.position_avg_price
        elif self.position == "short" and close <= self.position_avg_price * (1 - 2 * self.stop_loss_perc):
            self.current_stop = self.position_avg_price

        # 3) Determine if any exit conditions are met.
        exit_reason = None
        if (self.position == "long" and close >= profit_target) or (self.position == "short" and close <= profit_target):
            exit_reason = "Profit target reached"
        elif (self.position == "long" and close <= self.current_stop) or (self.position == "short" and close >= self.current_stop):
            exit_reason = "Stop loss hit (or break-even triggered)"
        elif (self.position == "long" and close >= sma) or (self.position == "short" and close <= sma):
            exit_reason = "Price reversion to SMA"

        # If an exit condition is met, proceed to close the position.
        if exit_reason:
            timestamp = self.get_timestamp()
            exit_qty = self.round_step_size(self.position_size)

            # 4) Place a MARKET order to close the position.
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

            # 5) Optionally, display the updated USDT futures balance.
            usdt_balance = await self.get_futures_balance(client, 'USDT')
            print(f"{timestamp} Updated USDT Futures Balance: {usdt_balance:.2f}")

            # 6) Reset the internal state to indicate that there is no open position.
            self.reset_position()

    def reset_position(self):
        """
        Reset the tracking variables after closing a position.
        This allows the strategy to start fresh for the next trade.
        """
        self.position = None
        self.position_size = 0.0
        self.position_avg_price = None
        self.current_stop = None
        # Record the bar index of the last trade to enforce the cooldown period.
        self.last_trade_bar_index = self.bar_count

# --------------------------------------------------------------------------------
# WebSocket Listener and Message Processor for Binance Futures Klines
# --------------------------------------------------------------------------------
async def kline_listener(strategy):
    """
    Listen to Binance Futures kline (candlestick) data via a multiplex socket.
    
    Steps:
        1) Create an AsyncClient connection to Binance Futures (Testnet).
        2) Connect to the multiplex WebSocket channel for the specified symbol and interval.
        3) Process each incoming message (bar data) through the process_message function.
    
    Args:
        strategy (HFTMeanReversionStrategy): An instance of the trading strategy.
    """
    # 1) Initialize Binance Futures client (Testnet)
    client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    # Set the API endpoint to Binance Futures Testnet
    client.API_URL = 'https://testnet.binancefuture.com'
    
    bm = BinanceSocketManager(client)
    print(f"{strategy.get_timestamp()} ✅ Binance Futures WebSocket connected via multiplex socket!")

    # 2) Define the channel name for kline data (e.g., 'btcusdt@kline_5m')
    channel = f"{strategy.symbol.lower()}@kline_5m"

    # 3) Subscribe to the multiplex socket for the defined channel.
    async with bm.futures_multiplex_socket([channel]) as stream:
        while True:
            msg = await stream.recv()
            await process_message(msg, strategy, client)

    # Close the connection when done (this line may not be reached in a continuous loop).
    await client.close_connection()

async def process_message(msg, strategy, client):
    """
    Process incoming WebSocket messages.
    
    This function extracts kline data from the message and checks if the current candle
    has closed. If so, it creates a simplified bar dictionary and passes it to the strategy.
    
    Args:
        msg (dict): The message received from the WebSocket.
        strategy (HFTMeanReversionStrategy): The trading strategy instance.
        client (AsyncClient): The Binance API client.
    """
    try:
        # Extract kline (candlestick) data from the message payload.
        kline = msg['data']['k']
        # Check if the kline has closed (finalized candle).
        if kline['x']:
            bar = {'close': float(kline['c'])}
            await strategy.on_new_bar(bar, client)
    except Exception as e:
        print(f"{strategy.get_timestamp()} ❌ Error processing message: {e}")

# --------------------------------------------------------------------------------
# Main Entry Point
# --------------------------------------------------------------------------------
async def main():
    """
    Main function to initialize the trading strategy and verify Binance Futures connectivity.
    
    Steps:
        1) Instantiate the HFTMeanReversionStrategy with desired parameters.
        2) Verify the Binance Futures Testnet account connection.
        3) Start listening to kline data to drive the strategy.
    """
    strategy = HFTMeanReversionStrategy(
        symbol='BTCUSDT',
        ma_length=20,
        threshold_perc=0.2,
        risk_perc_input=1.0,
        stop_loss_perc_input=0.2,
        profit_target_perc_input=0.8,
        cooldown_bars=3
    )

    # Optional: Verify connectivity with Binance Futures Testnet.
    temp_client = await AsyncClient.create(API_KEY, API_SECRET, testnet=True)
    temp_client.API_URL = 'https://testnet.binancefuture.com'

    try:
        account_info = await temp_client.futures_account()
        print("✅ Binance Futures Testnet Account Verified! API is working correctly.")
    except Exception as e:
        print(f"❌ Error verifying Futures Testnet connection: {e}")

    await temp_client.close_connection()

    # Start the kline listener to process market data and execute strategy logic.
    await kline_listener(strategy)

if __name__ == "__main__":
    asyncio.run(main())
