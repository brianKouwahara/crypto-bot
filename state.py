import os, json, logging, datetime as dt
from typing import Dict, Tuple
from config import STATE_FILE

log = logging.getLogger("bot")

def _ser(dct: Dict[Tuple[str, str], float]):
    return {f"{k[0]}|{k[1]}": v for k, v in dct.items()}

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        log.info(f"[STATE] Etat chargé depuis {STATE_FILE}")
        return {
            "last_side":             {tuple(k.split("|")): v for k, v in data.get("last_side", {}).items()},
            "entry_price":           {tuple(k.split("|")): v for k, v in data.get("entry_price", {}).items()},
            "peak_price":            {tuple(k.split("|")): v for k, v in data.get("peak_price", {}).items()},
            "tp_armed":              {tuple(k.split("|")): v for k, v in data.get("tp_armed", {}).items()},
            "base_qty_at_entry":     {tuple(k.split("|")): v for k, v in data.get("base_qty_at_entry", {}).items()},
            "last_trade_ts":         {tuple(k.split("|")): v for k, v in data.get("last_trade_ts", {}).items()},
            "buy_timestamps":        {tuple(k.split("|")): v for k, v in data.get("buy_timestamps", {}).items()},
            "cb_block_until_ts":     float(data.get("cb_block_until_ts", 0.0)),
        }
    except Exception:
        log.info(f"[STATE] Aucun état existant (nouveau run)")
        return {
            "last_side": {}, "entry_price": {}, "peak_price": {}, "tp_armed": {},
            "base_qty_at_entry": {}, "last_trade_ts": {}, "buy_timestamps": {},
            "cb_block_until_ts": 0.0,
        }

def save_state(last_side, entry_price, peak_price, tp_armed, base_qty_at_entry, last_trade_ts, buy_timestamps, cb_block_until_ts: float):
    try:
        payload = {
            "last_side":         _ser(last_side),
            "entry_price":       _ser(entry_price),
            "peak_price":        _ser(peak_price),
            "tp_armed":          _ser(tp_armed),
            "base_qty_at_entry": _ser(base_qty_at_entry),
            "last_trade_ts":     _ser(last_trade_ts),
            "buy_timestamps":    {f"{k[0]}|{k[1]}": v for k, v in buy_timestamps.items()},
            "cb_block_until_ts": float(cb_block_until_ts),
            "saved_at":          dt.datetime.utcnow().isoformat(),
        }
        path = os.path.dirname(STATE_FILE)
        if path: os.makedirs(path, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            import json
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.info(f"[STATE] Etat sauvegardé -> {STATE_FILE}")
    except Exception as e:
        log.warning(f"[STATE] Echec sauvegarde: {e}")
