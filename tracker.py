"""
Tracker - Phase 1 Data Collection
Saves every signal to /data/signals.json (persistent Railway volume)
Tracks paper trade performance with extended time windows
"""

import json
import os
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DATA_FILE = '/data/signals.json'
PAPER_BUY = 10.0


def _load() -> list:
    if not os.path.exists(DATA_FILE):
        os.makedirs('/data', exist_ok=True)
        return []
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def _save(data: list):
    os.makedirs('/data', exist_ok=True)
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def save_signal(signal: dict, score: dict):
    data = _load()
    entry = {
        'id':                 len(data) + 1,
        'ca':                 signal.get('ca', ''),
        'name':               signal.get('name', 'Unknown'),
        'timestamp':          signal.get('timestamp', datetime.now(timezone.utc).isoformat()),
        'marketcap':          signal.get('marketcap', 0),
        'age_minutes':        signal.get('age_minutes', 0),
        'holders':            signal.get('holders', 0),
        'top10_pct':          signal.get('top10_pct', 0),
        'volume':             signal.get('volume', 0),
        'liquidity':          signal.get('liquidity', 0),
        'bonding_curve':      signal.get('bonding_curve', 0),
        'dev_pct':            signal.get('dev_pct', 0),
        'twitter_url':        signal.get('twitter_url', ''),
        'website_url':        signal.get('website_url', ''),
        'telegram_url':       signal.get('telegram_url', ''),
        'has_twitter':        bool(signal.get('twitter_url')),
        'has_website':        bool(signal.get('website_url')),
        'has_telegram':       bool(signal.get('telegram_url')),
        'telegram_members':   signal.get('telegram_members', 0),
        'pumpfun_replies':    signal.get('pumpfun_replies', 0),
        'score_total':        score.get('total', 0),
        'red_flags':          score.get('red_flags', []),
        'paper_buy':          PAPER_BUY,
        'paper_entry_mcap':   signal.get('marketcap', 0),
        'current_mcap':       signal.get('marketcap', 0),
        'peak_mcap':          signal.get('marketcap', 0),
        'peak_multiplier':    1.0,
        'current_multiplier': 1.0,
        'hit_2x':             False,
        'hit_5x':             False,
        'hit_10x':            False,
        'hit_20x':            False,
        'hit_50x':            False,
        'rugged':             False,
        'active':             True,
        'paper_pnl':          0.0,
        # Track when each milestone was hit (minutes after signal)
        'time_to_2x':         None,
        'time_to_5x':         None,
        'time_to_10x':        None,
        'price_history':      [],
    }
    data.append(entry)
    _save(data)
    log.info(f"Saved signal #{entry['id']}: {entry['name']}")
    return entry['id']


def update_price(ca: str, current_mcap: float, multiplier: float):
    data = _load()
    for sig in data:
        if sig['ca'] == ca and sig['active']:
            sig['current_mcap']        = current_mcap
            sig['current_multiplier']  = round(multiplier, 3)

            if multiplier > sig.get('peak_multiplier', 1.0):
                sig['peak_mcap']       = current_mcap
                sig['peak_multiplier'] = round(multiplier, 3)

            # Calculate minutes since signal
            try:
                entry_time   = datetime.fromisoformat(sig['timestamp'])
                mins_elapsed = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60
            except Exception:
                mins_elapsed = 0

            # Record time to milestone
            if multiplier >= 2  and not sig['hit_2x']:
                sig['hit_2x']    = True
                sig['time_to_2x'] = round(mins_elapsed)
            if multiplier >= 5  and not sig['hit_5x']:
                sig['hit_5x']    = True
                sig['time_to_5x'] = round(mins_elapsed)
            if multiplier >= 10 and not sig['hit_10x']:
                sig['hit_10x']   = True
                sig['time_to_10x'] = round(mins_elapsed)
            if multiplier >= 20 and not sig['hit_20x']:
                sig['hit_20x']   = True
            if multiplier >= 50 and not sig['hit_50x']:
                sig['hit_50x']   = True

            # Rug detection: dropped 80%+ from peak
            peak = sig.get('peak_multiplier', 1.0)
            if peak > 1.5 and multiplier < peak * 0.2:
                sig['rugged'] = True
                sig['active'] = False

            # Extended time windows — mark inactive after 48 hours
            if mins_elapsed > 2880:  # 48 hours
                sig['active'] = False

            sig['paper_pnl'] = round((multiplier - 1) * PAPER_BUY, 2)

            sig['price_history'].append({
                'time':       datetime.now(timezone.utc).isoformat(),
                'mins':       round(mins_elapsed),
                'mcap':       current_mcap,
                'multiplier': round(multiplier, 3),
            })
            break

    _save(data)


def get_all_signals(active_only=False) -> list:
    data = _load()
    if active_only:
        return [s for s in data if s.get('active')]
    return data


def get_stats() -> dict:
    data = _load()
    if not data:
        return {
            'total': 0, 'hit_2x': 0, 'hit_5x': 0, 'hit_10x': 0,
            'rate_2x': 0, 'rate_5x': 0, 'rate_10x': 0,
            'rugged': 0, 'paper_profit': 0, 'best_pick': 'None yet',
            'avg_time_to_2x': 'N/A',
        }

    total   = len(data)
    hit_2x  = [s for s in data if s['hit_2x']]
    hit_5x  = [s for s in data if s['hit_5x']]
    hit_10x = [s for s in data if s['hit_10x']]
    rugged  = len([s for s in data if s['rugged']])
    profit  = sum(s['paper_pnl'] for s in data)

    best    = max(data, key=lambda s: s.get('peak_multiplier', 1), default=None)
    best_str = f"{best['name']} ({best['peak_multiplier']}x)" if best else 'None'

    # Average time to 2x
    times_to_2x = [s['time_to_2x'] for s in hit_2x if s.get('time_to_2x')]
    avg_2x = f"{round(sum(times_to_2x)/len(times_to_2x))}m" if times_to_2x else 'N/A'

    def rate(n):
        return round((len(n) / total * 100), 1) if total > 0 else 0

    return {
        'total':         total,
        'hit_2x':        len(hit_2x),
        'hit_5x':        len(hit_5x),
        'hit_10x':       len(hit_10x),
        'rate_2x':       rate(hit_2x),
        'rate_5x':       rate(hit_5x),
        'rate_10x':      rate(hit_10x),
        'rugged':        rugged,
        'paper_profit':  round(profit, 2),
        'best_pick':     best_str,
        'avg_time_to_2x': avg_2x,
    }
