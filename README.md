# HFT Mean Reversion Strategy for Binance Futures

This repository contains a Python implementation of a high-frequency trading (HFT) mean reversion strategy designed for Binance Futures. The strategy uses real-time kline (candlestick) data from Binance's Futures Testnet to identify trading signals based on the deviation of the current price from its simple moving average (SMA). When the price deviates by a specified threshold, the strategy enters a position and manages it using defined stop-loss and profit target parameters.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Strategy Details](#strategy-details)
- [Disclaimer](#disclaimer)

## Overview

The strategy works as follows:

1. **Data Collection:**  
   The strategy connects to Binance Futures Testnet using WebSockets and listens for 5-minute candlestick (kline) data. It stores the closing prices of each completed candle.

2. **Signal Generation:**  
   Once enough data points are collected, the strategy calculates the SMA over a configurable number of bars. It then determines the percentage deviation of the current close from the SMA:
   - If the price falls significantly below the SMA (deviation is more negative than a threshold), it triggers a long (buy) signal.
   - If the price rises significantly above the SMA (deviation is more positive than a threshold), it triggers a short (sell) signal.

3. **Risk Management & Order Execution:**  
   When a trade signal is generated:
   - The strategy calculates the appropriate position size based on a predefined risk percentage of the account balance.
   - It places a market order to enter the trade.
   - It sets stop-loss and profit target levels for the position.
   - During the trade, the strategy manages the position, including adjusting the stop-loss to break-even if the market moves favorably, and exits the position when a predefined exit condition is met (profit target reached, stop-loss hit, or price reverting back to the SMA).

4. **Logging:**  
   All trades and significant events are logged with timestamps for tracking and debugging purposes.

## Features

- **Real-time Data:** Connects to Binance Futures Testnet via WebSockets for live candlestick data.
- **Configurable Parameters:** Customize SMA length, threshold percentage for signal generation, risk percentage, stop-loss percentage, profit target percentage, and cooldown periods.
- **Risk Management:** Calculates trade size based on the available USDT balance and manages stops to protect capital.
- **Testnet Integration:** Uses Binance Futures Testnet for safe strategy development and testing.

## Prerequisites

- Python 3.7 or higher
- An API key and secret for Binance Futures Testnet. (Obtain these from [Binance Testnet](https://testnet.binancefuture.com/))
- The following Python packages:
  - `asyncio`
  - `numpy`
  - `python-binance`
  - `decimal`

## Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/yourusername/hft-mean-reversion.git
   cd hft-mean-reversion
   ```

2. **Create a virtual environment (optional but recommended):**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install the required dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

   *If you don't have a `requirements.txt`, you can install the packages manually:*

   ```bash
   pip install numpy python-binance
   ```

## Usage

1. **Update API Credentials:**

   In the Python file, update the `API_KEY` and `API_SECRET` with your Binance Futures Testnet credentials.

2. **Run the Script:**

   ```bash
   python your_script_name.py
   ```

   The script will:
   - Verify the connection to Binance Futures Testnet.
   - Start listening for 5-minute kline data.
   - Process incoming data, generate trade signals, and execute/manage trades according to the strategy parameters.

## Configuration

You can adjust the strategy parameters by modifying the instantiation of the `HFTMeanReversionStrategy` class in the `main()` function:

```python
strategy = HFTMeanReversionStrategy(
    symbol='BTCUSDT',              # Trading pair
    ma_length=20,                  # Number of bars for SMA calculation
    threshold_perc=0.2,            # Percentage deviation threshold for signal generation
    risk_perc_input=1.0,           # Percentage of balance to risk per trade
    stop_loss_perc_input=0.2,       # Stop loss distance in percentage
    profit_target_perc_input=0.8,   # Profit target distance in percentage
    cooldown_bars=3                # Number of bars to wait between trades
)
```

## Strategy Details

- **Mean Reversion Concept:**  
  The strategy is based on the idea that asset prices will revert to their mean (SMA) over time. Significant deviations from the mean signal potential reversals.

- **Risk Management:**  
  The position size is calculated based on a fixed risk percentage of the USDT balance. The stop loss is determined by a percentage of the entry price, ensuring that potential losses remain controlled.

- **Trade Management:**  
  Once in a trade, the strategy monitors for exit signals such as reaching the profit target, hitting the stop loss, or the price reverting to the SMA. This helps in locking profits or minimizing losses.

## Disclaimer

- **No Financial Advice:**  
  This code is provided for educational and research purposes only. It is not financial advice and should not be used as the sole basis for trading decisions.

- **Risk Warning:**  
  Trading cryptocurrencies, especially with leverage on futures markets, carries significant risk. You should thoroughly test any strategy in a simulated environment (e.g., testnet) before using real funds.

- **Maintenance:**  
  The code is provided "as is" without warranty of any kind. The author is not responsible for any losses incurred using this strategy.