import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api-demo.bybit.com"
API_KEY = os.getenv("BYBIT_DEMO_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_DEMO_API_SECRET", "").strip()
INTERVAL = "15"
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
RECV_WINDOW = "5000"


def now():
    return datetime.now(timezone.utc)


def notify(title, message):
    if not TOPIC:
        print("NTFY_TOPIC ayarlanmamis:", title, message)
        return
    try:
        req = urllib.request.Request(
            f"{NTFY_SERVER}/{TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "default",
                     "Tags": "chart_with_upwards_trend"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            response.read()
    except Exception as exc:
        print("Bildirim hatasi:", repr(exc))


def request_json(path, params=None, signed=False):
    params = dict(params or {})
    headers = {"User-Agent": "bybit-demo-paper-scanner/1.0"}
    if signed:
        if not API_KEY or not API_SECRET:
            raise RuntimeError("BYBIT_DEMO_API_KEY veya BYBIT_DEMO_API_SECRET eksik")
        timestamp = str(int(time.time() * 1000))
        query = urllib.parse.urlencode(sorted(params.items()))
        payload = timestamp + API_KEY + RECV_WINDOW + query
        signature = hmac.new(API_SECRET.encode(), payload.encode(),
                             hashlib.sha256).hexdigest()
        headers.update({
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": signature})
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(sorted(params.items()))
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print("BYBIT HTTP HATASI:", exc.code, body[:1000])
        raise
    if data.get("retCode", 0) != 0:
        raise RuntimeError(f"Bybit API hatasi {data.get('retCode')}: {data.get('retMsg')}")
    return data


def public_api(path, params=None):
    return request_json(path, params, False)


def private_api(path, params=None):
    return request_json(path, params, True)


def get_demo_usdt_balance():
    data = private_api("/v5/account/wallet-balance",
                       {"accountType": "UNIFIED", "coin": "USDT"})
    for account in data.get("result", {}).get("list", []):
        for coin in account.get("coin", []):
            if coin.get("coin") == "USDT":
                return coin.get("walletBalance", "0"), coin.get("equity", "0")
    return "0", "0"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"balance": CAPITAL, "open": {}, "history": [],
                "day": now().date().isoformat(), "daily_pnl": 0.0}


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
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes[-period:]]
    losses = [max(-x, 0) for x in changes[-period:]]
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))


def atr(highs, lows, closes, period=14):
    ranges = [max(highs[i] - lows[i],
                  abs(highs[i] - closes[i - 1]),
                  abs(lows[i] - closes[i - 1]))
              for i in range(1, len(closes))]
    return sum(ranges[-period:]) / period if len(ranges) >= period else None


def get_candidates():
    info = public_api("/v5/market/instruments-info",
                      {"category": "linear", "limit": 1000})["result"]["list"]
    tickers = public_api("/v5/market/tickers",
                         {"category": "linear"})["result"]["list"]
    ticker_map = {x["symbol"]: x for x in tickers}
    current_ms, candidates = int(time.time() * 1000), []
    for item in info:
        symbol = item.get("symbol", "")
        if (item.get("status") != "Trading"
                or item.get("contractType") != "LinearPerpetual"
                or item.get("quoteCoin") != "USDT"
                or not symbol.endswith("USDT")):
            continue
        ticker = ticker_map.get(symbol)
        if not ticker or float(ticker.get("turnover24h", 0) or 0) < MIN_VOLUME:
            continue
        bid = float(ticker.get("bid1Price", 0) or 0)
        ask = float(ticker.get("ask1Price", 0) or 0)
        mid = (bid + ask) / 2
        if mid <= 0 or (ask - bid) / mid * 100 > MAX_SPREAD:
            continue
        launch = int(item.get("launchTime", 0) or 0)
        if launch and current_ms - launch < 30 * 86400000:
            continue
        candidates.append((symbol, float(ticker.get("turnover24h", 0))))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [x[0] for x in candidates[:MAX_SYMBOLS]]


def get_signal(symbol):
    rows = public_api("/v5/market/kline",
                      {"category": "linear", "symbol": symbol,
                       "interval": INTERVAL, "limit": 100})["result"]["list"]
    if len(rows) < 60:
        return None
    candles = list(reversed(rows))[:-1]  # son acik mum haric
    closes = [float(x[4]) for x in candles]
    highs = [float(x[2]) for x in candles]
    lows = [float(x[3]) for x in candles]
    fast, slow = ema(closes, 20), ema(closes, 50)
    current_rsi, current_atr = rsi(closes), atr(highs, lows, closes)
    if not fast or not slow or not current_atr:
        return None
    price = closes[-1]
    if price <= 0 or current_atr / price < 0.001:
        return None
    if fast > slow and price > fast and 48 <= current_rsi <= 67:
        side = "LONG"
    elif fast < slow and price < fast and 33 <= current_rsi <= 52:
        side = "SHORT"
    else:
        return None
    return {"side": side, "price": price, "atr": current_atr}


