# Bitunix Online MACD Screener

Responsive web app for scanning Bitunix USDT perpetual contracts. Python/FastAPI backend, HTML/CSS/JavaScript frontend, manual scan button. Designed for desktop and mobile browsers.

## Signal rules

MACD uses the standard 12-period EMA, 26-period EMA, and 9-period signal line. The selected main timeframe sets the BTC bias timeframe and automatically chooses the two coin MACD timeframes:

| Selected timeframe | BTC bias | Coin higher timeframe | Coin lower timeframe |
|---|---|---|---|
| 1D | 1D | 4H | 1H |
| 4H | 4H | 1H | 15M |
| 1H | 1H | 15M | 3M |
| 15M | 15M | 3M | 1M |

MACD direction definition:
- LONG: MACD line is above zero and above its signal line.
- SHORT: MACD line is below zero and below its signal line.
- Otherwise: NEUTRAL.

A setup is shown only when all conditions agree:
- LONG: BTC bias on the selected timeframe is LONG, coin higher-timeframe MACD is LONG, and coin lower-timeframe MACD is SHORT.
- SHORT: BTC bias is SHORT, coin higher-timeframe MACD is SHORT, and coin lower-timeframe MACD is LONG.
- The results table displays only the coin symbol and LONG/SHORT signal.
- Every timeframe condition uses only the most recently closed candle. A defensive timestamp check filters out a candle whose duration has not yet elapsed.

The screener does not place orders and does not require a Bitunix API key.

## Run locally on Windows

1. Install Python 3.11 or newer from https://www.python.org/downloads/ (enable **Add Python to PATH** during setup).
2. Extract this ZIP.
3. Open Command Prompt in the extracted folder.
4. Run:

   ```bat
   py -m venv .venv
   .venv\\Scripts\\activate
   pip install -r requirements.txt
   uvicorn main:app --reload
   ```

5. Open http://127.0.0.1:8000 in a browser.

## Deploy to a public URL with Render + GitHub

1. Create a GitHub repository, for example `bitunix-macd-screener`, and upload the contents of this folder to the repository root.
2. Sign in to https://render.com and choose **New → Web Service**.
3. Connect the repository.
4. Use:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/api/health`
5. Deploy and open the URL Render provides. It will work on desktop and mobile.
6. Test `/api/health`, then press **Scan market** on the home page.

Render free services may sleep when inactive. A scan makes one BTC request plus two timeframe requests per scanned coin; test with 30 or 60 pairs first to check API latency and rate limits.

## Configuration

Environment variables:
- `MAX_SYMBOLS`: maximum number of pairs allowed per scan (default 250)
- `CONCURRENCY`: concurrent Bitunix requests (default 5)
- `MACD_FAST`: default 12
- `MACD_SLOW`: default 26
- `MACD_SIGNAL`: default 9

## API endpoints

- `GET /api/health`
- `GET /api/scan?timeframe=1h&limit=60`

Allowed selected timeframes: `15m`, `1h`, `4h`, `1d`.

## Important notes

- The hosted URL does not exist until you deploy this project to an account you control. This ZIP contains the updated source code but does not publish to GitHub or Render automatically.
- Public API availability, rate limits, symbols and response structure may change. If scanning fails, check the current Bitunix API documentation and deployment logs.
- This tool identifies conditions only; it does not predict outcomes or guarantee profitable signals.
