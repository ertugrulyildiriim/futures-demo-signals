
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://demo-fapi.binance.com"
INTERVAL = "15m"
CAPITAL = 1500.0
RISK = 0.01
MAX_LEVERAGE = 3
MAX_POSITIONS = 3
MAX_DAILY_LOSS = 45.0
MIN_VOLUME = 10_000_000
MAX_SPREAD = 0.15
MAX_SYMBOLS = 60
STATE_FILE = "paper_state.json"

TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")


def now():
    return datetime.now(timezone.utc)


def notify(title, message):
    if not TOPIC:
        print("NTFY_TOPIC ayarlanmamış:", title, message)
        return
    try:
        req = urllib.request.Request(
            f"{NTFY_SERVER}/{TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "default",
                "Tags": "chart_with_upwards_trend"
            },
            method="POST"
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:
        print("Bildirim hatası:", exc)



def api(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 paper-demo-scanner/1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode())

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print("BINANCE HTTP HATASI:", exc.code)
        print("HATA AÇIKLAMASI:", exc.reason)
        print("SUNUCU YANITI:", body[:1000])
        raise

    except Exception as exc:
        print("BINANCE BAĞLANTI HATASI:", repr(exc))
        raise



def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "balance": CAPITAL,
            "open": {},
            "history": [],
            "day": now().date().isoformat(),
            "daily_pnl": 0.0
        }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1 - alpha) * result
    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    changes = [values[i] - values[i - 1]
               for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes[-period:]]
    losses = [max(-x, 0) for x in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))


def atr(highs, lows, closes, period=14):
    ranges = []
    for i in range(1, len(closes)):
        ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    if len(ranges) < period:
        return None
    return sum(ranges[-period:]) / period


def get_candidates():
    info = api("/fapi/v1/exchangeInfo")
    tickers = api("/fapi/v1/ticker/24hr")
    books = api("/fapi/v1/ticker/bookTicker")

    volume_map = {x["symbol"]: float(x["quoteVolume"]) for x in tickers}
    book_map = {x["symbol"]: x for x in books}
    current_ms = int(time.time() * 1000)
    candidates = []

    for item in info["symbols"]:
        symbol = item["symbol"]
        if (item.get("status") != "TRADING"
                or item.get("contractType") != "PERPETUAL"
                or item.get("quoteAsset") != "USDT"
                or not symbol.endswith("USDT")):
            continue

        volume = volume_map.get(symbol, 0)
        book = book_map.get(symbol)
        if volume < MIN_VOLUME or not book:
            continue

        bid = float(book["bidPrice"])
        ask = float(book["askPrice"])
        mid = (bid + ask) / 2
        if mid <= 0:
            continue

        spread = (ask - bid) / mid * 100
        if spread > MAX_SPREAD:
            continue

        onboard = item.get("onboardDate", 0)
        if onboard and current_ms - onboard < 30 * 86400000:
            continue

        candidates.append((symbol, volume))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [x[0] for x in candidates[:MAX_SYMBOLS]]


def get_signal(symbol):
    candles = api("/fapi/v1/klines", {
        "symbol": symbol, "interval": INTERVAL, "limit": 100
    })
    if len(candles) < 60:
        return None

    # Son mum henüz kapanmamış olabilir; yalnızca kapanmış mumları kullan.
    candles = candles[:-1]
    closes = [float(x[4]) for x in candles]
    highs = [float(x[2]) for x in candles]
    lows = [float(x[3]) for x in candles]

    fast = ema(closes, 20)
    slow = ema(closes, 50)
    current_rsi = rsi(closes)
    current_atr = atr(highs, lows, closes)

    if not fast or not slow or not current_atr:
        return None

    price = closes[-1]
    if current_atr / price < 0.001:
        return None

    if fast > slow and price > fast and 48 <= current_rsi <= 67:
        side = "LONG"
    elif fast < slow and price < fast and 33 <= current_rsi <= 52:
        side = "SHORT"
    else:
        return None

    return {
        "side": side,
        "price": price,
        "atr": current_atr
    }


