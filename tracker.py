"""
Tracker
Saves every signal to a JSON file and tracks paper trade performance.
This is our training data — every win and loss gets recorded.
"""

import json
import os
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DATA_FILE = 'signals.json'
PAPER_BUY = 10.0  # $10 paper trade per signal


# ── File Helpers ──────────────────────────────────────────────────────────────

def _load() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def _save(data: list):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ── Signal Management ─────────────────────────────────────────────────────────

def save_signal(signal: dict, score: dict):
    """Save a new signal with paper trade entry."""
    data = _load()

    entry = {
        'id':               len(data) + 1,
        'ca':               signal.get('ca', ''),
        'name':             signal.get('name', 'Unknown'),
        'timestamp':        signal.get('timestamp', datetime.utcnow().isoformat()),
        'marketcap':        signal.get('marketcap', 0),
        'age_minutes':      signal.get('age_minutes', 0),
        'holders':          signal.get('holders', 0),
        'top10_pct':        signal.get('top10_pct', 0),
        'volume':           signal.get('volume', 0),
        'liquidity':        signal.get('liquidity', 0),
        'bonding_curve':    signal.get('bonding_curve', 0),
        'dev_pct':          signal.get('dev_pct', 0),
        'twitter_url':      signal.get('twitter_url', ''),
        'website_url':      signal.get('website_url', ''),
        'score_onchain':    score.get('onchain', 0),
        'score_narrative':  score.get('narrative', 0),
        'score_social':     score.get('social', 0),
        'score_total':      score.get('total', 0),
        'red_flags':        score.get('red_flags', []),
        'paper_buy':        PAPER_BUY,
        'paper_entry_mcap': signal.get('marketcap', 0),
        'current_mcap':     signal.get('marketcap', 0),
        'peak_mcap':        signal.get('marketcap', 0),
        'peak_multiplier':  1.0,
        'current_multiplier': 1.0,
        'hit_2x':           False,
        'hit_5x':           False,
        'hit_10x':          False,
        'hit_20x':          False,
        'rugged':           False,
        'active':           True,
        'paper_pnl':        0.0,
        'price_history':    [],
    }

    data.append(entry)
    _save(data)
    log.info(f"Saved signal #{entry['id']}: {entry['name']} (score: {entry['score_total']})")
    return entry['id']


def update_price(ca: str, current_mcap: float, multiplier: float):
    """Update price for an active paper trade."""
    data = _load()
    updated = False

    for sig in data:
        if sig['ca'] == ca and sig['active']:
            sig['current_mcap']        = current_mcap
            sig['current_multiplier']  = round(multiplier, 3)

            # Update peak
            if multiplier > sig.get('peak_multiplier', 1.0):
                sig['peak_mcap']       = current_mcap
                sig['peak_multiplier'] = round(multiplier, 3)

            # Check milestones
            if multiplier >= 2  and not sig['hit_2x']:  sig['hit_2x']  = True
            if multiplier >= 5  and not sig['hit_5x']:  sig['hit_5x']  = True
            if multiplier >= 10 and not sig['hit_10x']: sig['hit_10x'] = True
            if multiplier >= 20 and not sig['hit_20x']: sig['hit_20x'] = True

            # Detect rug (dropped 80%+ from peak)
            peak = sig.get('peak_multiplier', 1.0)
            if peak > 1.5 and multiplier < peak * 0.2:
                sig['rugged']   = True
                sig['active']   = False

            # Mark inactive after 2 hours (120 mins) from signal
            entry_time = datetime.fromisoformat(sig['timestamp'])
            now        = datetime.utcnow()
            mins_elapsed = (now - entry_time).total_seconds() / 60
            if mins_elapsed > 120:
                sig['active'] = False

            # Calculate paper PnL
            sig['paper_pnl'] = round((multiplier - 1) * PAPER_BUY, 2)

            # Add to price history
            sig['price_history'].append({
                'time':       datetime.utcnow().isoformat(),
                'mcap':       current_mcap,
                'multiplier': round(multiplier, 3),
            })

            updated = True
            break

    if updated:
        _save(data)


def get_all_signals(active_only=False) -> list:
    """Get all signals, optionally filtered to active ones."""
    data = _load()
    if active_only:
        return [s for s in data if s.get('active')]
    return data


def get_stats() -> dict:
    """Calculate overall paper trading performance stats."""
    data = _load()
    if not data:
        return {
            'total': 0, 'qualified': 0,
            'hit_2x': 0, 'hit_5x': 0, 'hit_10x': 0,
            'rate_2x': 0, 'rate_5x': 0, 'rate_10x': 0,
            'rugged': 0, 'paper_profit': 0,
            'best_pick': 'None yet',
        }

    total      = len(data)
    qualified  = len([s for s in data if s['score_total'] >= 60])
    hit_2x     = len([s for s in data if s['hit_2x']])
    hit_5x     = len([s for s in data if s['hit_5x']])
    hit_10x    = len([s for s in data if s['hit_10x']])
    rugged     = len([s for s in data if s['rugged']])
    profit     = sum(s['paper_pnl'] for s in data)

    best = max(data, key=lambda s: s.get('peak_multiplier', 1), default=None)
    best_str = f"{best['name']} ({best['peak_multiplier']}x)" if best else 'None'

    def rate(n):
        return round((n / total * 100), 1) if total > 0 else 0

    return {
        'total':        total,
        'qualified':    qualified,
        'hit_2x':       hit_2x,
        'hit_5x':       hit_5x,
        'hit_10x':      hit_10x,
        'rate_2x':      rate(hit_2x),
        'rate_5x':      rate(hit_5x),
        'rate_10x':     rate(hit_10x),
        'rugged':       rugged,
        'paper_profit': round(profit, 2),
        'best_pick':    best_str,
    }


def get_todays_top_picks(n=5) -> list:
    """Return today's top N scored signals."""
    data = _load()
    today = datetime.utcnow().date().isoformat()
    todays = [s for s in data if s['timestamp'].startswith(today)]
    return sorted(todays, key=lambda s: s['score_total'], reverse=True)[:n]
