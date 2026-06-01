# ════════════════════════════════════════════════════════════════
#  COLAB FIX — must be first
# ════════════════════════════════════════════════════════════════
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install",
    "yfinance", "pandas", "numpy", "ta", "python-telegram-bot",
    "schedule", "pytz", "nest_asyncio", "-q"])

import nest_asyncio
nest_asyncio.apply()

# ════════════════════════════════════════════════════════════════
#  ⚙️  YOUR CONFIG — FILL THESE IN
# ════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN_HERE"    # from @BotFather
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"      # from @userinfobot

CAPITAL          = 100_000
RISK_PCT         = 0.015
MAX_POSITIONS    = 2
MAX_MONTHLY_DD   = -0.06
BROKERAGE_RT     = 0.0005
STT_PCT          = 0.001
STCG_TAX         = 0.15
ADX_THRESHOLD    = 20
SL_BUFFER_PCT    = 0.002
PARTIAL_R        = 1.5
FULL_R           = 2.5
TRAIL_ACTIVATE_R = 1.0
TRAIL_DISTANCE_R = 0.8

STOCKS = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "LT.NS", "SBIN.NS", "ITC.NS", "AXISBANK.NS", "BAJFINANCE.NS"
]

STATE_FILE = "smc_state.json"

# ════════════════════════════════════════════════════════════════
#  IMPORTS
# ════════════════════════════════════════════════════════════════

import warnings; warnings.filterwarnings("ignore")
import json, os, time, asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd
import numpy as np
import ta
from telegram import Bot

IST = ZoneInfo("Asia/Kolkata")

# ════════════════════════════════════════════════════════════════
#  STATE
# ════════════════════════════════════════════════════════════════

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "capital":         CAPITAL,
        "start_capital":   CAPITAL,
        "month_start_cap": CAPITAL,
        "open_trades":     [],
        "closed_trades":   [],
        "month":           datetime.now(IST).strftime("%Y-%m"),
        "trade_counter":   0,
    }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ════════════════════════════════════════════════════════════════
#  DATA
# ════════════════════════════════════════════════════════════════

def fetch_ohlcv(ticker, interval, days=400):
    end   = datetime.now(IST)
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"),
                     interval=interval, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.dropna(inplace=True)
    return df

def add_indicators(df):
    df = df.copy()
    df["ADX"]   = ta.trend.ADXIndicator(df["High"], df["Low"], df["Close"], window=14).adx()
    df["RSI"]   = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    return df

# ════════════════════════════════════════════════════════════════
#  SMC LOGIC
# ════════════════════════════════════════════════════════════════

def detect_weekly_bias(df_w):
    if len(df_w) < 6:
        return 0
    recent_high = df_w["High"].iloc[-6:-1].max()
    recent_low  = df_w["Low"].iloc[-6:-1].min()
    last_close  = df_w["Close"].iloc[-1]
    last_ema    = df_w["Close"].ewm(span=10, adjust=False).mean().iloc[-1]
    if last_close > recent_high * 0.98 and last_close > last_ema:
        return 1
    if last_close < recent_low * 1.02 and last_close < last_ema:
        return -1
    return 0

def find_ob_zone(df, bias):
    for i in range(len(df) - 2, max(len(df) - 15, 2), -1):
        if bias == 1:
            if (df["Close"].iloc[i] < df["Open"].iloc[i] and
                    df["Close"].iloc[i+1] > df["High"].iloc[i-1]):
                return df["Low"].iloc[i], df["High"].iloc[i]
        elif bias == -1:
            if (df["Close"].iloc[i] > df["Open"].iloc[i] and
                    df["Close"].iloc[i+1] < df["Low"].iloc[i-1]):
                return df["Low"].iloc[i], df["High"].iloc[i]
    return None, None

def find_fvg(df, bias):
    for i in range(len(df) - 3, max(len(df) - 15, 2), -1):
        if bias == 1:
            gap = df["Low"].iloc[i+1] - df["High"].iloc[i-1]
            if gap > 0 and gap / df["Close"].iloc[i] > 0.003:
                return df["High"].iloc[i-1], df["Low"].iloc[i+1]
        elif bias == -1:
            gap = df["Low"].iloc[i-1] - df["High"].iloc[i+1]
            if gap > 0 and gap / df["Close"].iloc[i] > 0.003:
                return df["High"].iloc[i+1], df["Low"].iloc[i-1]
    return None, None

