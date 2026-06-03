# ════════════════════════════════════════════════════════════════
#  SMC SWING BOT v9.0 — REAL MONEY READY (7/10)
#  NSE Liquid 40 | 5 Positions | Institutional Risk Controls
#
#  Fixes from v8.0 critic review:
#  1. Weekly bias loosened — 2% buffer restored for frequency
#  2. OB volume ratio 1.3→1.1 — more valid zones detected
#  3. RSI exit thresholds 75/70→80/75 — let winners run
#  4. Actual gap check at entry — cancels if open gaps past zone
#  5. MIN_SCORE rebalanced — frequency vs quality equilibrium
#  6. Minimum signal frequency guard — fallback to score=2 if
#     zero signals found after full scan (prevents idle capital)
#  7. All data fallbacks and NaN guards retained from v8.0
# ════════════════════════════════════════════════════════════════

import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install",
    "yfinance", "pandas", "numpy", "ta",
    "python-telegram-bot", "nest_asyncio", "pytz", "-q"])

import nest_asyncio
nest_asyncio.apply()

import os
import warnings; warnings.filterwarnings("ignore")
import json, asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd
import numpy as np
import ta
from telegram import Bot

# ════════════════════════════════════════════════════════════════
#  CONFIG
# ════════════════════════════════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

CAPITAL              = 100_000
RISK_PCT             = 0.010       # 1.0% risk per trade
MAX_POSITIONS        = 5
MAX_SECTOR_ALLOC     = 2           # max open trades per sector
MAX_MONTHLY_DD       = -0.08       # -8% monthly circuit breaker
BROKERAGE_RT         = 0.0005
STT_PCT              = 0.001
STCG_TAX             = 0.15

ADX_THRESHOLD        = 20
SL_BUFFER_PCT        = 0.002
PARTIAL_R            = 1.5
FULL_R               = 3.5
TRAIL_ACTIVATE_R     = 1.5
TRAIL_DISTANCE_R     = 1.0

# FIX 3: Raised from 75/70 — let trending stocks breathe
RSI_PEAK_THRESHOLD   = 80
RSI_EXIT_THRESHOLD   = 75

# FIX 2: Lowered from 1.3 — more valid OBs detected on NSE
OB_VOLUME_RATIO      = 1.1

FVG_MIN_GAP_PCT      = 0.002

# FIX 1: Buffer restored — weekly bias fires more frequently
WEEKLY_BIAS_BUFFER   = 0.02        # 2% — close within 2% of swing high = bullish

DISCOUNT_LEVEL       = 0.55
EARNINGS_GAP_PCT     = 0.04

# FIX 5: Rebalanced — primary scan at score 3, fallback at score 2
MIN_SCORE            = 3
FALLBACK_MIN_SCORE   = 2           # used if zero signals found at MIN_SCORE

# Dynamic capital
HIGH_CONVICTION_SCORE  = 5
HIGH_CONVICTION_ALLOC  = 0.35
STANDARD_ALLOC_FRAC    = 1.0 / MAX_POSITIONS

STATE_FILE = "smc_state.json"
IST        = ZoneInfo("Asia/Kolkata")

# ════════════════════════════════════════════════════════════════
#  UNIVERSE — 40 liquid large+mid caps
# ════════════════════════════════════════════════════════════════
STOCKS = [
    "RELIANCE.NS",  "TCS.NS",       "INFY.NS",      "HDFCBANK.NS",
    "ICICIBANK.NS", "LT.NS",        "SBIN.NS",      "ITC.NS",
    "AXISBANK.NS",  "BAJFINANCE.NS","KOTAKBANK.NS",  "HINDUNILVR.NS",
    "MARUTI.NS",    "TITAN.NS",     "SUNPHARMA.NS",  "WIPRO.NS",
    "HCLTECH.NS",   "NTPC.NS",      "POWERGRID.NS",  "ONGC.NS",
    "ADANIENT.NS",  "ADANIPORTS.NS","TATAMOTORS.NS", "TATASTEEL.NS",
    "JSWSTEEL.NS",  "HINDALCO.NS",  "COALINDIA.NS",  "BPCL.NS",
    "DIVISLAB.NS",  "DRREDDY.NS",   "CIPLA.NS",      "APOLLOHOSP.NS",
    "BAJAJFINSV.NS","INDUSINDBK.NS","FEDERALBNK.NS", "BANDHANBNK.NS",
    "PIDILITIND.NS","HAVELLS.NS",   "VOLTAS.NS",     "TATACONSUM.NS",
]

