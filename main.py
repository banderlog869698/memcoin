import time
import json
import os
import requests
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = "8675665445:AAFBlWZoEkK29-t1gqLLOTMdtE8XofZzdS0"
CHAT_ID = "-1003506073360"

# Виртуальный баланс
START_BALANCE_USD = 1000.0
TRADE_SIZE_USD = 20.0

# ✅ Один Take Profit на +1.5%
TAKE_PROFIT = 1.015

# Time stop: закрыть через 12ч если TP не сработал
MAX_HOLD_HOURS = 12

# Фильтр ликвидности
MIN_LIQUIDITY_USD = 1000.0

# Фильтры входа
MIN_VOLUME_5M = 500
MIN_BUY_SELL_RATIO = 1.2
MIN_MOMENTUM_5M = 3.0

# Плохие часы UTC
BAD_HOURS_UTC = (2, 13, 23)

# Лимиты
MAX_OPEN_POSITIONS = 10
SCAN_INTERVAL = 20
REENTRY_COOLDOWN = 3600
MAX_PROFILES = 40

BLOCK_REPEAT_SYMBOL = True

STATE_FILE = "state.json"

# ============================================================
# HONEYPOT CHECK
# ============================================================

RUGCHECK_MCP = "https://web-production-58d585.up.railway.app/mcp"

def check_honeypot(mint_address: str) -> dict:
    try:
        r = requests.post(
            RUGCHECK_MCP,
            json={"method": "scan_token", "params": {"mint": mint_address}},
            timeout=15
        )
        if r.status_code != 200:
            return {"safe": False, "reason": f"http_{r.status_code}"}
        data = r.json()
        return {
            "safe": data.get("verdict") == "SAFE",
            "verdict": data.get("verdict", "UNKNOWN"),
            "score": data.get("safety_score", 0),
            "risks": data.get("risks", []),
        }
    except Exception as e:
        print(f"[HONEYPOT] {mint_address[:8]}: {e}")
        return {"safe": False, "reason": "check_failed"}

def is_honeypot_free(mint_address: str) -> bool:
    result = check_honeypot(mint_address)
    if not result["safe"]:
        print(f"[HONEYPOT] {mint_address[:8]} REJECTED: {result}")
        return False
    return True

# ============================================================
# DEXSCREENER
# ============================================================

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1"

# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
last_update_id = 0

def telegram(method, data=None):
    try:
        r = requests.post(f"{TELEGRAM_URL}/{method}", json=data or {}, timeout=15)
        return r.json()
    except Exception as e:
        print("[TELEGRAM]", e)
        return None

