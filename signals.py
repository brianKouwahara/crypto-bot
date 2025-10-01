import numpy as np
import pandas as pd
from typing import Tuple
from config import SHORT_TF_CONF, LONG_TF_CONF, VOL_LOOKBACK
from utils import minutes_between

# --- Indicateurs ---
def compute_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c1 = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([(h - l).abs(), (h - c1).abs(), (l - c1).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return atr.bfill().ffill()

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.bfill().ffill()

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([(h - l).abs(), (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return float(atr.iloc[-1])

def compute_supertrend(df: pd.DataFrame, atr_period: int = 14, mult: float = 3.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    atr = compute_atr_series(df, atr_period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    st = pd.Series(index=df.index, dtype=float)
    dirn = pd.Series(index=df.index, dtype=int)
    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = upper.iloc[i]
            dirn.iloc[i] = 1 if df["close"].iloc[i] >= st.iloc[i] else -1
            continue
        upper_i = max(upper.iloc[i], st.iloc[i-1]) if dirn.iloc[i-1] == 1 else upper.iloc[i]
        lower_i = min(lower.iloc[i], st.iloc[i-1]) if dirn.iloc[i-1] == -1 else lower.iloc[i]
        if dirn.iloc[i-1] == 1:
            st_i = lower_i if df["close"].iloc[i] < lower_i else max(lower_i, st.iloc[i-1])
        else:
            st_i = upper_i if df["close"].iloc[i] > upper_i else min(upper_i, st.iloc[i-1])
        dirn.iloc[i] = 1 if df["close"].iloc[i] >= st_i else -1
        st.iloc[i] = st_i
    return st.bfill().ffill(), upper.bfill().ffill(), lower.bfill().ffill()

def avg_dollar_volume(df: pd.DataFrame, lookback: int) -> float:
    sub = df.tail(max(lookback, 1))
    return float((sub["close"] * sub["vol"]).mean())

# --- Signal Hybride (reprend ta V2) ---
def hybrid_signal(df: pd.DataFrame, tf: str, conf: dict, signal_mode: str = "closed"):
    rsi = compute_rsi(df["close"], period=conf["rsi"]["period"]).clip(0, 100)
    rsi_avg = rsi.ewm(span=conf["rsi"]["smooth"], adjust=False).mean()
    idx = -2 if signal_mode == "closed" else -1
    rsi_last, rsi_avg_last = float(rsi.iloc[idx]), float(rsi_avg.iloc[idx])

    st_line, _, _ = compute_supertrend(df, conf["supertrend"]["atr_period"], conf["supertrend"]["mult"])
    st_trend = "bull" if df["close"].iloc[idx] >= st_line.iloc[idx] else "bear"

    don_len = conf["donchian"]["length"]
    don_high = df["high"].rolling(don_len, min_periods=don_len).max()
    don_low  = df["low"].rolling(don_len,  min_periods=don_len).min()
    don_high_last = None if np.isnan(don_high.iloc[idx]) else float(don_high.iloc[idx])
    don_low_last  = None if np.isnan(don_low.iloc[idx])  else float(don_low.iloc[idx])

    v_look = conf["volume"]["lookback"]
    avg_vol_usd = avg_dollar_volume(df, v_look)
    cur_vol_usd = float(df["close"].iloc[-1] * df["vol"].iloc[-1])
    vol_ok = (avg_vol_usd > 0) and (cur_vol_usd > avg_vol_usd * conf["volume"]["mult"]) and (cur_vol_usd > conf["volume"]["min_abs"])

    last_close = float(df["close"].iloc[idx])
    buy_cond = (rsi_last > rsi_avg_last) and (st_trend == "bull") and (don_high_last is not None and last_close > don_high_last) and vol_ok
    sell_cond = (rsi_last < rsi_avg_last) and (st_trend == "bear") and (don_low_last is not None and last_close < don_low_last)
    action = "buy" if buy_cond else ("sell" if sell_cond else None)

    return rsi_last, rsi_avg_last, st_trend, don_high_last, don_low_last, bool(vol_ok), action

def pick_conf_for_tf(tf: str):
    return SHORT_TF_CONF if tf in ["1m","2m","5m","15m"] else LONG_TF_CONF