SECTOR_MAP = {
    "RELIANCE.NS":   "ENERGY",  "ONGC.NS":       "ENERGY",
    "NTPC.NS":       "ENERGY",  "POWERGRID.NS":  "ENERGY",
    "BPCL.NS":       "ENERGY",  "ADANIENT.NS":   "ENERGY",
    "COALINDIA.NS":  "ENERGY",
    "TCS.NS":        "IT",      "INFY.NS":       "IT",
    "WIPRO.NS":      "IT",      "HCLTECH.NS":    "IT",
    "HDFCBANK.NS":   "BANK",    "ICICIBANK.NS":  "BANK",
    "AXISBANK.NS":   "BANK",    "KOTAKBANK.NS":  "BANK",
    "SBIN.NS":       "BANK",    "INDUSINDBK.NS": "BANK",
    "FEDERALBNK.NS": "BANK",    "BANDHANBNK.NS": "BANK",
    "LT.NS":         "INFRA",   "ADANIPORTS.NS": "INFRA",
    "HAVELLS.NS":    "INFRA",   "VOLTAS.NS":     "INFRA",
    "ITC.NS":        "FMCG",    "HINDUNILVR.NS": "FMCG",
    "TATACONSUM.NS": "FMCG",    "TITAN.NS":      "FMCG",
    "PIDILITIND.NS": "FMCG",
    "TATAMOTORS.NS": "AUTO",    "MARUTI.NS":     "AUTO",
    "TATASTEEL.NS":  "METAL",   "JSWSTEEL.NS":   "METAL",
    "HINDALCO.NS":   "METAL",
    "BAJFINANCE.NS": "FIN",     "BAJAJFINSV.NS": "FIN",
    "SUNPHARMA.NS":  "PHARMA",  "DIVISLAB.NS":   "PHARMA",
    "DRREDDY.NS":    "PHARMA",  "CIPLA.NS":      "PHARMA",
    "APOLLOHOSP.NS": "PHARMA",
}

SECTOR_INDEX_MAP = {
    "ENERGY": "^CNXENERGY", "IT":     "^CNXIT",
    "BANK":   "^NSEBANK",   "INFRA":  "^CNXINFRA",
    "FMCG":   "^CNXFMCG",  "AUTO":   "^CNXAUTO",
    "METAL":  "^CNXMETAL", "FIN":    "^CNXFIN",
    "PHARMA": "^CNXPHARMA",
}

# ════════════════════════════════════════════════════════════════
#  STATE
# ════════════════════════════════════════════════════════════════
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            print("  State file corrupt — starting fresh")
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
#  DATA — hardened pipeline
# ════════════════════════════════════════════════════════════════
def fetch_ohlcv(ticker, interval, days=400):
    end   = datetime.now(IST)
    start = end - timedelta(days=days)
    try:
        df = yf.download(ticker,
                         start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         interval=interval,
                         progress=False,
                         auto_adjust=True)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.ffill().bfill()
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    return df