def detect_choch(df_4h, bias):
    if len(df_4h) < 5:
        return False
    recent     = df_4h.iloc[-5:]
    swing_low  = recent["Low"].iloc[:-1].min()
    swing_high = recent["High"].iloc[:-1].max()
    last       = recent.iloc[-1]
    if bias == 1:
        return last["Low"] < swing_low and last["Close"] > swing_low
    elif bias == -1:
        return last["High"] > swing_high and last["Close"] < swing_high
    return False

# ════════════════════════════════════════════════════════════════
#  TRAILING SL
# ════════════════════════════════════════════════════════════════

def update_trailing_sl(trade, current_high, current_low):
    entry = trade["entry_price"]
    risk  = trade["risk_per_share"]
    if trade["bias"] == 1 and risk > 0:
        profit_r = (current_high - entry) / risk
        if profit_r >= TRAIL_ACTIVATE_R:
            new_sl = current_high - (TRAIL_DISTANCE_R * risk)
            if new_sl > trade["stop_loss"]:
                trade["stop_loss"]   = round(new_sl, 2)
                trade["trailing_on"] = True
        if not trade.get("partial_done") and current_high >= entry + PARTIAL_R * risk:
            trade["partial_done"] = True
            half = trade["shares"] // 2
            trade["realised_pnl"] = trade.get("realised_pnl", 0) + half * (PARTIAL_R * risk)
            trade["shares"] -= half
    return trade

def check_exit(trade, high, low, close):
    entry = trade["entry_price"]
    risk  = trade["risk_per_share"]
    if trade["bias"] == 1:
        if low <= trade["stop_loss"]:
            reason = "🔴 Trail SL Hit" if trade.get("trailing_on") else "🔴 Stop Loss Hit"
            return True, trade["stop_loss"], reason
        if high >= entry + FULL_R * risk:
            return True, round(entry + FULL_R * risk, 2), "✅ Target Hit (2.5R)"
    return False, None, None

def calc_net_pnl(shares, entry, exit_p, realised_pnl=0):
    gross = shares * (exit_p - entry) + realised_pnl
    costs = shares * entry * (BROKERAGE_RT + STT_PCT)
    tax   = max(0, gross * STCG_TAX)
    return round(gross - costs - tax, 2)

# ════════════════════════════════════════════════════════════════
#  SCANNER
# ════════════════════════════════════════════════════════════════

def scan_signals(state):
    if len(state["open_trades"]) >= MAX_POSITIONS:
        return []
    open_tickers = [t["ticker"] for t in state["open_trades"]]
    signals = []

    for ticker in STOCKS:
        if ticker in open_tickers:
            continue
        try:
            df_w  = add_indicators(fetch_ohlcv(ticker, "1wk", days=400))
            df_d  = add_indicators(fetch_ohlcv(ticker, "1d",  days=200))
            df_4h = fetch_ohlcv(ticker, "4h", days=60)

            if len(df_d) < 50 or len(df_w) < 10:
                continue

            bias = detect_weekly_bias(df_w)
            if bias == 0:
                continue

            adx = df_d["ADX"].iloc[-1]
            if pd.isna(adx) or adx < ADX_THRESHOLD:
                continue

            close = df_d["Close"].iloc[-1]
            ob_low,  ob_high  = find_ob_zone(df_d, bias)
            fvg_low, fvg_high = find_fvg(df_d, bias)

            zone_low = zone_high = zone_type = None
            if ob_low and ob_low <= close <= ob_high:
                zone_low, zone_high, zone_type = ob_low, ob_high, "OB"
            elif fvg_low and fvg_low <= close <= fvg_high:
                zone_low, zone_high, zone_type = fvg_low, fvg_high, "FVG"
            if zone_low is None:
                continue

            if not detect_choch(df_4h, bias):
                continue

            sl_price       = zone_low * (1 - SL_BUFFER_PCT)
            risk_per_share = abs(close - sl_price)
            if risk_per_share < 0.5:
                continue

            shares = int(state["capital"] * RISK_PCT / risk_per_share)
            if shares < 1:
                continue
            if shares * close > state["capital"] * 0.9:
                shares = int(state["capital"] * 0.9 / close)
            if shares < 1:
                continue

            signals.append({
                "ticker":         ticker,
                "bias":           bias,
                "entry_price":    round(close, 2),
                "stop_loss":      round(sl_price, 2),
                "target1":        round(close + PARTIAL_R * risk_per_share, 2),
                "target2":        round(close + FULL_R * risk_per_share, 2),
                "risk_per_share": round(risk_per_share, 2),
                "shares":         shares,
                "zone_type":      zone_type,
                "adx":            round(adx, 1),
                "entry_date":     datetime.now(IST).strftime("%Y-%m-%d"),
                "trailing_on":    False,
                "partial_done":   False,
                "realised_pnl":   0,
            })
        except Exception as e:
            print(f"  Scan error {ticker}: {e}")
    return signals

