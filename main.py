import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_URL = "https://fapi.bitunix.com"
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))
MAX_SYMBOLS = int(os.getenv("MAX_SYMBOLS", "250"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "5"))
REQUEST_TIMEOUT = 15.0

# Selected timeframe controls BTC bias; each value is (coin higher TF, coin lower TF).
TIMEFRAME_PAIRS = {
    "1d": ("4h", "1h"),
    "4h": ("1h", "15m"),
    "1h": ("15m", "3m"),
    "15m": ("3m", "1m"),
}
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000,
}

app = FastAPI(title="Bitunix MACD Screener", version="1.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def home():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


async def api_get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> Any:
    response = await client.get(f"{BASE_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
        raise RuntimeError(f"Bitunix API error: {payload.get('msg', payload.get('code'))}")
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def unwrap_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "symbols", "tradingPairs", "data", "rows", "klines"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


async def get_symbols(client: httpx.AsyncClient) -> list[str]:
    raw = await api_get(client, "/api/v1/futures/market/trading_pairs")
    pairs = unwrap_list(raw)
    symbols = []
    for item in pairs:
        if isinstance(item, str):
            symbol = item
        elif isinstance(item, dict):
            symbol = item.get("symbol") or item.get("symbolName") or item.get("s")
        else:
            symbol = None
        if symbol and str(symbol).upper().endswith("USDT"):
            symbols.append(str(symbol).upper())
    symbols = sorted(set(symbols))
    if "BTCUSDT" in symbols:
        symbols.remove("BTCUSDT")
        symbols.insert(0, "BTCUSDT")
    return symbols[:MAX_SYMBOLS]


def normalize_klines(raw: Any) -> list[dict]:
    rows = unwrap_list(raw)
    if not rows and isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, list):
                rows = value
                break
    out = []
    for row in rows:
        try:
            if isinstance(row, dict):
                close_raw = row.get("close", row.get("c"))
                ts_raw = row.get("time", row.get("ts", row.get("openTime", row.get("t", 0))))
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                # Common kline array order: timestamp, open, high, low, close, ...
                ts_raw, close_raw = row[0], row[4]
            else:
                continue
            close = float(close_raw)
            ts = int(float(ts_raw))
            # Normalize seconds to milliseconds when necessary.
            if ts and ts < 100_000_000_000:
                ts *= 1000
            if close > 0 and ts > 0:
                out.append({"close": close, "time": ts})
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda x: x["time"])
    return out


def closed_candles(rows: list[dict], interval: str) -> list[dict]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    interval_ms = INTERVAL_MS[interval]
    # Defensive check: the candle's open time plus its duration must have passed.
    return [r for r in rows if r["time"] + interval_ms <= now_ms]


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def macd(values: list[float]) -> tuple[float, float] | None:
    if len(values) < MACD_SLOW + MACD_SIGNAL + 5:
        return None
    fast = ema(values, MACD_FAST)
    slow = ema(values, MACD_SLOW)
    line = [a - b for a, b in zip(fast, slow)]
    signal_line = ema(line, MACD_SIGNAL)
    return line[-1], signal_line[-1]


def macd_direction(values: tuple[float, float] | None) -> str:
    if values is None:
        return "NEUTRAL"
    line, signal = values
    if line > 0 and line > signal:
        return "LONG"
    if line < 0 and line < signal:
        return "SHORT"
    return "NEUTRAL"


async def fetch_macd(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, symbol: str, interval: str
) -> tuple[str, str, tuple[float, float] | None, str | None]:
    async with semaphore:
        try:
            raw = await api_get(
                client,
                "/api/v1/futures/market/kline",
                {"symbol": symbol, "interval": interval, "limit": 100, "type": "LAST_PRICE"},
            )
            rows = closed_candles(normalize_klines(raw), interval)
            result = macd([r["close"] for r in rows])
            if result is None:
                return symbol, interval, None, f"Not enough closed candles for {interval}"
            return symbol, interval, result, None
        except Exception as exc:
            return symbol, interval, None, str(exc)


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "Bitunix MACD Screener"}


@app.get("/api/scan")
async def scan(
    timeframe: str = Query("1h", pattern="^(15m|1h|4h|1d)$"),
    limit: int = Query(120, ge=10, le=250),
):
    higher_tf, lower_tf = TIMEFRAME_PAIRS[timeframe]
    async with httpx.AsyncClient(headers={"User-Agent": "Bitunix-MACD-Screener/1.1"}) as client:
        try:
            all_symbols = await get_symbols(client)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not retrieve Bitunix USDT perpetual pairs: {exc}")

        symbols = all_symbols[:min(limit, MAX_SYMBOLS)]
        if "BTCUSDT" not in symbols and "BTCUSDT" in all_symbols:
            symbols = ["BTCUSDT"] + symbols[:-1]

        semaphore = asyncio.Semaphore(CONCURRENCY)
        _, _, btc_values, btc_error = await fetch_macd(client, semaphore, "BTCUSDT", timeframe)
        if btc_values is None:
            raise HTTPException(status_code=502, detail=f"Could not calculate BTC MACD ({timeframe}): {btc_error}")
        bias = macd_direction(btc_values)

        tasks = []
        for symbol in symbols:
            if symbol == "BTCUSDT":
                continue
            tasks.append(fetch_macd(client, semaphore, symbol, higher_tf))
            tasks.append(fetch_macd(client, semaphore, symbol, lower_tf))
        fetched = await asyncio.gather(*tasks)
        by_symbol: dict[str, dict[str, tuple[float, float] | None]] = {}
        errors = 0
        for symbol, interval, values, error in fetched:
            by_symbol.setdefault(symbol, {})[interval] = values
            if error or values is None:
                errors += 1

        matches = []
        if bias in ("LONG", "SHORT"):
            for symbol, values_by_tf in by_symbol.items():
                higher_dir = macd_direction(values_by_tf.get(higher_tf))
                lower_dir = macd_direction(values_by_tf.get(lower_tf))
                if bias == "LONG" and higher_dir == "LONG" and lower_dir == "SHORT":
                    matches.append({"coin": symbol, "signal": "LONG"})
                elif bias == "SHORT" and higher_dir == "SHORT" and lower_dir == "LONG":
                    matches.append({"coin": symbol, "signal": "SHORT"})

        matches.sort(key=lambda x: x["coin"])
        return {
            "exchange": "Bitunix USDT Perpetuals",
            "selected_timeframe": timeframe,
            "bias_timeframe": timeframe,
            "coin_higher_timeframe": higher_tf,
            "coin_lower_timeframe": lower_tf,
            "btc_bias": bias,
            "results": matches,
            "scanned": len(symbols) - (1 if "BTCUSDT" in symbols else 0),
            "errors": errors,
            "last_scan_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rule": "Latest closed candles only. LONG: BTC selected-timeframe MACD is LONG, coin higher-timeframe MACD is LONG, and coin lower-timeframe MACD is SHORT. SHORT is the inverse.",
        }