def send_message(text):
    if not CHAT_ID:
        print("\n" + text + "\n")
        return
    telegram("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    })

def check_start():
    global CHAT_ID, last_update_id
    try:
        r = requests.get(f"{TELEGRAM_URL}/getUpdates", params={
            "offset": last_update_id + 1, "timeout": 1
        }, timeout=5)
        data = r.json()
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            last_update_id = update["update_id"]
            msg = update.get("message")
            if not msg:
                continue
            if msg.get("text") == "/start":
                CHAT_ID = str(msg["chat"]["id"])
                send_message(
                    "🤖 PUMP.FUN PAPER TRADER\n\n"
                    f"💼 Balance: ${START_BALANCE_USD:.2f}\n"
                    f"📦 Trade size: ${TRADE_SIZE_USD:.2f}\n"
                    f"💧 Min liquidity: ${MIN_LIQUIDITY_USD}\n"
                    f"🎯 Take Profit: +1.5%\n"
                    f"⏰ Max hold: {MAX_HOLD_HOURS}h\n"
                    f"🛡 Honeypot check: ON"
                )
    except Exception as e:
        print("[TELEGRAM UPDATE]", e)

# ============================================================
# HELPERS
# ============================================================

def money(value):
    try:
        value = float(value)
        if value >= 1_000_000: return f"${value/1_000_000:.2f}M"
        if value >= 1000: return f"${value/1000:.1f}K"
        return f"${value:.2f}"
    except (TypeError, ValueError):
        return "$0"

def price_fmt(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if value == 0: return "$0"
    if value >= 1: return f"${value:.4f}"
    if value >= 0.001: return f"${value:.6f}"
    if value >= 0.000001: return f"${value:.8f}"
    return f"${value:.12f}"

def pct(value):
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "N/A"

def is_bad_hour():
    utc_hour = datetime.now(timezone.utc).hour
    return utc_hour in BAD_HOURS_UTC

# ============================================================
# MARKET DATA
# ============================================================

def get_profiles():
    try:
        r = requests.get(PROFILES_URL, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print("[PROFILES]", e)
        return []

def get_pairs(chain, token):
    try:
        r = requests.get(f"{PAIRS_URL}/{chain}/{token}", timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, dict):
            return data.get("pairs", [])
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print("[PAIRS]", e)
        return []

def get_best_pair(pairs):
    best = None
    best_liq = 0
    for pair in pairs:
        liq = ((pair.get("liquidity") or {}).get("usd")) or 0
        try:
            liq = float(liq)
        except (TypeError, ValueError):
            liq = 0
        if liq > best_liq:
            best_liq = liq
            best = pair
    return best, best_liq

def extract_market(pair):
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    if base.get("address") == quote.get("address"):
        return None
    try:
        price = float(pair.get("priceUsd"))
    except (TypeError, ValueError):
        return None
    liq = ((pair.get("liquidity") or {}).get("usd")) or 0
    vol = ((pair.get("volume") or {}).get("m5")) or 0
    chg = ((pair.get("priceChange") or {}).get("m5")) or 0
    txns = (pair.get("txns") or {}).get("m5") or {}
    buys = txns.get("buys") or 0
    sells = txns.get("sells") or 0

    def _f(x):
        try: return float(x)
        except (TypeError, ValueError): return 0.0

    return {
        "symbol": base.get("symbol", "UNKNOWN"),
        "name": base.get("name", "Unknown"),
        "address": base.get("address", ""),
        "price": price,
        "liquidity": _f(liq),
        "volume_5m": _f(vol),
        "change_5m": _f(chg),
        "buys_5m": int(buys),
        "sells_5m": int(sells),
        "url": pair.get("url", ""),
        "dex_id": pair.get("dexId", "unknown"),
    }

# ============================================================
# STATE
# ============================================================

balance_usd = START_BALANCE_USD
positions = {}
completed_trades = []
last_entry = {}
bought_tokens = set()
bought_symbols = set()

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "balance_usd": balance_usd,
                "positions": positions,
                "completed_trades": completed_trades,
                "last_entry": last_entry,
                "bought_tokens": list(bought_tokens),
                "bought_symbols": list(bought_symbols),
            }, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("[SAVE STATE]", e)

def load_state():
    global balance_usd, positions, completed_trades, last_entry, bought_tokens, bought_symbols
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        balance_usd = data.get("balance_usd", START_BALANCE_USD)
        positions = data.get("positions", {})
        completed_trades = data.get("completed_trades", [])
        last_entry = data.get("last_entry", {})
        bought_tokens = set(data.get("bought_tokens", []))
        bought_symbols = set(data.get("bought_symbols", []))
        print(f"[STATE] balance=${balance_usd:.2f} positions={len(positions)}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print("[LOAD STATE]", e)

def is_already_bought(market):
    if market["address"] in bought_tokens:
        return True
    if BLOCK_REPEAT_SYMBOL and market["symbol"] in bought_symbols:
        return True
    return False

def mark_as_bought(market):
    bought_tokens.add(market["address"])
    if market["symbol"]:
        bought_symbols.add(market["symbol"])

# ============================================================
# BUY FILTER
# ============================================================

def buy_signal(market):
    if is_bad_hour():
        return False
    if market["liquidity"] < MIN_LIQUIDITY_USD:
        return False
    if is_already_bought(market):
        return False
    if market["volume_5m"] < MIN_VOLUME_5M:
        return False
    if market["change_5m"] < MIN_MOMENTUM_5M:
        return False
    if market["change_5m"] > 200:
        return False
    if market["change_5m"] <= -15:
        return False

    sells = market["sells_5m"] or 1
    ratio = market["buys_5m"] / sells
    if ratio < MIN_BUY_SELL_RATIO:
        return False

    if not is_honeypot_free(market["address"]):
        return False

    return True

# ============================================================
# PAPER BUY
# ============================================================

def paper_buy(market):
    global balance_usd
    token = market["address"]
    if not token or token in positions:
        return
    if len(positions) >= MAX_OPEN_POSITIONS:
        return
    if balance_usd < TRADE_SIZE_USD:
        return
    now = time.time()
    if token in last_entry and now - last_entry[token] < REENTRY_COOLDOWN:
        return

    entry_price = market["price"]
    if entry_price <= 0:
        return

    amount = TRADE_SIZE_USD / entry_price
    balance_usd -= TRADE_SIZE_USD

    positions[token] = {
        "symbol": market["symbol"],
        "name": market["name"],
        "address": token,
        "entry_price": entry_price,
        "amount": amount,
        "cost": TRADE_SIZE_USD,
        "entry_time": now,
        "url": market["url"],
    }
    last_entry[token] = now
    mark_as_bought(market)
    save_state()

    msg = (
        "🟢 PAPER BUY\n\n"
        f"🪙 {market['symbol']}\n"
        f"💵 Entry: {price_fmt(entry_price)}\n"
        f"💰 Position: ${TRADE_SIZE_USD:.2f}\n"
        f"💧 Liquidity: {money(market['liquidity'])}\n"
        f"📊 Vol 5m: {money(market['volume_5m'])} ({pct(market['change_5m'])})\n\n"
        f"🎯 TP: {price_fmt(entry_price * TAKE_PROFIT)} (+1.5%)\n"
        f"⏰ Max hold: {MAX_HOLD_HOURS}h\n\n"
        f"💼 Balance: ${balance_usd:.2f}\n\n"
        f"🔗 {market['url']}"
    )
    print(msg)
    send_message(msg)

# ============================================================
# PAPER SELL
# ============================================================

def paper_sell(token, market, reason):
    global balance_usd
    if token not in positions:
        return
    pos = positions[token]
    entry = pos["entry_price"]
    current = market["price"]
    amount = pos["amount"]
    cost = pos["cost"]

    value = amount * current
    pnl = value - cost
    pnl_pct = (pnl / cost * 100) if cost else 0
    balance_usd += value

    trade = {
        "symbol": pos["symbol"],
        "entry": entry,
        "exit": current,
        "pnl": pnl,
        "pnl_percent": pnl_pct,
        "reason": reason,
        "time": time.time(),
    }
    completed_trades.append(trade)
    del positions[token]
    save_state()

    emoji = "💰" if pnl > 0 else "🛑"
    msg = (
        f"{emoji} PAPER SELL\n\n"
        f"🪙 {pos['symbol']}\n"
        f"📌 Reason: {reason}\n\n"
        f"🟢 Entry: {price_fmt(entry)}\n"
        f"🔴 Exit: {price_fmt(current)}\n\n"
        f"💵 P/L: {pnl:+.2f}$\n"
        f"📈 Return: {pnl_pct:+.2f}%\n\n"
        f"💼 Balance: ${balance_usd:.2f}"
    )
    print(msg)
    send_message(msg)

# ============================================================
# MANAGE POSITIONS
# ============================================================

def manage_positions():
    if not positions:
        return
    now = time.time()
    for token in list(positions.keys()):
        pos = positions[token]
        pairs = get_pairs("solana", token)
        if not pairs:
            continue
        best, _ = get_best_pair(pairs)
        if not best:
            continue
        market = extract_market(best)
        if not market:
            continue

        entry = pos["entry_price"]
        current = market["price"]
        multiple = current / entry if entry > 0 else 1

        # ✅ Один TP на +1.5%
        if multiple >= TAKE_PROFIT:
            paper_sell(token, market, f"TAKE PROFIT (+{(TAKE_PROFIT-1)*100:.1f}%)")
            continue

        # Time stop
        held_hours = (now - pos["entry_time"]) / 3600
        if held_hours >= MAX_HOLD_HOURS:
            paper_sell(token, market, "TIME LIMIT")
            continue

# ============================================================
# SCANNER
# ============================================================

seen_profiles = set()

def scan_new_tokens():
    profiles = get_profiles()
    if not profiles:
        return
    checked = 0
    for profile in profiles:
        if checked >= MAX_PROFILES:
            break
        if profile.get("chainId") != "solana":
            continue
        token = profile.get("tokenAddress")
        if not token or token in seen_profiles:
            continue
        seen_profiles.add(token)
        checked += 1

        pairs = get_pairs("solana", token)
        if not pairs:
            continue
        best, best_liq = get_best_pair(pairs)
        if not best:
            continue
        market = extract_market(best)
        if not market:
            continue

        if buy_signal(market):
            paper_buy(market)

# ============================================================
# CONSOLE DASHBOARD
# ============================================================

def show_console():
    os.system('cls' if os.name == 'nt' else 'clear')

    total_pnl = sum(t["pnl"] for t in completed_trades)
    wins = sum(1 for t in completed_trades if t["pnl"] > 0)
    losses = sum(1 for t in completed_trades if t["pnl"] < 0)
    winrate = (wins / len(completed_trades) * 100) if completed_trades else 0

    print("=" * 70)
    print(f"  💼 BALANCE: ${balance_usd:.2f}  |  📊 WINRATE: {winrate:.1f}%  |  📈 P/L: {total_pnl:+.2f}$")
    print("=" * 70)

    print(f"\n📌 OPEN POSITIONS ({len(positions)}):")
    if not positions:
        print("   (нет открытых позиций)")
    else:
        for mint, pos in positions.items():
            held_h = (time.time() - pos["entry_time"]) / 3600
            print(f"   🪙 {pos['symbol']:12} | entry ${pos['entry_price']:.8f} | {held_h:.1f}h")

    print(f"\n📜 RECENT TRADES ({len(completed_trades)}):")
    for t in completed_trades[-5:]:
        emoji = "✅" if t["pnl"] > 0 else "❌"
        print(f"   {emoji} {t['symbol']:12} | {t['reason']:25} | P/L: {t['pnl']:+.2f}$")

    print(f"\n💰 Virtual Balance: ${balance_usd:.2f}")
    print(f"⏱  Next scan in {SCAN_INTERVAL}s...")
    print("=" * 70)

# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("       PUMP.FUN PAPER TRADER")
    print("=" * 60)
    print(f"Start balance: ${START_BALANCE_USD:.2f}")
    print(f"Trade size: ${TRADE_SIZE_USD:.2f}")
    print(f"Min liquidity: ${MIN_LIQUIDITY_USD}")
    print(f"Take Profit: +{(TAKE_PROFIT-1)*100:.1f}%")
    print(f"Max hold: {MAX_HOLD_HOURS}h")
    print(f"Honeypot check: ON")
    print("=" * 60)

    load_state()

    while True:
        try:
            check_start()
            manage_positions()
            scan_new_tokens()
            show_console()
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\nSTOPPED")
            save_state()
            break
        except Exception as e:
            print("[MAIN ERROR]", e)
            time.sleep(10)

if __name__ == "__main__":
    main()