# ════════════════════════════════════════════════════════════════
#  DAILY ENGINE
# ════════════════════════════════════════════════════════════════

def run_daily(state):
    today     = datetime.now(IST).strftime("%Y-%m-%d")
    now_month = datetime.now(IST).strftime("%Y-%m")
    report    = []

    if state["month"] != now_month:
        state["month"]           = now_month
        state["month_start_cap"] = state["capital"]

    month_dd = (state["capital"] - state["month_start_cap"]) / state["month_start_cap"]
    if month_dd <= MAX_MONTHLY_DD:
        report.append("⛔ Monthly DD limit hit. No new trades this month.")
        return state, report

    newly_closed = []
    still_open   = []

    for trade in state["open_trades"]:
        try:
            df    = fetch_ohlcv(trade["ticker"], "1d", days=5)
            if len(df) < 2:
                still_open.append(trade); continue

            high  = float(df["High"].iloc[-1])
            low   = float(df["Low"].iloc[-1])
            close = float(df["Close"].iloc[-1])

            trade = update_trailing_sl(trade, high, low)
            should_exit, exit_price, reason = check_exit(trade, high, low, close)

            if should_exit:
                net = calc_net_pnl(trade["shares"], trade["entry_price"],
                                   exit_price, trade.get("realised_pnl", 0))
                trade.update({"exit_price": exit_price, "exit_date": today,
                              "net_pnl": net, "exit_reason": reason})
                state["capital"] += net
                newly_closed.append(trade)
                state["closed_trades"].append(trade)
            else:
                trade["current_price"] = round(close, 2)
                trade["unrealised"]    = round(
                    trade["shares"] * (close - trade["entry_price"]) +
                    trade.get("realised_pnl", 0), 2)
                still_open.append(trade)
        except Exception as e:
            print(f"  Update error {trade['ticker']}: {e}")
            still_open.append(trade)

    state["open_trades"] = still_open

    new_signals = scan_signals(state)
    for sig in new_signals:
        state["trade_counter"] += 1
        sig["trade_id"] = state["trade_counter"]
        state["open_trades"].append(sig)

    # ── Build Telegram message ──
    ist_time  = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    month_pnl = state["capital"] - state["month_start_cap"]
    month_pct = month_pnl / state["month_start_cap"] * 100
    month_trades = [t for t in state["closed_trades"] if t.get("exit_date","")[:7] == now_month]

    report.append("📊 *SMC DAILY REPORT*")
    report.append(f"🗓 {ist_time}")
    report.append("━━━━━━━━━━━━━━━━━━━━")

    if newly_closed:
        report.append("\n✅ *CLOSED TODAY*")
        for t in newly_closed:
            sign = "+" if t["net_pnl"] >= 0 else ""
            report.append(
                f"  {t['ticker'].replace('.NS','')} | {t['exit_reason']}\n"
                f"  Entry ₹{t['entry_price']} → Exit ₹{t['exit_price']}\n"
                f"  Net: {sign}₹{t['net_pnl']:,.0f}"
            )
    else:
        report.append("\n💤 *NO EXITS TODAY*")

    if new_signals:
        report.append("\n🟡 *NEW SIGNALS*")
        for s in new_signals:
            report.append(
                f"  {s['ticker'].replace('.NS','')} [{s['zone_type']}] "
                f"{'📈 LONG' if s['bias']==1 else '📉 SHORT'}\n"
                f"  Entry ₹{s['entry_price']} | SL ₹{s['stop_loss']}\n"
                f"  T1 ₹{s['target1']} | T2 ₹{s['target2']}\n"
                f"  Shares: {s['shares']} | ADX: {s['adx']}"
            )
    else:
        report.append("\n🔍 *NO NEW SIGNALS TODAY*")

    if state["open_trades"]:
        report.append(f"\n📂 *OPEN POSITIONS ({len(state['open_trades'])})*")
        for t in state["open_trades"]:
            unr   = t.get("unrealised", 0)
            trail = " 🔒trail" if t.get("trailing_on") else ""
            sign  = "+" if unr >= 0 else ""
            report.append(
                f"  {t['ticker'].replace('.NS','')} | "
                f"CMP ₹{t.get('current_price', t['entry_price'])}\n"
                f"  Entry ₹{t['entry_price']} | SL ₹{t['stop_loss']}{trail}\n"
                f"  Unrealised: {sign}₹{unr:,.0f}"
            )
    else:
        report.append("\n📂 *NO OPEN POSITIONS*")

    report.append("\n━━━━━━━━━━━━━━━━━━━━")
    report.append("📈 *MONTH SUMMARY*")
    report.append(f"  Capital:   ₹{state['capital']:,.0f}")
    report.append(f"  Month P&L: {'+' if month_pnl>=0 else ''}₹{month_pnl:,.0f} ({month_pct:+.1f}%)")
    report.append(
        f"  Trades:    {len(month_trades)} | "
        f"W:{sum(1 for t in month_trades if t.get('net_pnl',0)>0)} "
        f"L:{sum(1 for t in month_trades if t.get('net_pnl',0)<=0)}"
    )
    report.append(
        f"  All-time:  ₹{state['start_capital']:,.0f} → ₹{state['capital']:,.0f} "
        f"({(state['capital']-state['start_capital'])/state['start_capital']*100:+.1f}%)"
    )
    status = "🟢 TRADING ACTIVE" if month_dd > -0.03 else ("🟡 CAUTION" if month_dd > MAX_MONTHLY_DD else "🔴 MONTH PAUSED")
    report.append(f"\n{status}")
    report.append("\n_SMC Bot · Simulated · Not financial advice_")

    return state, report

