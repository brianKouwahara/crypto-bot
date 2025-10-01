import os

# ----------- Fichiers / Fees -----------
STATE_FILE = os.getenv("STATE_FILE", "state.json")
FEE_TAKER_PCT = float(os.getenv("FEE_TAKER_PCT", "0.001"))  # 0.1%

# ----------- Cooldowns par TF (s) -----------
COOLDOWN = {
    "1m":  int(os.getenv("COOLDOWN_1M", "10")),
    "2m":  int(os.getenv("COOLDOWN_2M", "15")),
    "5m":  int(os.getenv("COOLDOWN_5M", "30")),
    "15m": int(os.getenv("COOLDOWN_15M", "30")),
    "30m": int(os.getenv("COOLDOWN_30M", "45")),
    "1h":  int(os.getenv("COOLDOWN_1H", "60")),
    "2h":  int(os.getenv("COOLDOWN_2H", "90")),
    "4h":  int(os.getenv("COOLDOWN_4H", "0")),
    "1d":  int(os.getenv("COOLDOWN_1D", "0")),
    "1w":  int(os.getenv("COOLDOWN_1W", "0")),
}

# ----------- Slippage SELL optionnel -----------
_sell_slip = os.getenv("SELL_SLIP_PCT", "").strip()
try:
    SELL_SLIP_PCT = float(_sell_slip) if _sell_slip else None
except Exception:
    SELL_SLIP_PCT = None

# ----------- Risk sizing / volume / CB -----------
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0"))  # si tu veux risk par % balance
ATR_LOOKBACK       = int(os.getenv("ATR_LOOKBACK", "14"))
ATR_MULT_SL        = float(os.getenv("ATR_MULT_SL", "1.5"))

# Volume universel (pas besoin de token spécifique)
VOL_LOOKBACK       = int(os.getenv("VOL_LOOKBACK", "20"))
MIN_AVG_DOLLAR_VOL = float(os.getenv("MIN_AVG_DOLLAR_VOL", "0"))  # legacy, plus utilisé directement

# Capital buffer / circuit breaker
CB_SYMBOL       = os.getenv("CB_SYMBOL", "BTC/USDT")
CB_TF           = os.getenv("CB_TF", "5m")
CB_WINDOW_MIN   = int(os.getenv("CB_WINDOW_MIN", "15"))
CB_DROP_PCT     = float(os.getenv("CB_DROP_PCT", "3"))
CB_COOLDOWN_MIN = int(os.getenv("CB_COOLDOWN_MIN", "30"))

MAX_BUYS_PER_24H = int(os.getenv("MAX_BUYS_PER_24H", "0"))
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "").strip()

# ----------- Profils indicateurs (universels) -----------
# Petits TF → Donchian obligatoire + volume mini plus bas
SHORT_TF_CONF = {
    "supertrend": {"atr_period": 7, "mult": 2.0},
    "donchian":   {"length": 20, "require_breakout": True},   # cassure OBLIGATOIRE
    "rsi":        {"period": 7, "smooth": 7},
    "volume":     {"lookback": 20, "mult": 1.2, "min_abs": 75_000},  # cur_vol > 1.2*avg ET > 75k$
}
# Longs TF → Donchian optionnel + volume mini plus haut
LONG_TF_CONF = {
    "supertrend": {"atr_period": 14, "mult": 3.0},
    "donchian":   {"length": 55, "require_breakout": False},  # cassure NON obligatoire
    "rsi":        {"period": 14, "smooth": 21},
    "volume":     {"lookback": 50, "mult": 1.0, "min_abs": 150_000},  # cur_vol > avg ET > 150k$
}

# ----------- Watchdog -----------
HEARTBEAT_FILE         = os.getenv("HEARTBEAT_FILE", "/tmp/bot_heartbeat.txt")
HEARTBEAT_INTERVAL_SEC = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "30"))
MAX_STALE_SEC_ENV      = os.getenv("MAX_STALE_SEC", "").strip()
# -> MAX_STALE_SEC sera recalculé dynamiquement dans bot.py selon min TF

# ----------- Hystérésis / SL/TP / Stale par TF -----------
HYST_EPS_DEFAULT = 2.0
HYST_EPS_BY_TF   = {"1m":0.8,"2m":0.8,"5m":1.0,"15m":1.5,"30m":1.5,"1h":2.0,"2h":2.5,"4h":3.0,"1d":3.0,"1w":4.0}

STOP_LOSS_PCT_FALLBACK = float(os.getenv("STOP_LOSS_PCT", "0.025"))
TP_TRIGGER_FALLBACK    = float(os.getenv("TP_TRIGGER", "0.05"))
TP_TRAIL_FALLBACK      = float(os.getenv("TP_TRAIL", "0.02"))

STOP_LOSS_BY_TF  = {"1m":0.015,"2m":0.018,"5m":0.020,"15m":0.030,"30m":0.040,"1h":0.050,"2h":0.055,"4h":0.060,"1d":0.090,"1w":0.150}
TP_TRIGGER_BY_TF = {"1m":0.030,"2m":0.035,"5m":0.040,"15m":0.050,"30m":0.060,"1h":0.060,"2h":0.070,"4h":0.080,"1d":0.100,"1w":0.120}
TP_TRAIL_BY_TF   = {"1m":0.015,"2m":0.020,"5m":0.020,"15m":0.030,"30m":0.030,"1h":0.040,"2h":0.040,"4h":0.050,"1d":0.060,"1w":0.080}

# ----------- Détection manuelle -----------
MANUAL_ADD_TOL           = float(os.getenv("MANUAL_ADD_TOL", "0.03"))
USE_VWAP_ON_MANUAL_ADD   = (os.getenv("USE_VWAP_ON_MANUAL_ADD", "false").lower() == "true")
VWAP_LOOKBACK_MIN        = int(os.getenv("VWAP_LOOKBACK_MIN", "7"))
MANUAL_SELL_EMPTY_THRESH = float(os.getenv("MANUAL_SELL_EMPTY_THRESH", "1e-9"))

# ----------- Anti-slippage / risk fraction globaux -----------
DEFAULT_MAX_SLIPPAGE_PCT = float(os.getenv("DEFAULT_MAX_SLIPPAGE_PCT", "2.0"))
DEFAULT_RISK_FRACTION    = float(os.getenv("DEFAULT_RISK_FRACTION", "0.99"))  # max 99% de l’USDT libre
