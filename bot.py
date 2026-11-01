import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://fapi.binance.com"  # Public market data only; no authenticated or live orders.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "15m"
REFERENCE_CAPITAL = 1500.0
RISK_FRACTION = 0.01
MAX_LEVERAGE = 3
MAX_DAILY_LOSS = 45.0  # Planning limit only; this signal-only version cannot enforce account losses.
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FreeCloudDemoSignalBot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))

def ema(values, period):
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result

def rsi(values, period=14):
    changes = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes[-period:]]
    losses = [max(-x, 0) for x in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(klines, period=14):
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        trs.append(max(high-low, abs(high-prev_close), abs(low-prev_close)))
    return sum(trs[-period:]) / period

def notify(title, message, priority="default"):
    if not TOPIC:
        print("NTFY_TOPIC is not set; printing notification only.")
        print(title + "\n" + message)
        return
    url = f"{NTFY_SERVER}/{urllib.parse.quote(TOPIC, safe='')}"
    data = message.encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Title": title, "Priority": priority, "Tags": "chart_with_upwards_trend"}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        print("Push notification status:", response.status)

def analyze(symbol):
    params = urllib.parse.urlencode({"symbol": symbol, "interval": INTERVAL, "limit": 100})
    rows = get_json(f"{BASE}/fapi/v1/klines?{params}")
    # Ignore the currently-forming candle; use the latest fully closed candle.
    closed = rows[:-1]
    closes = [float(row[4]) for row in closed]
    if len(closes) < 60:
        return None

    fast_now = ema(closes[-60:], 20)
    slow_now = ema(closes[-60:], 50)
    fast_prev = ema(closes[-61:-1], 20)
    last_close = closes[-1]
    prev_close = closes[-2]
    momentum = rsi(closes)
    volatility = atr(closed)
    candle_time = datetime.fromtimestamp(closed[-1][6] / 1000, tz=timezone.utc).isoformat()

    direction = None
    # Balanced trend-pullback trigger; deliberately selective, not a guarantee of profit.
    if fast_now > slow_now and last_close > fast_now and prev_close <= fast_prev and 50 <= momentum <= 67:
        direction = "LONG"
        stop_distance = max(1.5 * volatility, last_close * 0.004)
    elif fast_now < slow_now and last_close < fast_now and prev_close >= fast_prev and 33 <= momentum <= 50:
        direction = "SHORT"
        stop_distance = max(1.5 * volatility, last_close * 0.004)

    if not direction:
        print(f"{symbol}: no setup | close={last_close:.6g} RSI={momentum:.1f}")
        return None

    risk_usdt = REFERENCE_CAPITAL * RISK_FRACTION
    quantity = risk_usdt / stop_distance
    notional = quantity * last_close
    max_notional = REFERENCE_CAPITAL * MAX_LEVERAGE
    if notional > max_notional:
        quantity = max_notional / last_close
        notional = quantity * last_close
        estimated_risk = quantity * stop_distance
    else:
        estimated_risk = risk_usdt

    if direction == "LONG":
        stop = last_close - stop_distance
        target = last_close + 2 * stop_distance
    else:
        stop = last_close + stop_distance
        target = last_close - 2 * stop_distance

    return {
        "symbol": symbol, "direction": direction, "price": last_close,
        "stop": stop, "target": target, "quantity_estimate": quantity,
        "notional_estimate": notional, "risk_estimate": estimated_risk,
        "rsi": momentum, "candle_time": candle_time
    }

def main():
    if not TOPIC:
        print("WARNING: Set NTFY_TOPIC in GitHub Actions secrets to receive iPhone push notifications.")
    print("Signal-only demo bot. No API key, account access, or order placement.")
    for symbol in SYMBOLS:
        try:
            signal = analyze(symbol)
            if signal:
                msg = (
                    f"{signal['symbol']} {signal['direction']} setup (SIGNAL ONLY)\n"
                    f"Reference entry: {signal['price']:.6g}\n"
                    f"Stop reference: {signal['stop']:.6g}\n"
                    f"Target reference (2R): {signal['target']:.6g}\n"
                    f"Estimated quantity: {signal['quantity_estimate']:.6g}\n"
                    f"Estimated notional: {signal['notional_estimate']:.2f} USDT\n"
                    f"Estimated risk: {signal['risk_estimate']:.2f} USDT\n"
                    f"RSI: {signal['rsi']:.1f}\n"
                    f"Closed candle: {signal['candle_time']}\n"
                    "No order was placed. Check spread, funding, liquidity and news manually."
                )
                notify(f"{signal['symbol']} {signal['direction']} signal", msg, "high")
                print(msg)
        except Exception as exc:
            print(f"{symbol}: ERROR: {exc}")
    print("Scan complete.")

if __name__ == "__main__":
    main()
