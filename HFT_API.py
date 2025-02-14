import time
import datetime
import krakenex
import pandas as pd

# -----------------------------
# Set your Kraken API credentials
# -----------------------------
API_KEY = "HREbea3geJMTRocFfofvJdPCvEzUzDf5a5E6mCSl3jd17/3KvdSo7yqF"
API_SECRET = "aKeMUFmBWssj+wBoU6Gmgsn0WcATTta9GslSCF9DpwY0vA20IgaRxCP4eHDcyQ4ZrRfI4Ml9M5YuFbSBFG0ooA=="

# Initialize Kraken API client
kraken = krakenex.API(API_KEY, API_SECRET)

# -----------------------------
# Strategy Parameters
# -----------------------------
MA_LENGTH = 20
THRESHOLD_PERC = 0.2 / 100      # Deviation threshold
RISK_PERC = 1.0 / 100
STOP_LOSS_PERC = 0.2 / 100
PROFIT_TARGET_PERC = 0.8 / 100
COOLDOWN_BARS = 3               # Number of candles to wait after a trade
CANDLE_INTERVAL = 5             # Candle interval in minutes

# -----------------------------
# Trade Tracking Variables
# -----------------------------
last_trade_bar = None           # Unix timestamp of the last trade candle
long_break_even_triggered = False
position_size = 0               # Current BTC position (in BTC)
entry_price = None              # Effective entry price for current trade

# For SMA-update messaging
last_sma_time = None            # The last candle timestamp for which an SMA update was printed
sma_initialized = False         # Flag for initial data gathering