def get_last_price(symbol):
    rows = public_api("/v5/market/tickers",
                      {"category": "linear", "symbol": symbol})["result"]["list"]
    if not rows:
        raise RuntimeError(f"{symbol} fiyat bilgisi gelmedi")
    return float(rows[0]["lastPrice"])


def close_position(state, symbol, price, reason):
    pos = state["open"].pop(symbol)
    pnl = ((price - pos["entry"]) if pos["side"] == "LONG"
           else (pos["entry"] - price)) * pos["qty"]
    state["balance"] += pnl
    state["daily_pnl"] += pnl
    trade = {"symbol": symbol, "side": pos["side"], "entry": pos["entry"],
             "exit": price, "pnl": round(pnl, 2), "reason": reason,
             "closed_at": now().isoformat()}
    state["history"] = (state["history"] + [trade])[-200:]
    notify(f"Sanal islem kapandi: {symbol}",
           f"{pos['side']} | Kapanis: {price:.6g} | Sonuc: {pnl:+.2f} USDT "
           f"| Sebep: {reason} | Sanal bakiye: {state['balance']:.2f} USDT")
    print("KAPANDI:", trade)


def manage_positions(state):
    for symbol in list(state["open"]):
        try:
            price, pos = get_last_price(symbol), state["open"][symbol]
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
            print("Pozisyon kontrol hatasi:", symbol, repr(exc))


def open_position(state, symbol, signal):
    if (symbol in state["open"] or len(state["open"]) >= MAX_POSITIONS
            or state["daily_pnl"] <= -MAX_DAILY_LOSS):
        return
    side, entry = signal["side"], signal["price"]
    distance = max(signal["atr"] * 1.5, entry * 0.004)
    stop, target = ((entry - distance, entry + distance * 2) if side == "LONG"
                    else (entry + distance, entry - distance * 2))
    risk_amount = min(state["balance"] * RISK, 15.0)
    qty = min(risk_amount / distance, state["balance"] * MAX_LEVERAGE / entry)
    if qty <= 0:
        return
    actual_risk = qty * distance
    state["open"][symbol] = {
        "side": side, "entry": entry, "stop": stop, "target": target,
        "qty": qty, "risk": actual_risk, "opened_at": now().isoformat()}
    notify(f"Yeni sanal islem: {symbol} {side}",
           f"Giris: {entry:.6g} | Stop: {stop:.6g} | Hedef: {target:.6g} "
           f"| Tahmini risk: {actual_risk:.2f} USDT "
           f"| Sanal bakiye: {state['balance']:.2f} USDT")
    print("ACILDI:", symbol, state["open"][symbol])


def main():
    wallet, equity = get_demo_usdt_balance()
    print("BYBIT DEMO USDT Cuzdan Bakiyesi:", wallet)
    print("BYBIT DEMO USDT Equity:", equity)
    state = load_state()
    today = now().date().isoformat()
    if state.get("day") != today:
        state["day"], state["daily_pnl"] = today, 0.0
    print("Sanal bakiye:", state["balance"])
    print("Acik sanal pozisyon:", len(state["open"]))
    manage_positions(state)
    if state["daily_pnl"] <= -MAX_DAILY_LOSS:
        print("Gunluk zarar siniri doldu. Yeni sanal islem acilmayacak.")
        save_state(state)
        return
    symbols = get_candidates()
    print("Taranacak uygun parite:", len(symbols))
    for symbol in symbols:
        if symbol in state["open"]:
            continue
        if len(state["open"]) >= MAX_POSITIONS or state["daily_pnl"] <= -MAX_DAILY_LOSS:
            break
        try:
            signal = get_signal(symbol)
            if signal:
                open_position(state, symbol, signal)
            time.sleep(0.12)
        except Exception as exc:
            print("Sembol atlandi:", symbol, repr(exc))
    save_state(state)
    print("Tarama tamamlandi. Sanal bakiye:", round(state["balance"], 2))
    print("Acik sanal pozisyon:", len(state["open"]))


if __name__ == "__main__":
    main()
