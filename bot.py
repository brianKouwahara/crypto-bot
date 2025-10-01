# -*- coding: utf-8 -*-
import os, sys, time, logging, traceback
import pandas as pd
import ccxt
from typing import Dict, Tuple
from logging.handlers import RotatingFileHandler

from config import (
    FEE_TAKER_PCT, COOLDOWN, SELL_SLIP_PCT, RISK_PER_TRADE_PCT, ATR_LOOKBACK, ATR_MULT_SL,
    MIN_AVG_DOLLAR_VOL, VOL_LOOKBACK, CB_SYMBOL, CB_TF, CB_WINDOW_MIN, CB_DROP_PCT,
    CB_COOLDOWN_MIN, MAX_BUYS_PER_24H, HYST_EPS_DEFAULT, HYST_EPS_BY_TF,
    STOP_LOSS_PCT_FALLBACK, TP_TRIGGER_FALLBACK, TP_TRAIL_FALLBACK,
    STOP_LOSS_BY_TF, TP_TRIGGER_BY_TF, TP_TRAIL_BY_TF, MAX_STALE_SEC_ENV,
    DEFAULT_MAX_SLIPPAGE_PCT, DEFAULT_RISK_FRACTION
)
from utils import (
    utcnow, next_candle_time, minutes_between, touch_heartbeat, note_progress,
    get_env_clean, tf_to_minutes, send_webhook, _last_progress
)
from signals import hybrid_signal, pick_conf_for_tf, avg_dollar_volume, compute_atr
from state import load_state, save_state
from execution import build_exchange, with_retry, place_market_buy, place_market_sell_all