def close_position(state, symbol, price, reason):
    pos = state["open"].pop(symbol)
    if pos["side"] == "LONG":
        pnl = (price - pos["entry"]) * pos["qty"]
    else:
        pnl = (pos["entry"] - price) * pos["qty"]

    state["balance"] += pnl
    state["daily_pnl"] += pnl

    trade = {
        "symbol": symbol,
        "side": pos["side"],
        "entry": pos["entry"],
        "exit": price,
        "pnl": round(pnl, 2),
        "reason": reason,
        "closed_at": now().isoformat()
    }
    state["history"].append(trade)
    state["history"] = state["history"][-200:]

    notify(
        f"Sanal işlem kapandı: {symbol}",
        f"{pos['side']} | Kapanış: {price:.6g} | "
        f"Sonuç: {pnl:+.2f} USDT | Sebep: {reason} | "
        f"Sanal bakiye: {state['balance']:.2f} USDT"
    )
    print("KAPANDI:", trade)


def manage_positions(state):
    for symbol in list(state["open"].keys()):
        try:
            data = api("/fapi/v1/ticker/price", {"symbol": symbol})
            price = float(data["price"])
            pos = state["open"][symbol]

            if pos["side"] == "LONG":
                if price <= pos["stop"]:
                    close_position(state, symbol, price, "Stop Loss")
                elif price >= pos["target"]:
                    close_position(state, symbol, price, "Take Profit")
            else:
                if price >= pos["stop"]:
                    close_position(state, symbol, price, "Stop Loss")
                elif price <= pos["target"]:
                    close_position(state, symbol, price, "Take Profit")

        except Exception as exc:
            print("Pozisyon kontrol hatası:", symbol, exc)


def open_position(state, symbol, signal):
    if symbol in state["open"]:
        return
    if len(state["open"]) >= MAX_POSITIONS:
        return
    if state["daily_pnl"] <= -MAX_DAILY_LOSS:
        return

    side = signal["side"]
    entry = signal["price"]
    distance = max(signal["atr"] * 1.5, entry * 0.004)

    if side == "LONG":
        stop = entry - distance
        target = entry + distance * 2
    else:
        stop = entry + distance
        target = entry - distance * 2

    risk_amount = min(state["balance"] * RISK, 15.0)
    qty = risk_amount / distance

    # 3x kaldıraç sınırı: sanal pozisyon büyüklüğü üst limiti.
    max_notional = state["balance"] * MAX_LEVERAGE
    qty = min(qty, max_notional / entry)

    if qty <= 0:
        return

    actual_risk = qty * distance
    state["open"][symbol] = {
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "qty": qty,
        "risk": actual_risk,
        "opened_at": now().isoformat()
    }

    notify(
        f"Yeni sanal işlem: {symbol} {side}",
        f"Giriş: {entry:.6g} | Stop: {stop:.6g} | "
        f"Hedef: {target:.6g} | Tahmini risk: {actual_risk:.2f} USDT | "
        f"Sanal bakiye: {state['balance']:.2f} USDT"
    )
    print("AÇILDI:", symbol, state["open"][symbol])


def main():
    state = load_state()
    today = now().date().isoformat()

    if state.get("day") != today:
        state["day"] = today
        state["daily_pnl"] = 0.0

    print("Sanal bakiye:", state["balance"])
    print("Açık pozisyon:", len(state["open"]))

    manage_positions(state)

    if state["daily_pnl"] <= -MAX_DAILY_LOSS:
        print("Günlük zarar sınırı doldu. Yeni işlem açılmayacak.")
        save_state(state)
        return

    try:
        symbols = get_candidates()
        print("Taranacak uygun parite:", len(symbols))

        for symbol in symbols:
            if symbol in state["open"]:
                continue
            if len(state["open"]) >= MAX_POSITIONS:
                break
            if state["daily_pnl"] <= -MAX_DAILY_LOSS:
                break

            try:
                signal = get_signal(symbol)
                if signal:
                    open_position(state, symbol, signal)
                time.sleep(0.12)
            except Exception as exc:
                print("Sembol atlandı:", symbol, exc)

    except Exception as exc:
        print("Tarama hatası:", exc)

    save_state(state)
    print("Tarama tamamlandı.")
    print("Sanal bakiye:", round(state["balance"], 2))
    print("Açık pozisyon:", len(state["open"]))


if __name__ == "__main__":
    main()
