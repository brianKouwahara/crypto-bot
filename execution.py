# execution.py
# -*- coding: utf-8 -*-
from typing import Optional
import time
import logging
import ccxt

log = logging.getLogger("bot")

# Erreurs réseau/charge à retenter
NETWORK_EXCEPTIONS = (
    ccxt.NetworkError,
    ccxt.ExchangeNotAvailable,
    ccxt.RequestTimeout,
    getattr(ccxt, "DDoSProtection", Exception),
    getattr(ccxt, "RateLimitExceeded", Exception),
)

def with_retry(fn, retries: int = 3, base_sleep: float = 1.0, *a, **kw):
    """Exécute fn avec retry exponentiel sur erreurs réseau connues."""
    for i in range(retries):
        try:
            return fn(*a, **kw)
        except NETWORK_EXCEPTIONS as e:
            wait = base_sleep * (2 ** i)
            log.warning(f"[RETRY] Tentative {i+1}/{retries} après erreur réseau: {e} (pause {wait}s)")
            time.sleep(wait)
    # Dernière tentative sans capture pour surfacer l'erreur
    return fn(*a, **kw)

def build_exchange():
    """Construit l'instance Bitget Spot depuis les variables d'environnement."""
    import os
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    password = os.getenv("PASSWORD")
    if not api_key or not api_secret or not password:
        raise ValueError("[ERROR] API_KEY, API_SECRET ou PASSWORD manquants")
    return ccxt.bitget({
        "apiKey": api_key,
        "secret": api_secret,
        "password": password,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
        "timeout": 20000,
    })

def best_last_from_ticker(t: dict) -> float:
    """Retourne un 'last' exploitable en priorisant last/close/bid/ask puis mid(bid,ask)."""
    for k in ("last", "close", "bid", "ask"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    bid, ask = t.get("bid"), t.get("ask")
    try:
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
    except Exception:
        pass
    raise ValueError("Ticker sans prix exploitable")

def place_market_buy(exchange, symbol: str, usdt_amount: float, slip_limit_pct: Optional[float] = None):
    """
    Achat market pour un budget en USDT.
    - Anti-slippage : check (ask - last)/last vs slip_limit_pct
    - Capé au solde USDT 'free'
    - Respecte min_amount / min_cost du marché
    """
    market = exchange.market(symbol)

    # Ticker + pré-check slippage
    ticker = with_retry(exchange.fetch_ticker, 3, 1, symbol)
    last = best_last_from_ticker(ticker)
    ask = ticker.get("ask")
    try:
        ask = float(ask) if ask is not None else last
    except Exception:
        ask = last

    if slip_limit_pct is not None and slip_limit_pct > 0 and last > 0:
        pre_slip = (ask - last) / last * 100.0
        if pre_slip > slip_limit_pct:
            log.info(f"[BUY-SKIP] Anti-slippage {symbol}: {pre_slip:.2f}% > {slip_limit_pct:.2f}%")
            return {"skipped": True, "reason": "anti_slippage", "pre_slip_pct": round(pre_slip, 4), "limit_pct": slip_limit_pct}

    # Solde USDT et clamp du montant
    bal = with_retry(exchange.fetch_balance, 3, 1)
    usdt_free = float((bal.get("USDT") or {}).get("free", 0.0))
    usdt_amount = max(0.0, min(usdt_amount, usdt_free * 0.99))
    if usdt_amount <= 0:
        return {"skipped": True, "reason": "no_budget"}

    # Quantité & minimas
    amount = usdt_amount / last if last > 0 else 0.0
    amount_prec = float(exchange.amount_to_precision(symbol, amount))
    if amount_prec <= 0:
        return {"skipped": True, "reason": "amount_zero"}

    limits = market.get("limits") or {}
    min_amt  = float((limits.get("amount") or {}).get("min", 0) or 0)
    min_cost = float((limits.get("cost")   or {}).get("min", 0) or 0)
    est_cost = amount_prec * last

    if min_amt and amount_prec < min_amt:
        return {"skipped": True, "reason": "amount_too_small", "amount": amount_prec, "min_amount": min_amt}
    if min_cost and est_cost < min_cost:
        return {"skipped": True, "reason": "cost_too_small", "est_cost": est_cost, "min_cost": min_cost}

    log.info(f"[ORDER] BUY {symbol} amount={amount_prec} usdt~={usdt_amount:.4f} (slip_limit={slip_limit_pct})")
    return with_retry(exchange.create_order, 3, 1, symbol, "market", "buy", amount_prec)

def place_market_sell_all(exchange, symbol: str, slip_limit_pct: Optional[float] = None):
    """
    Vente market de TOUT le solde base disponible.
    - Anti-slippage : check (last - bid)/last vs slip_limit_pct
    - Respecte min_amount / min_cost
    """
    market = exchange.market(symbol)

    ticker = with_retry(exchange.fetch_ticker, 3, 1, symbol)
    last = best_last_from_ticker(ticker)
    bid = ticker.get("bid")
    try:
        bid = float(bid) if bid is not None else last
    except Exception:
        bid = last

    if slip_limit_pct is not None and slip_limit_pct > 0 and last > 0:
        pre_slip = (last - bid) / last * 100.0
        if pre_slip > slip_limit_pct:
            log.info(f"[SELL-SKIP] Anti-slippage {symbol}: {pre_slip:.2f}% > {slip_limit_pct:.2f}%")
            return {"skipped": True, "reason": "anti_slippage_sell", "pre_slip_pct": round(pre_slip, 4), "limit_pct": slip_limit_pct}

    base_ccy = market.get("base")
    bal = with_retry(exchange.fetch_balance, 3, 1)
    free_base = float((bal.get(base_ccy) or {}).get("free", 0.0))
    if free_base <= 0:
        return {"skipped": True, "reason": "no_base_balance", "symbol": symbol, "base": base_ccy}

    amount_prec = float(exchange.amount_to_precision(symbol, free_base))

    limits = market.get("limits") or {}
    min_amt  = float((limits.get("amount") or {}).get("min", 0) or 0)
    min_cost = float((limits.get("cost")   or {}).get("min", 0) or 0)
    est_cost = amount_prec * last

    if min_amt and amount_prec < min_amt:
        return {"skipped": True, "reason": "amount_too_small", "symbol": symbol}
    if min_cost and est_cost < min_cost:
        return {"skipped": True, "reason": "cost_too_small", "symbol": symbol}

    log.info(f"[ORDER] SELL {symbol} amount={amount_prec} (liquidation)")
    return with_retry(exchange.create_order, 3, 1, symbol, "market", "sell", amount_prec)