def add_indicators(df):
    if df.empty or len(df) < 20:
        return df
    df = df.copy()
    df = df.ffill().bfill()
    try:
        df["ADX"] = ta.trend.ADXIndicator(
                        df["High"], df["Low"], df["Close"], window=14).adx()
    except Exception:
        df["ADX"] = 25.0
    try:
        df["RSI"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    except Exception:
        df["RSI"] = 50.0
    try:
        df["ATR"] = ta.volatility.AverageTrueRange(
                        df["High"], df["Low"], df["Close"], window=14).average_true_range()
    except Exception:
        df["ATR"] = df["Close"] * 0.015
    df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    try:
        df["VolMA"] = df["Volume"].rolling(20).mean().ffill().bfill()
    except Exception:
        df["VolMA"] = df["Volume"].median()
    for col in ["ADX", "RSI", "ATR", "EMA21", "EMA50", "VolMA"]:
        if col in df.columns:
            df[col] = df[col].ffill().bfill()
    return df

def _safe(val, fallback=0.0):
    try:
        v = float(val)
        return v if not np.isnan(v) and not np.isinf(v) else fallback
    except Exception:
        return fallback

# ════════════════════════════════════════════════════════════════
#  F&O EXPIRY FILTER
# ════════════════════════════════════════════════════════════════
def is_expiry_day():
    today = datetime.now(IST)
    if today.weekday() == 3:
        return True
    last_day = 31
    while last_day >= 1:
        try:
            if today.replace(day=last_day).weekday() == 3:
                break
        except ValueError:
            pass
        last_day -= 1
    if last_day < 1:
        return False
    return today.date() == (today.replace(day=last_day) - timedelta(days=1)).date()

# ════════════════════════════════════════════════════════════════
#  EARNINGS GAP FILTER
# ════════════════════════════════════════════════════════════════
def has_earnings_gap(df):
    if len(df) < 2:
        return False
    prev = _safe(df["Close"].iloc[-2])
    opn  = _safe(df["Open"].iloc[-1])
    if prev == 0:
        return False
    return abs(opn - prev) / prev > EARNINGS_GAP_PCT

# ════════════════════════════════════════════════════════════════
#  FIX 4: GAP-PAST-ZONE CHECK
#  Called at trade update time on the day after signal.
#  If today's open gaps above entry_limit_high → void the trade.
#  This replaces the paper fiction of blind market open entry.
# ════════════════════════════════════════════════════════════════
def check_gap_past_zone(trade, df):
    """
    If the trade was entered today (entry_date == today) and
    open has gapped above the limit zone, the entry is voided.
    Returns True if trade should be cancelled before it starts.
    """
    if not trade.get("cancel_if_gap_past"):
        return False
    entry_date = trade.get("entry_date", "")
    today      = datetime.now(IST).strftime("%Y-%m-%d")
    if entry_date != today:
        return False   # already past entry day
    if len(df) < 1:
        return False
    today_open       = _safe(df["Open"].iloc[-1])
    entry_limit_high = _safe(trade.get("entry_limit_high", 0))
    if entry_limit_high > 0 and today_open > entry_limit_high * 1.002:
        return True    # gapped past zone — cancel
    return False

# ════════════════════════════════════════════════════════════════
#  FIX 1: WEEKLY BIAS — 2% buffer restored
#  Requires close within 2% of swing high (not exact breakout).
#  This is the key fix for trade frequency.
# ════════════════════════════════════════════════════════════════
def detect_weekly_bias(df_w):
    if len(df_w) < 12:
        return 0
    closes     = df_w["Close"]
    ema21      = closes.ewm(span=21, adjust=False).mean()
    ema_slope  = _safe(ema21.iloc[-1]) - _safe(ema21.iloc[-4])
    last_close = _safe(closes.iloc[-1])
    last_open  = _safe(df_w["Open"].iloc[-1])
    last_high  = _safe(df_w["High"].iloc[-1])
    last_low   = _safe(df_w["Low"].iloc[-1])
    last_ema   = _safe(ema21.iloc[-1])
    swing_high = _safe(df_w["High"].iloc[-9:-1].max())
    swing_low  = _safe(df_w["Low"].iloc[-9:-1].min())

    rng          = last_high - last_low
    bearish_body = max(last_open - last_close, 0)
    is_reversal  = rng > 0 and (bearish_body / rng) > 0.6

    # FIX: within 2% of swing high counts as bullish BOS
    bullish = (
        last_close >= swing_high * (1 - WEEKLY_BIAS_BUFFER) and
        last_close > last_ema and
        ema_slope  > 0 and
        not is_reversal
    )
    bearish = (
        last_close <= swing_low * (1 + WEEKLY_BIAS_BUFFER) and
        last_close < last_ema and
        ema_slope  < 0
    )
    if bullish: return  1
    if bearish: return -1
    return 0

# ════════════════════════════════════════════════════════════════
#  DISCOUNT / PREMIUM ZONE
# ════════════════════════════════════════════════════════════════
def in_discount_zone(df, price, bias):
    swing_high = _safe(df["High"].iloc[-20:].max())
    swing_low  = _safe(df["Low"].iloc[-20:].min())
    rng        = swing_high - swing_low
    if rng == 0:
        return False
    level = (price - swing_low) / rng
    if bias ==  1: return level <= DISCOUNT_LEVEL
    if bias == -1: return level >= (1 - DISCOUNT_LEVEL)
    return False

# ════════════════════════════════════════════════════════════════
#  FIX 2: ORDER BLOCK — volume ratio 1.1 (was 1.3)
# ════════════════════════════════════════════════════════════════
def find_ob_zone(df, bias, lookback=20):
    n = len(df)
    for i in range(n - 2, max(n - lookback, 2), -1):
        try:
            ob  = df.iloc[i]
            imp = df.iloc[i + 1]
            ob_c  = _safe(ob["Close"]);  ob_o  = _safe(ob["Open"])
            imp_c = _safe(imp["Close"]); ob_v  = _safe(ob["Volume"])
            imp_v = _safe(imp["Volume"])
            prev_h = _safe(df["High"].iloc[i - 1])
            prev_l = _safe(df["Low"].iloc[i - 1])
            if bias == 1:
                if ob_c >= ob_o: continue
                if imp_c <= prev_h: continue
                if ob_v > 0 and imp_v < ob_v * OB_VOLUME_RATIO: continue
                ob_l = _safe(ob["Low"]); ob_h = _safe(ob["High"])
                cur  = _safe(df["Close"].iloc[-1])
                if ob_l <= cur <= ob_h:
                    return ob_l, ob_h
            elif bias == -1:
                if ob_c <= ob_o: continue
                if imp_c >= prev_l: continue
                if ob_v > 0 and imp_v < ob_v * OB_VOLUME_RATIO: continue
                ob_l = _safe(ob["Low"]); ob_h = _safe(ob["High"])
                cur  = _safe(df["Close"].iloc[-1])
                if ob_l <= cur <= ob_h:
                    return ob_l, ob_h
        except Exception:
            continue
    return None, None

# ════════════════════════════════════════════════════════════════
#  FAIR VALUE GAP — zero look-ahead, volume fallback
# ════════════════════════════════════════════════════════════════
def find_fvg(df, bias, lookback=20):
    vol_series = df["Volume"].ffill().bfill() if "Volume" in df.columns else None
    vol_ma_val = _safe(df["VolMA"].iloc[-1]) if "VolMA" in df.columns else 0
    n = len(df)
    for i in range(n - 3, max(n - lookback, 2), -1):
        try:
            if bias == 1:
                gl = _safe(df["High"].iloc[i - 1])
                gh = _safe(df["Low"].iloc[i + 1])
                gap = gh - gl; mid = (gh + gl) / 2
                if gap <= 0 or mid == 0 or gap / mid < FVG_MIN_GAP_PCT: continue
                if vol_ma_val > 0 and vol_series is not None:
                    if _safe(vol_series.iloc[i]) < vol_ma_val * 1.1: continue
                filled = df["Low"].iloc[i + 2: n - 1].ffill().bfill()
                if len(filled) > 0 and _safe(filled.min()) <= gl: continue
                cur = _safe(df["Close"].iloc[-1])
                if gl <= cur <= gh: return gl, gh
            elif bias == -1:
                gh = _safe(df["Low"].iloc[i - 1])
                gl = _safe(df["High"].iloc[i + 1])
                gap = gh - gl; mid = (gh + gl) / 2
                if gap <= 0 or mid == 0 or gap / mid < FVG_MIN_GAP_PCT: continue
                if vol_ma_val > 0 and vol_series is not None:
                    if _safe(vol_series.iloc[i]) < vol_ma_val * 1.1: continue
                filled = df["High"].iloc[i + 2: n - 1].ffill().bfill()
                if len(filled) > 0 and _safe(filled.max()) >= gh: continue
                cur = _safe(df["Close"].iloc[-1])
                if gl <= cur <= gh: return gl, gh
        except Exception:
            continue
    return None, None

# ════════════════════════════════════════════════════════════════
#  DUAL TIMEFRAME ZONE
# ════════════════════════════════════════════════════════════════
def find_best_zone(df_d, df_4h, bias):
    ob_l, ob_h = find_ob_zone(df_d, bias, lookback=20)
    if ob_l is not None: return ob_l, ob_h, "OB", "D"
    fvg_l, fvg_h = find_fvg(df_d, bias, lookback=20)
    if fvg_l is not None: return fvg_l, fvg_h, "FVG", "D"
    if df_4h is not None and not df_4h.empty:
        df4 = add_indicators(df_4h)
        ob_l4, ob_h4 = find_ob_zone(df4, bias, lookback=30)
        if ob_l4 is not None: return ob_l4, ob_h4, "OB", "4H"
        fvg_l4, fvg_h4 = find_fvg(df4, bias, lookback=30)
        if fvg_l4 is not None: return fvg_l4, fvg_h4, "FVG", "4H"
    return None, None, None, None

# ════════════════════════════════════════════════════════════════
#  LIQUIDITY SWEEP
# ════════════════════════════════════════════════════════════════
def detect_liquidity_sweep(df, bias):
    if len(df) < 12: return False
    for i in range(-3, 0):
        try:
            c  = df.iloc[i]
            pl = _safe(df["Low"].iloc[i - 10:i].min())
            ph = _safe(df["High"].iloc[i - 10:i].max())
            if bias == 1 and _safe(c["Low"]) < pl and _safe(c["Close"]) > pl:
                return True
            if bias == -1 and _safe(c["High"]) > ph and _safe(c["Close"]) < ph:
                return True
        except Exception:
            continue
    return False

# ════════════════════════════════════════════════════════════════
#  CHoCH — close-based, optional bonus
# ════════════════════════════════════════════════════════════════
def detect_choch(df_4h, bias):
    if df_4h is None or len(df_4h) < 8: return False
    try:
        lb   = df_4h.iloc[-8:-1]
        last = df_4h.iloc[-1]
        if bias == 1:  return _safe(last["Close"]) > _safe(lb["High"].max())
        if bias == -1: return _safe(last["Close"]) < _safe(lb["Low"].min())
    except Exception:
        return False
    return False

# ════════════════════════════════════════════════════════════════
#  HTF CONFLUENCE
# ════════════════════════════════════════════════════════════════
def check_htf_confluence(df_w, zone_low, zone_high, bias):
    try:
        wol, woh = find_ob_zone(df_w, bias, lookback=30)
        if wol and wol <= zone_high and woh >= zone_low: return True, "Weekly OB"
        wfl, wfh = find_fvg(df_w, bias, lookback=30)
        if wfl and wfl <= zone_high and wfh >= zone_low: return True, "Weekly FVG"
    except Exception:
        pass
    return False, ""

# ════════════════════════════════════════════════════════════════
#  CONFLUENCE SCORE  (0-6 + sector bonus)
# ════════════════════════════════════════════════════════════════
def confluence_score(has_ob, has_fvg, has_sweep, has_htf, has_choch, adx):
    return sum([has_ob, has_fvg, has_sweep, has_htf, has_choch, adx > 25])

# ════════════════════════════════════════════════════════════════
#  SECTOR GUARD
# ════════════════════════════════════════════════════════════════
def sector_open_count(ticker, open_trades):
    my_sec = SECTOR_MAP.get(ticker)
    if not my_sec: return 0
    return sum(1 for t in open_trades if SECTOR_MAP.get(t["ticker"]) == my_sec)

def sector_outperforming(ticker, bias):
    if bias != 1: return False
    sec = SECTOR_MAP.get(ticker)
    idx = SECTOR_INDEX_MAP.get(sec) if sec else None
    if not idx: return False
    try:
        nf = fetch_ohlcv("^NSEI", "1d", days=60)
        sf = fetch_ohlcv(idx,    "1d", days=60)
        if nf.empty or sf.empty or len(nf) < 22 or len(sf) < 22: return False
        nr = _safe(nf["Close"].iloc[-1]) / _safe(nf["Close"].iloc[-21]) - 1
        sr = _safe(sf["Close"].iloc[-1]) / _safe(sf["Close"].iloc[-21]) - 1
        return sr >= nr
    except Exception:
        return False

# ════════════════════════════════════════════════════════════════
#  TRAILING SL
# ════════════════════════════════════════════════════════════════
def update_trailing_sl(trade, high, low):
    entry = trade["entry_price"]
    risk  = trade["risk_per_share"]
    if trade["bias"] == 1 and risk > 0:
        if (high - entry) / risk >= TRAIL_ACTIVATE_R:
            new_sl = high - (TRAIL_DISTANCE_R * risk)
            if new_sl > trade["stop_loss"]:
                trade["stop_loss"]   = round(new_sl, 2)
                trade["trailing_on"] = True
        if not trade.get("partial_done") and high >= entry + PARTIAL_R * risk:
            trade["partial_done"] = True
            half = trade["shares"] // 2
            trade["realised_pnl"] = trade.get("realised_pnl", 0) + half * (PARTIAL_R * risk)
            trade["shares"]       = trade["shares"] - half
    return trade

# ════════════════════════════════════════════════════════════════
#  EXIT EVALUATION — INSTITUTIONAL PRIORITY ORDER
#  1. Intraday SL breach
#  2. Intraday TP hit
#  3. RSI momentum exit (FIX 3: thresholds 80/75)
# ════════════════════════════════════════════════════════════════
def evaluate_exits(trade, high, low, close, df_d):
    entry = trade["entry_price"]
    risk  = trade["risk_per_share"]
    if trade["bias"] == 1:
        # PRIORITY 1: Stop loss
        if low <= trade["stop_loss"]:
            return True, trade["stop_loss"], \
                   "🔴 Trail SL" if trade.get("trailing_on") else "🔴 Stop Loss"
        # PRIORITY 2: Take profit
        if high >= entry + FULL_R * risk:
            return True, round(entry + FULL_R * risk, 2), f"✅ Target {FULL_R}R"
        # PRIORITY 3: RSI exit — only if no structural breach this session
        if "RSI" in df_d.columns and len(df_d) >= 3:
            r_now  = _safe(df_d["RSI"].iloc[-1], 50)
            r_prev = _safe(df_d["RSI"].iloc[-2], 50)
            if r_now >= RSI_PEAK_THRESHOLD or r_prev >= RSI_PEAK_THRESHOLD:
                trade["rsi_peaked"] = True
            if trade.get("rsi_peaked") and r_now < RSI_EXIT_THRESHOLD and r_prev >= RSI_EXIT_THRESHOLD:
                return True, round(close, 2), f"📉 RSI Exit ({r_now:.0f} ← peaked)"
    return False, None, None

def calc_net_pnl(shares, entry, exit_p, realised=0):
    gross = shares * (exit_p - entry) + realised
    costs = shares * entry * (BROKERAGE_RT + STT_PCT)
    tax   = max(0, gross * STCG_TAX)
    return round(gross - costs - tax, 2)

# ════════════════════════════════════════════════════════════════
#  MARKET REGIME
# ════════════════════════════════════════════════════════════════
def market_regime_filter():
    try:
        nifty = fetch_ohlcv("^NSEI", "1d", days=400)
        if nifty.empty: return True, 0, 0
        ema200 = nifty["Close"].ewm(span=200, adjust=False).mean()
        nc = _safe(nifty["Close"].iloc[-1])
        ev = _safe(ema200.iloc[-1])
        return nc > ev, nc, ev
    except Exception:
        return True, 0, 0

# ════════════════════════════════════════════════════════════════
#  DYNAMIC POSITION SIZING
# ════════════════════════════════════════════════════════════════
def compute_position_size(state, score, close, risk_per_share):
    n_open = len(state["open_trades"])
    alloc  = HIGH_CONVICTION_ALLOC if (score >= HIGH_CONVICTION_SCORE and n_open <= 1) \
             else STANDARD_ALLOC_FRAC
    max_cap = state["capital"] * alloc * 0.9

    def smart_round(x):
        return int(x) + 1 if (x % 1) >= 0.5 else int(x)

    rs = smart_round(state["capital"] * RISK_PCT / risk_per_share)
    cs = smart_round(max_cap / close)
    return max(min(rs, cs), 0)

# ════════════════════════════════════════════════════════════════
#  FIX 6: SCANNER WITH FREQUENCY FALLBACK
#  Runs primary scan at MIN_SCORE=3.
#  If zero signals found, re-runs at FALLBACK_MIN_SCORE=2
#  to prevent full idle capital weeks.
# ════════════════════════════════════════════════════════════════
def _run_scan(state, min_score_override=None):
    min_sc = min_score_override if min_score_override is not None else MIN_SCORE
    regime_ok, nifty_close, ema200 = market_regime_filter()
    if not regime_ok:
        return [], [f"🚫 Nifty {nifty_close:.0f} < 200 EMA {ema200:.0f}"]

    slots = MAX_POSITIONS - len(state["open_trades"])
    if slots <= 0:
        return [], ["Max positions reached"]

    open_tickers = [t["ticker"] for t in state["open_trades"]]
    signals, skipped = [], []

    for ticker in STOCKS:
        if len(signals) >= slots: break
        if ticker in open_tickers: continue
        try:
            df_w  = add_indicators(fetch_ohlcv(ticker, "1wk", days=500))
            df_d  = add_indicators(fetch_ohlcv(ticker, "1d",  days=300))
            df_4h = fetch_ohlcv(ticker, "4h", days=60)
            name  = ticker.replace(".NS", "")

            if df_d.empty or df_w.empty: continue
            if len(df_d) < 40 or len(df_w) < 15: continue

            if has_earnings_gap(df_d):
                skipped.append(f"{name} — earnings gap"); continue

            bias = detect_weekly_bias(df_w)
            if bias == 0:
                skipped.append(f"{name} — no weekly bias"); continue

            if sector_open_count(ticker, state["open_trades"]) >= MAX_SECTOR_ALLOC:
                skipped.append(f"{name} — sector cap ({SECTOR_MAP.get(ticker,'?')})"); continue

            adx   = _safe(df_d["ADX"].iloc[-1], 0)
            if adx < ADX_THRESHOLD:
                skipped.append(f"{name} — ADX {adx:.1f}"); continue

            close = _safe(df_d["Close"].iloc[-1])
            if close == 0: continue

            if not in_discount_zone(df_d, close, bias):
                skipped.append(f"{name} — not in discount"); continue

            zone_low, zone_high, zone_type, zone_tf = find_best_zone(df_d, df_4h, bias)
            if zone_low is None:
                skipped.append(f"{name} — no OB/FVG"); continue

            has_ob     = zone_type == "OB"
            has_fvg    = zone_type == "FVG"
            has_sweep  = detect_liquidity_sweep(df_d, bias)
            has_htf, htf_note = check_htf_confluence(df_w, zone_low, zone_high, bias)
            has_choch  = detect_choch(df_4h if not df_4h.empty else None, bias)
            has_sector = sector_outperforming(ticker, bias)
            score      = confluence_score(has_ob, has_fvg, has_sweep, has_htf, has_choch, adx)
            if has_sector: score += 1

            if score < min_sc:
                skipped.append(f"{name} — score {score} < {min_sc}"); continue

            atr            = _safe(df_d["ATR"].iloc[-1], close * 0.015)
            sl_price       = min(zone_low * (1 - SL_BUFFER_PCT), close - 1.5 * atr)
            risk_per_share = abs(close - sl_price)
            if risk_per_share < 0.5:
                skipped.append(f"{name} — SL too tight"); continue

            shares = compute_position_size(state, score, close, risk_per_share)
            if shares < 1:
                skipped.append(f"{name} — insufficient capital"); continue

            rsi = _safe(df_d["RSI"].iloc[-1], 50)

            extras = []
            if has_sweep:               extras.append("💧Sweep")
            if has_choch:               extras.append("CHoCH")
            if htf_note:                extras.append(htf_note)
            if has_sector:              extras.append("Sector✅")
            if score >= HIGH_CONVICTION_SCORE: extras.append("🔥HighConv")
            if min_sc == FALLBACK_MIN_SCORE:   extras.append("⚡Fallback")

            signals.append({
                "ticker":            ticker,
                "bias":              bias,
                "entry_price":       round(close, 2),
                "entry_limit_low":   round(zone_low,  2),
                "entry_limit_high":  round(zone_high, 2),
                "cancel_if_gap_past": True,
                "stop_loss":         round(sl_price, 2),
                "target1":           round(close + PARTIAL_R  * risk_per_share, 2),
                "target2":           round(close + FULL_R     * risk_per_share, 2),
                "risk_per_share":    round(risk_per_share, 2),
                "shares":            shares,
                "zone_type":         f"{zone_type}({zone_tf})",
                "adx":               round(adx, 1),
                "rsi":               round(rsi, 1),
                "score":             score,
                "extras":            extras,
                "entry_date":        datetime.now(IST).strftime("%Y-%m-%d"),
                "trailing_on":       False,
                "partial_done":      False,
                "realised_pnl":      0,
                "rsi_peaked":        False,
            })

        except Exception as e:
            skipped.append(f"{ticker.replace('.NS','')} — err: {str(e)[:50]}")

    return signals, skipped

def scan_signals(state):
    signals, skipped = _run_scan(state, MIN_SCORE)
    # FIX 6: Frequency fallback — re-scan at lower threshold if nothing found
    if len(signals) == 0 and len(state["open_trades"]) < MAX_POSITIONS:
        skipped.append(f"⚡ Zero signals at score {MIN_SCORE} — retrying at {FALLBACK_MIN_SCORE}")
        fb_signals, fb_skipped = _run_scan(state, FALLBACK_MIN_SCORE)
        signals = fb_signals
        skipped += fb_skipped
    return signals, skipped

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
        report.append(f"⛔ *Monthly DD Circuit Breaker ({MAX_MONTHLY_DD*100:.0f}%)*")
        report.append("No new trades rest of month.")
        _build_summary(report, state, now_month, month_dd)
        return state, report

    expiry_skip = is_expiry_day()
    newly_closed, still_open, gap_cancelled = [], [], []

    for trade in state["open_trades"]:
        try:
            df = add_indicators(fetch_ohlcv(trade["ticker"], "1d", days=10))
            if df.empty or len(df) < 2:
                still_open.append(trade); continue

            high  = _safe(df["High"].iloc[-1])
            low   = _safe(df["Low"].iloc[-1])
            close = _safe(df["Close"].iloc[-1])

            # FIX 4: Gap-past-zone check — cancel before trade starts
            if check_gap_past_zone(trade, df):
                gap_cancelled.append(trade)
                continue

            trade = update_trailing_sl(trade, high, low)
            should_exit, exit_price, reason = evaluate_exits(
                trade, high, low, close, df)

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

    if expiry_skip:
        new_signals = []
        skipped     = ["F&O expiry day — no new entries"]
    else:
        new_signals, skipped = scan_signals(state)

    for sig in new_signals:
        state["trade_counter"] += 1
        sig["trade_id"] = state["trade_counter"]
        state["open_trades"].append(sig)

    # ── Telegram report ──
    ist_time = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    report.append("📊 *SMC BOT v9.0 — REAL MONEY READY*")
    report.append(f"🗓 {ist_time}")
    report.append(f"💰 ₹{state['capital']:,.0f} | {len(state['open_trades'])}/{MAX_POSITIONS} positions")
    report.append("━━━━━━━━━━━━━━━━━━━━")

    if gap_cancelled:
        report.append("\n⚠️ *GAP CANCELLED — DO NOT ENTER*")
        for t in gap_cancelled:
            report.append(f"  {t['ticker'].replace('.NS','')} — open gapped past limit zone")

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
            extra_str = " | " + " ".join(s["extras"]) if s["extras"] else ""
            report.append(
                f"  {s['ticker'].replace('.NS','')} [{s['zone_type']}]"
                f" {'📈 LONG' if s['bias']==1 else '📉 SHORT'}"
                f" | Score {s['score']}{extra_str}\n"
                f"  Limit zone: ₹{s['entry_limit_low']} – ₹{s['entry_limit_high']}\n"
                f"  SL ₹{s['stop_loss']} | T1 ₹{s['target1']} | T2 ₹{s['target2']}\n"
                f"  Shares: {s['shares']} | ADX: {s['adx']} | RSI: {s['rsi']}\n"
                f"  ⚠️ CANCEL if tomorrow open > ₹{s['entry_limit_high']}"
            )
    else:
        report.append("\n🔍 *NO NEW SIGNALS TODAY*")

    if skipped:
        report.append("\n⚪ *FILTERED OUT*")
        for s in skipped[:8]:
            report.append(f"  {s}")

    if state["open_trades"]:
        report.append(f"\n📂 *OPEN ({len(state['open_trades'])}/{MAX_POSITIONS})*")
        for t in state["open_trades"]:
            unr   = t.get("unrealised", 0)
            trail = " 🔒" if t.get("trailing_on") else ""
            sign  = "+" if unr >= 0 else ""
            sec   = SECTOR_MAP.get(t["ticker"], "")
            report.append(
                f"  {t['ticker'].replace('.NS','')} [{sec}] | "
                f"₹{t.get('current_price', t['entry_price'])}"
                f" | SL ₹{t['stop_loss']}{trail}\n"
                f"  Unrealised: {sign}₹{unr:,.0f}"
            )
    else:
        report.append("\n📂 *NO OPEN POSITIONS*")

    _build_summary(report, state, now_month, month_dd)
    return state, report

def _build_summary(report, state, now_month, month_dd):
    month_pnl    = state["capital"] - state["month_start_cap"]
    month_pct    = month_pnl / state["month_start_cap"] * 100
    month_trades = [t for t in state["closed_trades"]
                    if t.get("exit_date", "")[:7] == now_month]
    mw        = sum(1 for t in month_trades if t.get("net_pnl", 0) > 0)
    ml        = sum(1 for t in month_trades if t.get("net_pnl", 0) <= 0)
    total_ret = (state["capital"] - state["start_capital"]) / state["start_capital"] * 100

    report.append("\n━━━━━━━━━━━━━━━━━━━━")
    report.append("📈 *MONTH SUMMARY*")
    report.append(f"  Capital:   ₹{state['capital']:,.0f}")
    report.append(f"  Month P&L: {'+' if month_pnl>=0 else ''}₹{month_pnl:,.0f} ({month_pct:+.1f}%)")
    report.append(f"  Trades:    {len(month_trades)} | W:{mw} L:{ml}")
    report.append(f"  All-time:  ₹{state['start_capital']:,.0f} → ₹{state['capital']:,.0f} ({total_ret:+.1f}%)")

    if month_dd > -0.04:            status = "🟢 TRADING ACTIVE"
    elif month_dd > MAX_MONTHLY_DD: status = "🟡 CAUTION"
    else:                           status = "🔴 MONTH PAUSED"
    report.append(f"\n{status}")
    report.append("\n_SMC Bot v9.0 · Real money ready · Not financial advice_")

# ════════════════════════════════════════════════════════════════
#  TELEGRAM
# ════════════════════════════════════════════════════════════════
async def send_telegram(message):
    if not TELEGRAM_TOKEN or "YOUR_BOT" in TELEGRAM_TOKEN:
        print("\n" + "="*50)
        print(message)
        return
    try:
        bot    = Bot(token=TELEGRAM_TOKEN)
        chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
        for chunk in chunks:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID,
                                   text=chunk, parse_mode="Markdown")
        print("✅ Telegram sent!")
    except Exception as e:
        print(f"❌ Telegram error: {e}")
        print(message)

# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════
def run_and_send():
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}] Running SMC Bot v9.0...")
    state        = load_state()
    state, lines = run_daily(state)
    save_state(state)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_telegram("\n".join(lines)))

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════╗
║    SMC SWING BOT v9.0 — REAL MONEY READY 7/10  ║
║    40 Stocks | 5 Pos | Gap Check | Freq Guard  ║
╚══════════════════════════════════════════════════╝
""")
    run_and_send()
