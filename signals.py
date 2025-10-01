# signals.py
# -*- coding: utf-8 -*-
from typing import Tuple
import numpy as np
import pandas as pd

from config import SHORT_TF_CONF, LONG_TF_CONF

# ---------- Indicateurs ----------
def compute_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR (EMA) série complète."""
    if len(df) < max(2, period + 1):
        return pd.Series([0.0] * len(df), index=df.index)
    h, l, c1 = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([(h - l).abs(), (h - c1).abs(), (l - c1).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return atr.fillna(method="bfill").fillna(method="ffill")

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI classique (EMA des gains/pertes). Renvoie une série bornée [0,100]."""
    if len(close) < max(2, period + 1):
        return pd.Series([50.0] * len(close), index=close.index)  # neutre si historique trop court
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.clip(0, 100).fillna(method="bfill").fillna(method="ffill")

def smooth_rsi(rsi: pd.Series, avg_type: str, avg_period: int) -> pd.Series:
    """Lissage du RSI par SMA ou EMA selon avg_type."""
    avg_type = (avg_type or "ema").lower()
    if avg_type == "sma":
        return rsi.rolling(window=avg_period, min_periods=avg_period).mean().fillna(method="bfill").fillna(method="ffill")
    # défaut = EMA
    return rsi.ewm(span=avg_period, adjust=False).mean().fillna(method="bfill").fillna(method="ffill")

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Dernier ATR (EMA) pour sizing risque."""
    if len(df) < max(2, period + 1):
        return 0.0
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([(h - l).abs(), (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 0.0

def compute_supertrend(df: pd.DataFrame, atr_period: int = 14, mult: float = 3.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Retourne (ligne ST, upper, lower)."""
    atr = compute_atr_series(df, atr_period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr

    st = pd.Series(index=df.index, dtype=float)
    dirn = pd.Series(index=df.index, dtype=int)  # 1 bull, -1 bear

    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = upper.iloc[i]
            dirn.iloc[i] = 1 if df["close"].iloc[i] >= st.iloc[i] else -1
            continue

        up_i = max(upper.iloc[i], st.iloc[i-1]) if dirn.iloc[i-1] == 1 else upper.iloc[i]
        lo_i = min(lower.iloc[i], st.iloc[i-1]) if dirn.iloc[i-1] == -1 else lower.iloc[i]

        if dirn.iloc[i-1] == 1:
            st_i = lo_i if df["close"].iloc[i] < lo_i else max(lo_i, st.iloc[i-1])
        else:
            st_i = up_i if df["close"].iloc[i] > up_i else min(up_i, st.iloc[i-1])

        dirn.iloc[i] = 1 if df["close"].iloc[i] >= st_i else -1
        st.iloc[i] = st_i

    return (
        st.fillna(method="bfill").fillna(method="ffill"),
        upper.fillna(method="bfill").fillna(method="ffill"),
        lower.fillna(method="bfill").fillna(method="ffill"),
    )

def avg_dollar_volume(df: pd.DataFrame, lookback: int) -> float:
    """Moyenne (close*vol) sur la fenêtre lookback."""
    if len(df) == 0:
        return 0.0
    sub = df.tail(max(lookback, 1))
    val = (sub["close"] * sub["vol"]).mean()
    try:
        return float(val)
    except Exception:
        return 0.0

# ---------- Signal Hybride ----------
def hybrid_signal(
    df: pd.DataFrame,
    tf: str,
    conf: dict,
    signal_mode: str = "closed",
    *,
    avg_type: str = None,     # 'ema' | 'sma' (si None => conf par défaut)
    avg_period: int = None,   # si None => conf["rsi"]["smooth"]
    rsi_period: int = None    # si None => conf["rsi"]["period"]
):
    """
    Retourne: (rsi_last, rsi_avg_last, st_trend, don_high_last, don_low_last, vol_ok, action)
    action ∈ {"buy", "sell", None}
    """
    # Périodes finales (fallback sur conf)
    rsi_per   = int(rsi_period or conf["rsi"]["period"])
    smooth_per = int(avg_period or conf["rsi"]["smooth"])
    avg_kind   = (avg_type or "ema").lower()

    # RSI + lissage SMA/EMA choisi
    rsi = compute_rsi(df["close"], period=rsi_per)
    rsi_avg = smooth_rsi(rsi, avg_kind, smooth_per)

    idx = -2 if signal_mode == "closed" else -1
    if abs(idx) > len(df):
        idx = -1  # fallback sécurité

    rsi_last, rsi_avg_last = float(rsi.iloc[idx]), float(rsi_avg.iloc[idx])

    # Supertrend
    st_line, _, _ = compute_supertrend(df, conf["supertrend"]["atr_period"], conf["supertrend"]["mult"])
    st_trend = "bull" if df["close"].iloc[idx] >= st_line.iloc[idx] else "bear"

    # Donchian
    don_len = int(conf["donchian"]["length"])
    if len(df) < max(2, don_len):
        don_high_last = don_low_last = None
    else:
        don_high = df["high"].rolling(don_len, min_periods=don_len).max()
        don_low  = df["low"].rolling(don_len,  min_periods=don_len).min()
        don_high_last = None if np.isnan(don_high.iloc[idx]) else float(don_high.iloc[idx])
        don_low_last  = None if np.isnan(don_low.iloc[idx])  else float(don_low.iloc[idx])

    # Volume en $
    v_look = int(conf["volume"]["lookback"])
    avg_vol_usd = avg_dollar_volume(df, v_look)
    cur_vol_usd = float(df["close"].iloc[-1] * df["vol"].iloc[-1]) if len(df) else 0.0
    vol_ok = (avg_vol_usd > 0) and \
             (cur_vol_usd > avg_vol_usd * conf["volume"]["mult"]) and \
             (cur_vol_usd > conf["volume"]["min_abs"])

    # ---- Règles ----
    last_close = float(df["close"].iloc[idx])

    # Donchian : cassure obligatoire ou non selon la conf
    require_breakout = bool(conf.get("donchian", {}).get("require_breakout", True))
    if don_high_last is None:
        don_ok = True
    else:
        don_ok = (last_close > don_high_last) if require_breakout else True

    buy_cond  = (rsi_last > rsi_avg_last) and (st_trend == "bull") and don_ok and vol_ok
    sell_cond = (rsi_last < rsi_avg_last) and (st_trend == "bear") and \
                (don_low_last is not None and last_close < don_low_last)

    action = "buy" if buy_cond else ("sell" if sell_cond else None)
    return rsi_last, rsi_avg_last, st_trend, don_high_last, don_low_last, bool(vol_ok), action

def pick_conf_for_tf(tf: str):
    """Profil d’indicateurs selon TF (court vs long)."""
    return SHORT_TF_CONF if tf in ["1m", "2m", "5m", "15m"] else LONG_TF_CONF