# -------- LOGGING --------
LOG_FMT = "%(asctime)s | %(levelname)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, stream=sys.stdout)
fh = RotatingFileHandler("bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
fh.setFormatter(logging.Formatter(LOG_FMT))
logging.getLogger().addHandler(fh)
log = logging.getLogger("bot")

ALLOC_RE = __import__("re").compile(r"^\d+(\.\d+)?%?$")


def parse_pairs_cfg(raw: str):
    """PAIRE@TF=ALLOC,avg=(sma|ema),avg_period=<int>,rsi=<int>,signal=(live|closed)[,slip=<pct>]; ..."""
    out = []
    if not raw:
        return out
    entries = [e.strip() for e in raw.split(";") if e.strip()]
    for entry in entries:
        left, *attrs = [frag.strip() for frag in entry.split(",")]
        pair_tf, alloc = [frag.strip() for frag in left.split("=", 1)]
        pair, tf = [frag.strip() for frag in pair_tf.split("@", 1)]

        if not ALLOC_RE.match(alloc):
            raise ValueError(f"Allocation invalide '{alloc}'")
        _ = tf_to_minutes(tf)  # validation TF

        avg, avg_period, rsi_per, signal, slip = "ema", 21, 21, "closed", None
        for frag in attrs:
            if "=" not in frag:
                continue
            k, v = frag.split("=", 1)
            k = k.strip().lower()
            v = v.strip().lower()

            if k == "avg":
                if v not in ("ema", "sma"):
                    raise ValueError("avg doit être 'ema' ou 'sma'")
                avg = v
            elif k == "avg_period":
                ap = int(v)
                if ap <= 0:
                    raise ValueError("avg_period doit être > 0")
                avg_period = ap
            elif k == "rsi":
                rp = int(v)
                if rp <= 0:
                    raise ValueError("rsi doit être > 0")
                rsi_per = rp
            elif k == "signal":
                if v not in ("live", "closed"):
                    raise ValueError("signal doit être 'live' ou 'closed'")
                signal = v
            elif k == "slip":
                try:
                    sv = float(v)
                except Exception:
                    raise ValueError("slip doit être un nombre (en %)")
                slip = sv

        out.append({
            "symbol": pair,
            "tf": tf.lower(),
            "alloc": alloc,
            "avg": avg,
            "avg_period": avg_period,
            "rsi_period": rsi_per,
            "signal": signal,
            "slip": slip,
        })
    return out


def get_base_balance(exchange, market):
    base_ccy = market.get("base")
    bal = with_retry(exchange.fetch_balance, 3, 1)
    return float((bal.get(base_ccy) or {}).get("free", 0.0))


def compute_vwap_from_trades(trades):
    if not trades:
        return None
    tot_cost, tot_amt = 0.0, 0.0
    for tr in trades:
        price = float(tr.get("price") or 0.0)
        amt = float(tr.get("amount") or 0.0)
        if amt <= 0:
            continue
        cost = float(tr.get("cost") or (price * amt))
        tot_cost += cost
        tot_amt += amt
    if tot_amt <= 0:
        return None
    return tot_cost / tot_amt


def bot_loop():
    log.info(f"[ENV] Python: {sys.version.split()[0]}")
    log.info("[START] Demarrage bot (Bitget Spot)")

    DRY_RUN = (os.getenv("DRY_RUN", "true").lower() == "true")
    pairs_cfg_raw = get_env_clean("PAIRS_CFG")
    if not pairs_cfg_raw:
        raise ValueError("[ERROR] Aucune paire dans PAIRS_CFG")
    cfg_list = parse_pairs_cfg(pairs_cfg_raw)
    if not cfg_list:
        raise ValueError("[ERROR] Aucune paire valide dans PAIRS_CFG")

    # ✅ Notification démarrage
    try:
        send_webhook("bot_start", {
            "emoji": "🚀",
            "message": "Bot démarré",
            "mode": "TEST" if DRY_RUN else "LIVE",
            "pairs_count": len(cfg_list),
            "min_tf": min(tf_to_minutes(c["tf"]) for c in cfg_list),
            "ts": int(time.time())
        })
    except Exception:
        pass

    # -------- Watchdog basé sur le plus petit TF --------
    if MAX_STALE_SEC_ENV:
        try:
            MAX_STALE_SEC = int(MAX_STALE_SEC_ENV)
        except Exception:
            MAX_STALE_SEC = 600
    else:
        min_tf_min = min(tf_to_minutes(c["tf"]) for c in cfg_list)
        # 3x le plus petit TF + marge 60s
        MAX_STALE_SEC = int(min_tf_min * 3 * 60 + 60)
    log.info(f"[WATCHDOG] MAX_STALE_SEC = {MAX_STALE_SEC}s")

    exchange = build_exchange()
    note_progress()
    markets = exchange.load_markets()
    note_progress()
    for c in cfg_list:
        if c["symbol"] not in markets:
            raise ValueError(f"[ERROR] Symbole inexistant: {c['symbol']}")

    log.info("[CONFIG] Configuration :")
    for c in cfg_list:
        log.info(
            f" - {c['symbol']} @ {c['tf']} | alloc={c['alloc']} | avg={c['avg']} | "
            f"avg_period={c['avg_period']} | rsi={c['rsi_period']} | signal={c['signal']} | slip={c.get('slip')} |"
        )
    log.info(f"Mode = {'TEST' if DRY_RUN else 'LIVE'}")

    tf_minutes_map = {c["tf"]: tf_to_minutes(c["tf"]) for c in cfg_list}
    next_run = {}
    now = utcnow()
    for tf, mins in tf_minutes_map.items():
        next_run[tf] = next_candle_time(now, mins)

    trades_per_candle: Dict[Tuple[str, str, object], int] = {}
    MIN_BUY_USDT = 1.0

    _state = load_state()
    last_side = _state["last_side"]
    entry_price = _state["entry_price"]
    peak_price = _state["peak_price"]
    tp_armed = _state["tp_armed"]
    base_qty_at_entry = _state["base_qty_at_entry"]
    last_trade_ts = _state["last_trade_ts"]
    buy_timestamps = _state.get("buy_timestamps", {})
    cb_block_until_ts = float(_state.get("cb_block_until_ts", 0.0))

    touch_heartbeat(force=True)

    def circuit_breaker_active() -> bool:
        return time.time() < cb_block_until_ts

    while True:
        # Heartbeat / anti-stale
        touch_heartbeat()
        if (time.time() - _last_progress) > MAX_STALE_SEC:
            delay = int(time.time() - _last_progress)
            log.error(f"[STALE] Pas de progrès > {MAX_STALE_SEC}s (delay={delay}). Exit(42).")
            # ✅ Notification watchdog (stale)
            try:
                send_webhook("bot_stale_exit", {
                    "emoji": "⏳",
                    "message": "Watchdog: données/avancement figés",
                    "stale_sec": delay,
                    "max_stale_sec": MAX_STALE_SEC,
                    "code": 42,
                    "ts": int(time.time())
                })
            except Exception:
                pass
            sys.exit(42)

        now = utcnow()
        due_tfs = [tf for tf, t in next_run.items() if now >= t]
        if not due_tfs:
            wake_at = min(next_run.values())
            delta = max(1, int((wake_at - now).total_seconds()))
            log.info(f"[SLEEP] Aucun TF dû. Réveil dans {delta}s (à {wake_at:%Y-%m-%d %H:%M:%S} UTC)")
            time.sleep(min(delta, 30))
            continue

        log.info(f"[CYCLE] TF dû: {', '.join(due_tfs)} | now={now:%Y-%m-%d %H:%M:%S} UTC")
        note_progress()

        # --- Circuit breaker global (refresh par cycle) ---
        try:
            if CB_DROP_PCT > 0 and CB_COOLDOWN_MIN > 0:
                cb_raw = with_retry(exchange.fetch_ohlcv, 3, 1, CB_SYMBOL, timeframe=CB_TF, limit=200)
                cb_df = pd.DataFrame(cb_raw, columns=["ts", "open", "high", "low", "close", "vol"])
                tfm = tf_to_minutes(CB_TF)
                bars = max(1, int(CB_WINDOW_MIN / max(tfm, 1)))
                if len(cb_df) > bars:
                    p0 = float(cb_df["close"].iloc[-bars - 1])
                    p1 = float(cb_df["close"].iloc[-1])
                    change = (p1 - p0) / p0 * 100.0
                    if change <= -abs(CB_DROP_PCT):
                        cb_block_until_ts = time.time() + CB_COOLDOWN_MIN * 60
                        log.warning(f"[CB] Actif ({CB_SYMBOL} {change:.2f}% <= -{CB_DROP_PCT}%). BUY off {CB_COOLDOWN_MIN} min")
        except Exception as e:
            log.warning(f"[CB] Echec: {e}")

        # Solde USDT
        try:
            balance = with_retry(exchange.fetch_balance, 3, 1)
            usdt_free = float((balance.get("USDT") or {}).get("free", 0.0))
            note_progress()
        except Exception as e:
            log.warning(f"[WARN] fetch_balance KO: {e}")
            usdt_free = 0.0
        log.info(f"[BALANCE] USDT dispo: {usdt_free:.2f}")
        usdt_free_local = usdt_free

        # Contrôle d’alloc indicatif
        try:
            alloc_sum = 0.0
            for c in cfg_list:
                if c["tf"] not in due_tfs:
                    continue
                a = c["alloc"].strip()
                alloc_sum += (float(a[:-1]) * usdt_free / 100.0) if a.endswith("%") else float(a)
            if alloc_sum > usdt_free:
                log.warning(f"[WARN] Somme allocations dues ({alloc_sum:.2f}) > solde ({usdt_free:.2f})")
        except Exception as e:
            log.warning(f"[WARN] Controle allocations: {e}")

        current_keys = set()

        for c in cfg_list:
            if c["tf"] not in due_tfs:
                continue

            sym, tf, alloc = c["symbol"], c["tf"], c["alloc"]
            avg, avg_period, rsi_period = c["avg"], c["avg_period"], c["rsi_period"]
            signal_mode, slip_pct = c["signal"], c.get("slip")

            try:
                # OHLCV
                ohlcv = with_retry(exchange.fetch_ohlcv, 3, 1, sym, timeframe=tf, limit=300)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "vol"])
                df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)

                # MAX_STALE par TF
                last_ts = df["ts"].iloc[-1]
                MAX_STALE_BY_TF = {"1m": 3, "2m": 5, "5m": 10, "15m": 30, "30m": 60, "1h": 90, "2h": 150, "4h": 360,
                                   "1d": 2880, "1w": 4320}
                staleness_min = minutes_between(utcnow(), last_ts)
                max_stale = MAX_STALE_BY_TF.get(tf, 120)
                if staleness_min > max_stale:
                    log.warning(f"[STALE/TF] {sym}@{tf} données trop anciennes ({staleness_min:.1f} > {max_stale}). Skip.")
                    continue

                # Filtre de volume global (optionnel)
                if MIN_AVG_DOLLAR_VOL > 0:
                    avg_vol_usd_glob = avg_dollar_volume(df, VOL_LOOKBACK)
                    if avg_vol_usd_glob < MIN_AVG_DOLLAR_VOL:
                        log.info(f"[LIQ] {sym}@{tf} avg$vol={avg_vol_usd_glob:.0f} < {MIN_AVG_DOLLAR_VOL:.0f} → skip")
                        continue

                # --- Signal hybride ---
                conf = pick_conf_for_tf(tf)
                rsi_last, rsi_avg_last, st_trend, don_high_last, don_low_last, vol_ok, action = hybrid_signal(
                    df, tf, conf,
                    signal_mode=signal_mode,
                    avg_type=avg,
                    avg_period=avg_period,
                    rsi_period=rsi_period
                )

                close = float(df["close"].iloc[-1])
                ts = df["ts"].iloc[-1 if signal_mode == "live" else -2]
                current_keys.add((sym, tf, ts))

                side_key = (sym, tf)
                mkt = exchange.market(sym)
                try:
                    cur_base = get_base_balance(exchange, mkt)
                except Exception as e:
                    log.warning(f"[MANUAL BAL] fetch_balance {sym} KO: {e}")
                    cur_base = base_qty_at_entry.get(side_key, 0.0)

                prev_base = base_qty_at_entry.get(side_key)

                # Vente manuelle ?
                if last_side.get(side_key) == "buy" and cur_base <= float(os.getenv("MANUAL_SELL_EMPTY_THRESH", "1e-9")):
                    log.info(f"[MANUAL SELL] {sym}@{tf} détectée. Reset état.")
                    entry_price.pop(side_key, None)
                    peak_price.pop(side_key, None)
                    tp_armed.pop(side_key, None)
                    base_qty_at_entry.pop(side_key, None)
                    last_side[side_key] = "sell"
                    save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                               last_trade_ts, buy_timestamps, cb_block_until_ts)

                # Renfort manuel ?
                from config import MANUAL_ADD_TOL, USE_VWAP_ON_MANUAL_ADD, VWAP_LOOKBACK_MIN
                if last_side.get(side_key) == "buy" and prev_base not in (None, 0.0) and cur_base > prev_base:
                    growth = (cur_base - prev_base) / prev_base
                    if growth >= MANUAL_ADD_TOL:
                        if USE_VWAP_ON_MANUAL_ADD:
                            since = int((utcnow() - __import__("datetime").timedelta(days=VWAP_LOOKBACK_MIN)).timestamp() * 1000)
                            try:
                                my_trades = with_retry(exchange.fetch_my_trades, 3, 1, sym, since)
                                vwap = compute_vwap_from_trades([t for t in my_trades if (str(t.get("side")).lower() == "buy")])
                            except Exception:
                                vwap = None
                            new_entry = vwap if vwap else close
                            entry_price[side_key] = new_entry
                            peak_price[side_key] = max(new_entry, close)
                        else:
                            entry_price[side_key] = close
                            peak_price[side_key] = close
                        tp_armed[side_key] = False
                        base_qty_at_entry[side_key] = cur_base
                        log.info(f"[MANUALADD] Recalage {sym}: entry={entry_price[side_key]:.8f}, base={cur_base:.8f} (+{growth*100:.2f}%)")
                        save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                                   last_trade_ts, buy_timestamps, cb_block_until_ts)
                elif prev_base is None and cur_base > 0:
                    base_qty_at_entry[side_key] = cur_base
                    save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                               last_trade_ts, buy_timestamps, cb_block_until_ts)

                # Hystérésis
                prev_side = last_side.get(side_key)
                diff_val = float(rsi_last - rsi_avg_last)
                HYST_EPS = HYST_EPS_BY_TF.get(tf, HYST_EPS_DEFAULT)
                if action == "buy" and prev_side == "sell" and diff_val <= HYST_EPS:
                    log.info(f"[HYST] Flip SELL->BUY bloqué (diff={diff_val:.2f} <= {HYST_EPS}) {sym}")
                    action = None
                elif action == "sell" and prev_side == "buy" and -diff_val <= HYST_EPS:
                    log.info(f"[HYST] Flip BUY->SELL bloqué (diff={-diff_val:.2f} <= {HYST_EPS}) {sym}")
                    action = None

                # SL / TP si en position
                if last_side.get(side_key) == "buy":
                    if side_key not in entry_price:
                        entry_price[side_key] = close
                        peak_price[side_key] = close
                        tp_armed[side_key] = False
                    peak_price[side_key] = max(peak_price.get(side_key, close), close)

                    fee = max(0.0, FEE_TAKER_PCT)
                    entry_eff = entry_price[side_key] * (1.0 + fee)
                    close_eff = close * (1.0 - fee)
                    pnl_net = (close_eff - entry_eff) / entry_eff
                    peak_eff = peak_price[side_key] * (1.0 - fee)
                    drawdown_net = (close_eff - peak_eff) / peak_eff

                    sl_pct = STOP_LOSS_BY_TF.get(tf, STOP_LOSS_PCT_FALLBACK)
                    tp_trigger = TP_TRIGGER_BY_TF.get(tf, TP_TRIGGER_FALLBACK)
                    tp_trail = TP_TRAIL_BY_TF.get(tf, TP_TRAIL_FALLBACK)

                    if tp_trigger <= tp_trail:
                        tp_trigger = tp_trail + 0.01
                    MIN_LOCK = 0.02
                    if (tp_trigger - tp_trail) < MIN_LOCK:
                        tp_trigger = tp_trail + MIN_LOCK
                    MIN_RR = 1.5
                    if sl_pct > 0 and (tp_trigger / sl_pct) < MIN_RR:
                        tp_trigger = sl_pct * MIN_RR

                    if not tp_armed.get(side_key, False) and pnl_net >= tp_trigger:
                        tp_armed[side_key] = True
                        log.info(f"[TP] Trailing armé {sym} @ gain_net={pnl_net*100:.2f}%")
                        save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                                   last_trade_ts, buy_timestamps, cb_block_until_ts)

                    if sl_pct and pnl_net <= -sl_pct:
                        log.info(f"[SL] Stop-loss SELL {sym}: {pnl_net*100:.2f}%")
                        action = "sell"
                    elif tp_armed.get(side_key, False) and drawdown_net <= -tp_trail:
                        log.info(f"[TP] Trailing SELL {sym}: drawdown={drawdown_net*100:.2f}%")
                        action = "sell"

                # -------- LOG "raison du refus" + état --------
                reasons = []
                if not vol_ok:
                    reasons.append("VolOk=False")
                if conf["donchian"].get("require_breakout", True) and (don_high_last is not None) and (close <= don_high_last):
                    reasons.append("Donchian=False")
                if rsi_last <= rsi_avg_last:
                    reasons.append("RSI<=RSIavg")
                if st_trend != "bull":
                    reasons.append("ST!=bull")

                log.info(
                    f"[DATA] {sym} | Close={close:.8f} | RSI={rsi_last:.2f}/{rsi_avg_last:.2f} | "
                    f"ST={st_trend} | Don(H/L)={don_high_last}/{don_low_last} | VolOK={vol_ok} | "
                    f"can_buy={action=='buy'} | reason={'OK' if not reasons else ','.join(reasons)}"
                )

                # Limite par bougie
                key = (sym, tf, ts)
                count = trades_per_candle.get(key, 0)
                if count >= 3:
                    log.warning(f"[WARN] Max 3 trades {sym} @ {ts}")
                    action = None

                # Cooldown
                cool = COOLDOWN.get(tf, 0) or 0
                if cool > 0:
                    lt = float(last_trade_ts.get(side_key, 0.0))
                    if time.time() - lt < cool:
                        log.info(f"[COOLDOWN] {sym}@{tf} {int(time.time()-lt)}s < {cool}s")
                        action = None

                # Cap BUY / 24h
                if action == "buy" and MAX_BUYS_PER_24H > 0:
                    lst = buy_timestamps.get(side_key, [])
                    now_ts = time.time()
                    lst = [t for t in lst if (now_ts - float(t)) < 24 * 3600]
                    if len(lst) >= MAX_BUYS_PER_24H:
                        log.info(f"[CAP] {sym}@{tf} plafond BUY atteint")
                        action = None
                    buy_timestamps[side_key] = lst

                # Circuit breaker
                if action == "buy" and circuit_breaker_active():
                    left = int(cb_block_until_ts - time.time())
                    log.info(f"[CB] BUY bloqué (~{max(left, 0)}s)")
                    action = None

                # Allocation locale
                if usdt_free_local <= MIN_BUY_USDT and action == "buy":
                    log.info(f"[INFO] Plus d'allocation USDT locale (<= {MIN_BUY_USDT}) {sym}")
                    action = None

                # === EXECUTION ===
                if action == "buy":
                    usdt_amt_alloc = (float(alloc[:-1]) * usdt_free / 100.0) if alloc.endswith('%') else float(alloc)
                    usdt_amt = usdt_amt_alloc

                    # --- Risk sizing optionnel (ATR/SL) ---
                    if RISK_PER_TRADE_PCT > 0:
                        try:
                            atr = compute_atr(df, ATR_LOOKBACK)
                            sl_pct_est = STOP_LOSS_BY_TF.get(tf, STOP_LOSS_PCT_FALLBACK)
                            if ATR_MULT_SL > 0 and close > 0:
                                sl_pct_est = max(sl_pct_est, (ATR_MULT_SL * atr) / close)
                            risk_usdt = usdt_free * (RISK_PER_TRADE_PCT / 100.0)
                            if sl_pct_est > 0:
                                usdt_amt = min(usdt_amt_alloc, risk_usdt / sl_pct_est)
                        except Exception as e:
                            log.warning(f"[RISK] Sizing ATR impossible: {e}")

                    # --- Risk fraction global ---
                    usdt_amt = max(0.0, min(usdt_amt, usdt_free_local * DEFAULT_RISK_FRACTION))

                    if usdt_amt <= MIN_BUY_USDT:
                        log.info(f"[BUY-SKIP] Montant insuffisant (<= {MIN_BUY_USDT} USDT)")
                    else:
                        # --- Anti-slippage universel (manuel par paire > sinon défaut global) ---
                        slip_limit = (slip_pct if (slip_pct is not None) else DEFAULT_MAX_SLIPPAGE_PCT)
                        log.info(f"[BUY] {sym} usdt={usdt_amt:.2f} (slip≤{slip_limit}%)")
                        if not DRY_RUN:
                            try:
                                order = place_market_buy(exchange, sym, usdt_amt, slip_limit_pct=slip_limit)
                                if isinstance(order, dict) and order.get("skipped"):
                                    log.info(f"[BUY-SKIP] {sym} (reason={order.get('reason')})")
                                else:
                                    trades_per_candle[key] = count + 1
                                    entry_price[side_key] = close
                                    peak_price[side_key] = close
                                    tp_armed[side_key] = False
                                    last_side[side_key] = "buy"
                                    try:
                                        base_qty_at_entry[side_key] = get_base_balance(exchange, mkt)
                                    except Exception:
                                        base_qty_at_entry[side_key] = base_qty_at_entry.get(side_key, 0.0)
                                    usdt_free_local = max(0.0, usdt_free_local - usdt_amt)
                                    last_trade_ts[side_key] = time.time()
                                    lst = buy_timestamps.get(side_key, [])
                                    lst.append(time.time())
                                    buy_timestamps[side_key] = lst
                                    save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                                               last_trade_ts, buy_timestamps, cb_block_until_ts)
                                    send_webhook("buy", {"symbol": sym, "tf": tf, "price": close, "usdt": usdt_amt})
                            except Exception as e:
                                log.error(f"[ERROR] BUY échec ({sym}) -> {e}")
                        else:
                            trades_per_candle[key] = count + 1
                            entry_price[side_key] = close
                            peak_price[side_key] = close
                            tp_armed[side_key] = False
                            last_side[side_key] = "buy"
                            usdt_free_local = max(0.0, usdt_free_local - usdt_amt)
                            last_trade_ts[side_key] = time.time()
                            lst = buy_timestamps.get(side_key, [])
                            lst.append(time.time())
                            buy_timestamps[side_key] = lst
                            save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                                       last_trade_ts, buy_timestamps, cb_block_until_ts)
                            send_webhook("buy_dry", {"symbol": sym, "tf": tf, "price": close, "usdt": usdt_amt})

                elif action == "sell":
                    log.info(f"[SELL] {sym} (liquidation)")
                    if not DRY_RUN:
                        try:
                            order = place_market_sell_all(exchange, sym, slip_limit_pct=SELL_SLIP_PCT)
                            if isinstance(order, dict) and order.get("skipped"):
                                log.info(f"[SELL-SKIP] {sym} (reason={order.get('reason')})")
                            else:
                                trades_per_candle[key] = count + 1
                                entry_price.pop(side_key, None)
                                peak_price.pop(side_key, None)
                                tp_armed.pop(side_key, None)
                                base_qty_at_entry.pop(side_key, None)
                                last_side[side_key] = "sell"
                                last_trade_ts[side_key] = time.time()
                                save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                                           last_trade_ts, buy_timestamps, cb_block_until_ts)
                                send_webhook("sell", {"symbol": sym, "tf": tf, "price": close})
                        except Exception as e:
                            log.error(f"[ERROR] SELL échec ({sym}) -> {e}")
                    else:
                        trades_per_candle[key] = count + 1
                        entry_price.pop(side_key, None)
                        peak_price.pop(side_key, None)
                        tp_armed.pop(side_key, None)
                        base_qty_at_entry.pop(side_key, None)
                        last_side[side_key] = "sell"
                        last_trade_ts[side_key] = time.time()
                        save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                                   last_trade_ts, buy_timestamps, cb_block_until_ts)
                        send_webhook("sell_dry", {"symbol": sym, "tf": tf, "price": close})
                else:
                    log.info(f"[INFO] Aucun signal {sym}")

                time.sleep(0.25)

            except ccxt.BaseError as e:
                log.warning(f"[WARN] Exchange {sym}: {e}")
            except Exception as e:
                log.error(f"[ERROR] Général {sym}: {e}\n{traceback.format_exc()}")

        # purge compteurs bougie
        if trades_per_candle:
            trades_per_candle = {k: v for k, v in trades_per_candle.items() if k in current_keys}

        save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry,
                   last_trade_ts, buy_timestamps, cb_block_until_ts)

        now2 = utcnow()
        for tf in due_tfs:
            mins = tf_minutes_map[tf]
            next_run[tf] = next_candle_time(now2, mins)


# -------- Redémarrage auto (watchdog) --------
def main():
    while True:
        try:
            bot_loop()
        except SystemExit:
            # Exit volontaire (watchdog) -> l’orchestrateur redémarre
            raise
        except Exception as e:
            # ✅ Notification crash + annonce redémarrage
            try:
                send_webhook("bot_crash", {
                    "emoji": "💥",
                    "message": "Crash imprévu",
                    "error": str(e),
                    "trace": traceback.format_exc()[-1200:],  # tronqué pour TG
                    "ts": int(time.time())
                })
                send_webhook("bot_autorestart", {
                    "emoji": "🔁",
                    "message": "Redémarrage automatique planifié",
                    "delay_sec": 10,
                    "ts": int(time.time())
                })
            except Exception:
                pass
            log.error(f"[CRASH] Bot crashe: {e}\n{traceback.format_exc()}")
            log.info("[RESTART] Redemarrage dans 10s...")
            time.sleep(10)


if __name__ == "__main__":
    main()