# -----------------------------
# Utility Functions
# -----------------------------
def get_timestamp():
    """Returns the current time as a readable string."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# -----------------------------
# Account Balance Function
# -----------------------------
def get_usd_balance():
    """
    Queries Kraken's private Balance endpoint to fetch your account balance.
    Returns the USD balance (as a float) if available.
    """
    try:
        response = kraken.query_private("Balance")
        if response.get("error"):
            print(f"[{get_timestamp()}] Error fetching balance: {response.get('error')}")
            return None
        balance = response.get("result", {})
        # Kraken typically returns the USD balance under "ZUSD"
        usd_balance = balance.get("ZUSD")
        if usd_balance is None:
            usd_balance = balance.get("USD")
        if usd_balance is not None:
            return float(usd_balance)
        else:
            print(f"[{get_timestamp()}] USD balance not found in account.")
            return None
    except Exception as e:
        print(f"[{get_timestamp()}] Exception fetching balance: {e}")
        return None

# -----------------------------
# Data Fetching Functions
# -----------------------------
def get_market_data(pair="XXBTZUSD", count=MA_LENGTH + 10, interval=CANDLE_INTERVAL):
    """
    Fetches historical OHLC data from Kraken.
    Returns a DataFrame with numeric 'close' prices and integer 'time' (Unix timestamp).
    """
    try:
        response = kraken.query_public("OHLC", {"pair": pair, "interval": interval, "count": count})
        if "result" not in response:
            return None
        ohlc = response["result"].get(pair, [])
        if not ohlc:
            return None
        df = pd.DataFrame(ohlc, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
        df["close"] = df["close"].astype(float)
        df["time"] = df["time"].astype(int)
        return df
    except Exception as e:
        print(f"[{get_timestamp()}] Error fetching market data: {e}")
        return None

def get_order_book(pair="XXBTZUSD"):
    """
    Fetch the current order book for a pair.
    Returns best_bid, best_ask, and spread.
    """
    try:
        response = kraken.query_public("Depth", {"pair": pair, "count": 1})
        if "result" not in response:
            return None, None, None
        data = response["result"].get(pair)
        best_bid = float(data["bids"][0][0])
        best_ask = float(data["asks"][0][0])
        spread = best_ask - best_bid
        return best_bid, best_ask, spread
    except Exception as e:
        print(f"[{get_timestamp()}] Error fetching order book: {e}")
        return None, None, None

# -----------------------------
# Signal Calculation
# -----------------------------
def calculate_signals(df):
    """
    Calculates the SMA and checks for trade signals.
    Returns a tuple (signal, last_row) where signal is one of:
      - "Hold" (or "Cooldown")
      - "Buy"
      - "Exit Profit"
      - "Exit Stop Loss"
      - "Move Stop to Entry"
    and last_row is the most recent candle (as a Series).
    """
    global last_trade_bar, long_break_even_triggered, position_size, entry_price
    global last_sma_time, sma_initialized

    if len(df) < MA_LENGTH:
        if not sma_initialized:
            print(f"{get_timestamp()} - SMA UPDATE")
            print("Gathering initial data for SMA calculation...")
            sma_initialized = True
        return "Hold", None

    df["SMA"] = df["close"].rolling(window=MA_LENGTH).mean()
    df.dropna(inplace=True)
    if df.empty:
        return "Hold", None

    last_row = df.iloc[-1]
    current_candle_time = last_row["time"]
    deviation_perc = (last_row["close"] - last_row["SMA"]) / last_row["SMA"]

    # Print SMA update only for a new candle.
    if last_sma_time is None or current_candle_time > last_sma_time:
        readable_time = datetime.datetime.fromtimestamp(current_candle_time).strftime("%Y-%m-%d %H:%M:%S")
        print("=" * 44)
        print(f"Candle Time: {readable_time}")
        print(f"Close Price : ${last_row['close']:,.2f}")
        print(f"SMA         : ${last_row['SMA']:,.2f}")
        print(f"Deviation   : {deviation_perc*100:.4f}%")
        print("=" * 44)
        last_sma_time = current_candle_time

    # Enforce a cooldown period (in candles) after the last trade.
    if last_trade_bar is not None and (current_candle_time - last_trade_bar) < COOLDOWN_BARS * CANDLE_INTERVAL * 60:
        return "Cooldown", last_row

    # Signal to Buy if flat and current price is below SMA by more than the threshold.
    if position_size == 0 and deviation_perc < -THRESHOLD_PERC:
        return "Buy", last_row

    # If in a position, check for stop loss, break-even, and profit target.
    if position_size > 0:
        stop_price = entry_price * (1 - STOP_LOSS_PERC)
        profit_target = entry_price * (1 + PROFIT_TARGET_PERC)
        if last_row["close"] <= stop_price:
            return "Exit Stop Loss", last_row
        if not long_break_even_triggered and last_row["close"] >= entry_price * (1 + 2 * STOP_LOSS_PERC):
            return "Move Stop to Entry", last_row
        if last_row["close"] >= profit_target:
            return "Exit Profit", last_row

    return "Hold", last_row

# -----------------------------
# Trade Execution (Simulated) Using Order Book Data
# -----------------------------
def execute_trade(signal, last_row, pair="XXBTZUSD"):
    """
    Simulates executing a trade based on the signal.
    Uses current order book data to account for the spread.
    For a Buy, it calculates trade quantity as 1% of your total USD assets divided by the effective entry price.
    On exit, it prints the realized profit or loss.
    Additionally, upon a Buy, it prints the stop loss, take profit, and break-even levels.
    """
    global last_trade_bar, position_size, entry_price, long_break_even_triggered

    best_bid, best_ask, spread = get_order_book(pair)
    if best_bid is None or best_ask is None:
        print("=" * 44)
        print(f"{get_timestamp()} - ORDER BOOK ERROR")
        print("Order book unavailable. Falling back to candle price.")
        print("=" * 44)
        effective_price = last_row["close"]
    else:
        if signal == "Buy":
            effective_price = best_ask   # Price to buy (ask)
        elif signal in ["Exit Profit", "Exit Stop Loss"]:
            effective_price = best_bid   # Price to sell (bid)
        else:
            effective_price = last_row["close"]

    if signal == "Buy":
        # Compute trade quantity as 1% of USD assets divided by the effective entry price.
        usd_balance = get_usd_balance()
        if usd_balance is None:
            print("=" * 44)
            print(f"{get_timestamp()} - TRADE ABORTED")
            print("Error: USD balance unavailable. Aborting trade.")
            print("=" * 44)
            return
        trade_quantity = (usd_balance * 0.01) / effective_price
        entry_price = effective_price
        position_size = trade_quantity
        last_trade_bar = last_row["time"]

        # Calculate risk management levels.
        stop_loss_price   = entry_price * (1 - STOP_LOSS_PERC)
        take_profit_price = entry_price * (1 + PROFIT_TARGET_PERC)
        break_even_price  = entry_price * (1 + 2 * STOP_LOSS_PERC)

        print("=" * 44)
        print(f"{get_timestamp()} - BUY Signal Executed")
        print(f"Position Size      : {trade_quantity:.8f} BTC")
        print(f"Effective Entry    : ${entry_price:,.2f}")
        print(f"Best Ask Price     : ${best_ask:,.2f}")
        print(f"Simulated Spread   : ${spread:,.2f}")
        print("Risk Management Levels:")
        print(f"  Stop Loss        : ${stop_loss_price:,.2f}")
        print(f"  Take Profit      : ${take_profit_price:,.2f}")
        print(f"  Break-Even       : ${break_even_price:,.2f}")
        print("=" * 44)
    elif signal in ["Exit Profit", "Exit Stop Loss"]:
        pnl = (effective_price - entry_price) * position_size
        pnl_percentage = (effective_price - entry_price) / entry_price * 100
        exit_type = "Profit" if signal == "Exit Profit" else "Stop Loss"
        print("=" * 44)
        print(f"{get_timestamp()} - SELL ({exit_type}) Signal Executed")
        print(f"Exiting Position   : {position_size:.8f} BTC")
        print(f"Effective Exit     : ${effective_price:,.2f}")
        print(f"Best Bid Price     : ${best_bid:,.2f}")
        print(f"Simulated Spread   : ${spread:,.2f}")
        print(f"Realized PnL       : ${pnl:,.4f} ({pnl_percentage:.2f}%)")
        print("=" * 44)
        # Reset trade variables.
        position_size = 0
        entry_price = None
        long_break_even_triggered = False
        last_trade_bar = last_row["time"]
    elif signal == "Move Stop to Entry":
        long_break_even_triggered = True
        print("=" * 44)
        print(f"{get_timestamp()} - BREAK-EVEN Signal")
        print(f"Stop moved to entry price ${entry_price:,.2f}.")
        print("=" * 44)

# -----------------------------
# Main Loop
# -----------------------------
if __name__ == "__main__":
    # Test connection to Kraken.
    test = kraken.query_public("Time")
    if "result" in test:
        print(f"{get_timestamp()} - CONNECTION TEST")
        print("Successfully connected to Kraken.")
    else:
        print(f"{get_timestamp()} - CONNECTION ERROR")
        print("Failed to connect to Kraken. Exiting.")
        exit(1)

    print(f"{get_timestamp()} - STARTING Kraken Mean Reversion Bot...")

    while True:
        df = get_market_data()
        if df is not None:
            signal, last_row = calculate_signals(df)
            if signal in ["Buy", "Exit Profit", "Exit Stop Loss", "Move Stop to Entry"]:
                execute_trade(signal, last_row)
        # Check conditions more frequently if in a trade.
        sleep_interval = 1 if position_size > 0 else 10
        time.sleep(sleep_interval)
