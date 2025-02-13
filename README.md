# HFT Mean Reversion Strategy Bot

This repository contains a high-frequency trading (HFT) mean reversion strategy for Binance Futures. The strategy connects to the Binance Futures Testnet, listens to real-time market data via WebSocket, calculates a simple moving average (SMA), and automatically places trades based on the deviation from the SMA.

## Features

- **Real-Time Data Streaming:** Uses Binance Futures WebSocket to receive live kline data.
- **Mean Reversion Strategy:** Calculates a configurable SMA and triggers trades when the current price deviates from it beyond a specified threshold.
- **Risk Management:** Dynamically sizes positions based on a configurable percentage of your USDT balance.
- **Automated Trade Execution:** Places MARKET orders for entry and exit positions with defined stop-loss and profit target levels.
- **Cool-Down Mechanism:** Prevents overtrading by enforcing a cool-down period between trades.
- **Binance Futures Testnet:** Runs on Binance Futures Testnet to allow safe testing before deploying with live funds.

## Requirements

- Python 3.7+
- [python-binance](https://github.com/sammchardy/python-binance)
- [numpy](https://numpy.org/)

Install the required Python packages using pip:

```bash
pip install python-binance numpy
```

## Setup & Configuration

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/yourusername/hft-mean-reversion-strategy.git
   cd hft-mean-reversion-strategy
   ```

2. **API Credentials:**

   Update the API credentials in the script:

   ```python
   API_KEY = "your_api_key_here"
   API_SECRET = "your_api_secret_here"
   ```

   > **Note:** This code is configured to use the Binance Futures Testnet. For live trading, ensure you update the API URL and understand the risks involved.

3. **Strategy Parameters:**

   You can customize the strategy by changing the parameters in the `HFTMeanReversionStrategy` class instantiation:
   
   - `symbol`: Trading symbol (default: 'BTCUSDT').
   - `ma_length`: Number of bars to calculate the SMA (default: 20).
   - `threshold_perc`: Percentage deviation from the SMA to trigger a trade (default: 0.2).
   - `risk_perc_input`: Percentage of your USDT balance to risk per trade (default: 1.0).
   - `stop_loss_perc_input`: Stop loss percentage (default: 0.2).
   - `profit_target_perc_input`: Profit target percentage (default: 0.8).
   - `cooldown_bars`: Number of bars to wait after a trade before opening a new one (default: 3).

## How It Works

1. **Data Collection & SMA Calculation:**
   - The bot subscribes to Binance Futures WebSocket for kline data (set to 5-minute intervals in the code).
   - It collects the closing prices and computes an SMA once enough data points are available.

2. **Trade Signal Generation:**
   - When the price deviates from the SMA by more than the specified threshold, and if the cool-down period has passed, a trading signal is generated.
   - A "long" position is initiated when the price is below the SMA; a "short" position is initiated when the price is above the SMA.

3. **Position Management:**
   - The bot sets a stop loss and a profit target for each trade.
   - It monitors the open position and exits if the profit target is reached, the stop loss is triggered, or if the price reverts back to the SMA.

4. **Risk Management & Order Execution:**
   - Position size is calculated based on a percentage of the available USDT balance.
   - Orders are executed as MARKET orders using the Binance API.

## Running the Bot

Execute the main script to start the bot:

```bash
python your_script_name.py
```

Upon running, the bot will:
- Verify the Binance Futures Testnet connectivity.
- Start listening to the specified kline channel.
- Log trade actions and account balance updates to the console.

## Disclaimer

This project is provided for educational and testing purposes only. Trading cryptocurrencies involves substantial risk. Always test strategies on the Binance Futures Testnet before deploying with real funds. The author is not responsible for any losses incurred while using this bot.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more details.