# ════════════════════════════════════════════════════════════════
#  TELEGRAM
# ════════════════════════════════════════════════════════════════

async def send_telegram(message):
    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n" + "="*50)
        print("TELEGRAM NOT SET — printing report:")
        print("="*50)
        print(message)
        return
    try:
        bot    = Bot(token=TELEGRAM_TOKEN)
        chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
        for chunk in chunks:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID,
                                   text=chunk, parse_mode="Markdown")
        print("✅ Telegram message sent!")
    except Exception as e:
        print(f"❌ Telegram error: {e}")
        print(message)

# ════════════════════════════════════════════════════════════════
#  RUNNER  (Colab-safe asyncio)
# ════════════════════════════════════════════════════════════════

def run_and_send():
    print(f"\n[{datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}] Running daily scan...")
    state        = load_state()
    state, lines = run_daily(state)
    save_state(state)
    message      = "\n".join(lines)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_telegram(message))

# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

print("""
╔══════════════════════════════════════════════════╗
║     SMC SWING BOT — GODMODE FINAL VERSION       ║
║     Capital: ₹1,00,000 | NSE 10 Stocks         ║
╚══════════════════════════════════════════════════╝
""")

# Run once immediately
run_and_send()

# Schedule daily at 3:45 PM IST
import schedule
schedule.every().day.at("15:45").do(run_and_send)
print("\n⏰ Scheduler active — runs every day at 3:45 PM IST")
print("   Keep this Colab tab open.\n")

while True:
    schedule.run_pending()
    time.sleep(60)
          
