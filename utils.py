# utils.py
# -*- coding: utf-8 -*-
import os, time, datetime as dt, json, logging, urllib.request as _req, urllib.parse
from config import HEARTBEAT_FILE, HEARTBEAT_INTERVAL_SEC, WEBHOOK_URL

log = logging.getLogger("bot")

_last_hb = 0.0
_last_progress = time.time()  # suivi watchdog


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def floor_dt_to_tf(now: dt.datetime, tf_minutes: int) -> dt.datetime:
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    total_min = int((now - epoch).total_seconds() // 60)
    floored_min = (total_min // tf_minutes) * tf_minutes
    return epoch + dt.timedelta(minutes=floored_min)

def next_candle_time(now: dt.datetime, tf_minutes: int) -> dt.datetime:
    return floor_dt_to_tf(now, tf_minutes) + dt.timedelta(minutes=tf_minutes)

def minutes_between(a: dt.datetime, b: dt.datetime) -> float:
    return abs((a - b).total_seconds()) / 60.0

def touch_heartbeat(force: bool = False):
    """Écrit un heartbeat dans HEARTBEAT_FILE toutes les HEARTBEAT_INTERVAL_SEC secondes."""
    global _last_hb
    now_ts = time.time()
    if force or (now_ts - _last_hb) >= HEARTBEAT_INTERVAL_SEC:
        try:
            path = os.path.dirname(HEARTBEAT_FILE)
            if path and not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
            with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                f.write(str(dt.datetime.utcnow()))
            _last_hb = now_ts
            log.info("[HB] Heartbeat OK")
        except Exception as e:
            log.warning(f"[HB] Ecriture heartbeat KO: {e}")

def note_progress():
    """Met à jour le marqueur de progrès global (watchdog)."""
    global _last_progress
    _last_progress = time.time()

def get_env_clean(key: str) -> str:
    """Retourne une variable d’environnement nettoyée (sans quotes/retours ligne)."""
    val = os.getenv(key, "")
    if not val:
        return ""
    val = val.replace("\r", "").strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    return val.replace("\n", "").strip()

def tf_to_minutes(tf: str) -> int:
    """Convertit un timeframe en minutes (ex: '15m' → 15)."""
    tf = tf.lower()
    if tf.endswith("m"): return int(tf[:-1])
    if tf.endswith("h"): return int(tf[:-1]) * 60
    if tf.endswith("d"): return int(tf[:-1]) * 60 * 24
    if tf.endswith("w"): return int(tf[:-1]) * 60 * 24 * 7
    raise ValueError(f"Timeframe non supporté: {tf}")

# -----------------------------------------------------------
# ✅ Envoi Webhook/Telegram ergonomique
# -----------------------------------------------------------
def format_message(event: str, payload: dict) -> str:
    """Construit un message texte ergonomique pour Telegram."""
    emoji = payload.get("emoji", "ℹ️")
    msg = payload.get("message", event.upper())

    if event.startswith("buy"):
        return f"🟢 {emoji} Achat {payload.get('symbol','?')} @ {payload.get('price',0):.4f}\n💵 {payload.get('usdt',0):.2f} USDT"
    elif event.startswith("sell"):
        return f"🔴 {emoji} Vente {payload.get('symbol','?')} @ {payload.get('price',0):.4f}"
    elif event == "bot_start":
        return f"🚀 Bot démarré en mode *{payload.get('mode','?')}* avec {payload.get('pairs_count',0)} paires."
    elif event == "bot_stale_exit":
        return f"⏳ Watchdog: aucune activité > {payload.get('max_stale_sec','?')}s\n(exit 42)"
    elif event == "bot_crash":
        return f"💥 Crash imprévu:\n{payload.get('error','?')}"
    elif event == "bot_autorestart":
        return f"🔁 Redémarrage auto dans {payload.get('delay_sec',10)}s"
    else:
        return f"{emoji} {msg}"

def send_webhook(event: str, payload: dict):
    """Envoie un webhook JSON et aussi un message texte ergonomique (Telegram-ready)."""
    if not WEBHOOK_URL:
        return
    try:
        # Envoi JSON brut (API webhook type REST/Zapier/Render logs)
        data = json.dumps({"event": event, **payload}).encode("utf-8")
        req = _req.Request(WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"})
        _req.urlopen(req, timeout=5).read()
        log.info(f"[WEBHOOK] {event} JSON envoyé")

        # Envoi texte formaté (Telegram Bot API si URL correspond)
        text_msg = format_message(event, payload)
        if "api.telegram.org" in WEBHOOK_URL:  # cas Telegram direct
            send_telegram_message(WEBHOOK_URL, text_msg)
        else:
            log.info(f"[WEBHOOK] Message formaté: {text_msg}")

    except Exception as e:
        log.warning(f"[WEBHOOK] Echec envoi: {e}")

def send_telegram_message(base_url: str, text: str):
    """Envoi direct Telegram si WEBHOOK_URL est déjà un endpoint Bot API."""
    try:
        url = f"{base_url}?{urllib.parse.urlencode({'text': text, 'parse_mode': 'Markdown'})}"
        _req.urlopen(url, timeout=5).read()
        log.info("[TELEGRAM] Message envoyé")
    except Exception as e:
        log.warning(f"[TELEGRAM] Echec: {e}")
