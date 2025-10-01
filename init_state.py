import json
import os

# Fichier de state utilisé par ton bot (adapte le chemin si besoin)
STATE_FILE = os.getenv("STATE_FILE", "state.json")

def init_position(symbol: str, tf: str, entry_price: float, qty: float):
    """Ajoute ou met à jour une position manuelle dans state.json"""
    key = f"{symbol}|{tf}"

    # Charger état existant
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                state = json.load(f)
            except Exception:
                state = {}
    else:
        state = {}

    # S'assurer que toutes les sections existent
    for k in ["last_side", "entry_price", "peak_price", "tp_armed", "base_qty_at_entry", "last_trade_ts", "buy_timestamps"]:
        if k not in state:
            state[k] = {}

    # Injecter la position
    state["last_side"][key] = "buy"
    state["entry_price"][key] = entry_price
    state["peak_price"][key] = entry_price
    state["tp_armed"][key] = False
    state["base_qty_at_entry"][key] = qty
    state["last_trade_ts"][key] = 0
    if "buy_timestamps" not in state:
        state["buy_timestamps"] = {}
    state["buy_timestamps"][key] = []

    # Circuit breaker par défaut
    state["cb_block_until_ts"] = state.get("cb_block_until_ts", 0.0)

    # Sauvegarde
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    print(f"[OK] Position initialisée : {symbol}@{tf} entry={entry_price} qty={qty}")

if __name__ == "__main__":
    # ⚠️ Exemple : adapte ces valeurs à ta position réelle !
    init_position(
        symbol="XYZ/USDT",  # paire exacte comme dans PAIRS_CFG
        tf="5m",            # timeframe exacte comme dans PAIRS_CFG
        entry_price=0.0123, # ton prix moyen d'achat
        qty=1234            # quantité achetée
    )
