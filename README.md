## Bot Spot Bitget — V2 Modulaire
- Signal hybride: RSI > lissage, Supertrend bull/bear, breakout Donchian + filtre de volume $.
- SL par TF + Trailing TP avec garde-fous.
- Anti-slippage BUY/SELL, cooldowns, plafond BUY/24h, circuit breaker marché.
- Persistance d’état (JSON), watchdog heartbeat + auto-restart.

### Lancer en local
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Créer `.env` à partir de `.env.example`
4. `python -m botv2.bot`

### Déploiement Render
- Crée un **Background Worker** avec `python -m botv2.bot`
- Ajoute les **Env Vars** du `.env` dans le dashboard Render (ne **pas** commit les clés).
