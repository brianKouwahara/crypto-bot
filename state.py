# state.py
# -*- coding: utf-8 -*-
import os, json, logging, datetime as dt, glob
from typing import Dict, Tuple
from config import STATE_FILE

log = logging.getLogger("bot")

# --------- Paramètres de backup ---------
BACKUP_DIR = os.getenv("STATE_BACKUP_DIR", "state_backups")
BACKUP_RETENTION = int(os.getenv("STATE_BACKUP_RETENTION", "50"))  # nb de fichiers à conserver

def _ser(dct: Dict[Tuple[str, str], float]):
    return {f"{k[0]}|{k[1]}": v for k, v in dct.items()}

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        log.info(f"[STATE] Etat chargé depuis {STATE_FILE}")
        return {
            "last_side":         {tuple(k.split("|")): v for k, v in data.get("last_side", {}).items()},
            "entry_price":       {tuple(k.split("|")): v for k, v in data.get("entry_price", {}).items()},
            "peak_price":        {tuple(k.split("|")): v for k, v in data.get("peak_price", {}).items()},
            "tp_armed":          {tuple(k.split("|")): v for k, v in data.get("tp_armed", {}).items()},
            "base_qty_at_entry": {tuple(k.split("|")): v for k, v in data.get("base_qty_at_entry", {}).items()},
            "last_trade_ts":     {tuple(k.split("|")): v for k, v in data.get("last_trade_ts", {}).items()},
            "buy_timestamps":    {tuple(k.split("|")): v for k, v in data.get("buy_timestamps", {}).items()},
            "cb_block_until_ts": float(data.get("cb_block_until_ts", 0.0)),
        }
    except Exception:
        log.info(f"[STATE] Aucun état existant (nouveau run)")
        return {
            "last_side": {}, "entry_price": {}, "peak_price": {}, "tp_armed": {},
            "base_qty_at_entry": {}, "last_trade_ts": {}, "buy_timestamps": {},
            "cb_block_until_ts": 0.0,
        }

def _ensure_parent_dir(path: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

def _backup_state_file():
    """Copie STATE_FILE vers BACKUP_DIR avec un nom horodaté. Applique la rétention."""
    try:
        if not os.path.exists(STATE_FILE):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = dt.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
        base = os.path.splitext(os.path.basename(STATE_FILE))[0]  # ex: 'state'
        backup_path = os.path.join(BACKUP_DIR, f"{base}_{ts}.json")
        # Lire puis réécrire pour éviter les liens durs/soft et garder l’atomicité logique
        with open(STATE_FILE, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        # Rétention
        files = sorted(glob.glob(os.path.join(BACKUP_DIR, f"{base}_*.json")))
        if BACKUP_RETENTION > 0 and len(files) > BACKUP_RETENTION:
            for old in files[:-BACKUP_RETENTION]:
                try:
                    os.remove(old)
                except Exception:
                    pass
        log.info(f"[STATE] Backup écrit -> {backup_path}")
    except Exception as e:
        log.warning(f"[STATE] Echec backup: {e}")

def save_state(last_side, entry_price, peak_price, tp_armed,
               base_qty_at_entry, last_trade_ts, buy_timestamps, cb_block_until_ts: float):
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

        _ensure_parent_dir(STATE_FILE)

        # 1) Écrire de façon atomique
        tmp_file = STATE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, STATE_FILE)  # remplace l’ancien fichier

        # 2) Faire un backup daté et appliquer la rétention
        _backup_state_file()

        log.info(f"[STATE] Etat sauvegardé -> {STATE_FILE}")
    except Exception as e:
        log.warning(f"[STATE] Echec sauvegarde: {e}")
