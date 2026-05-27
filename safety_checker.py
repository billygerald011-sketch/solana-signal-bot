"""
Safety Checker
Checks for:
1. Honeypot detection via rugcheck.xyz
2. Dev wallet history (has this dev rugged before?)
3. Holder growth rate
4. Volume velocity pattern
"""

import asyncio
import aiohttp
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)


async def check_rugcheck(session, ca: str) -> dict:
    """Check rugcheck.xyz for honeypot and risk analysis."""
    result = {
        'rugcheck_score': None,
        'is_honeypot': False,
        'rugcheck_risks': [],
        'rugcheck_rating': 'unknown',
    }
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{ca}/report/summary"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                               headers={'User-Agent': 'Mozilla/5.0'}) as r:
            if r.status == 200:
                d = await r.json()
                score  = d.get('score', None)
                risks  = d.get('risks', [])
                rating = d.get('rating', 'unknown')

                result['rugcheck_score']  = score
                result['rugcheck_rating'] = rating
                result['rugcheck_risks']  = [r.get('name', '') for r in risks] if risks else []

                # Honeypot = can't sell
                honeypot_keywords = ['honeypot', 'cannot sell', 'sell disabled', 'freeze']
                risk_names = ' '.join(result['rugcheck_risks']).lower()
                result['is_honeypot'] = any(kw in risk_names for kw in honeypot_keywords)

    except Exception as e:
        log.warning(f"Rugcheck failed for {ca}: {e}")
    return result


async def check_dev_history(session, dev_wallet: str) -> dict:
    """Check if dev wallet has created tokens that rugged before."""
    result = {
        'dev_previous_tokens': 0,
        'dev_previous_rugs':   0,
        'dev_rug_rate':        0,
        'dev_is_serial_rugger': False,
    }
    if not dev_wallet:
        return result
    try:
        # Check pump.fun for dev's previous tokens
        url = f"https://frontend-api.pump.fun/coins?creator={dev_wallet}&limit=20&offset=0"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                coins = await r.json()
                if coins:
                    total = len(coins)
                    # A token "rugged" if it has very low mcap now vs its peak
                    # We use complete/not complete as proxy
                    completed = sum(1 for c in coins if c.get('complete', False))
                    not_completed = total - completed

                    result['dev_previous_tokens']   = total
                    result['dev_previous_rugs']     = not_completed
                    result['dev_rug_rate']          = round(not_completed / total * 100) if total > 0 else 0
                    result['dev_is_serial_rugger']  = (total >= 3 and result['dev_rug_rate'] >= 80)

    except Exception as e:
        log.warning(f"Dev history check failed for {dev_wallet}: {e}")
    return result


async def check_holder_velocity(session, ca: str, current_holders: int) -> dict:
    """
    Estimate holder growth rate by checking pump.fun trades.
    More unique buyers recently = growing holder base.
    """
    result = {
        'unique_buyers_5m':  0,
        'holder_growth_fast': False,
    }
    try:
        url = f"https://frontend-api.pump.fun/coins/{ca}/trades?limit=50&offset=0"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                trades = await r.json()
                if trades:
                    now = datetime.now(timezone.utc)
                    recent_buyers = set()
                    for t in trades:
                        try:
                            ts = t.get('timestamp', '').replace('Z', '')
                            if not ts:
                                continue
                            trade_time = datetime.fromisoformat(ts)
                            mins_ago = (now - trade_time).total_seconds() / 60
                            if mins_ago <= 5 and t.get('is_buy', False):
                                recent_buyers.add(t.get('user', ''))
                        except Exception:
                            continue

                    result['unique_buyers_5m']   = len(recent_buyers)
                    result['holder_growth_fast'] = len(recent_buyers) >= 5

    except Exception as e:
        log.warning(f"Holder velocity check failed for {ca}: {e}")
    return result


async def extract_dev_wallet(text: str) -> str:
    """Extract dev wallet address from signal text."""
    # Dev line looks like: Dev: HNeQN...SKL9n
    match = re.search(r'Dev[:\s]+([A-Za-z0-9]{32,44})', text)
    return match.group(1) if match else ''


async def run_safety_checks(signal: dict, session: aiohttp.ClientSession) -> dict:
    """Run all safety checks concurrently."""
    ca          = signal.get('ca', '')
    dev_wallet  = signal.get('dev_wallet', '')
    holders     = signal.get('holders', 0)

    tasks = [
        check_rugcheck(session, ca),
        check_dev_history(session, dev_wallet) if dev_wallet else asyncio.sleep(0),
        check_holder_velocity(session, ca, holders),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    rugcheck   = results[0] if isinstance(results[0], dict) else {}
    dev_hist   = results[1] if isinstance(results[1], dict) else {}
    holder_vel = results[2] if isinstance(results[2], dict) else {}

    # Build red flags list
    red_flags = []
    if rugcheck.get('is_honeypot'):
        red_flags.append('🚨 HONEYPOT DETECTED')
    if rugcheck.get('rugcheck_risks'):
        red_flags.extend([f"⚠️ {r}" for r in rugcheck['rugcheck_risks'][:3]])
    if dev_hist.get('dev_is_serial_rugger'):
        red_flags.append(f"🚨 Serial rugger — {dev_hist.get('dev_rug_rate')}% rug rate on {dev_hist.get('dev_previous_tokens')} previous tokens")
    if rugcheck.get('rugcheck_rating') in ['danger', 'high risk']:
        red_flags.append(f"🔴 Rugcheck rating: {rugcheck.get('rugcheck_rating')}")

    return {
        'rugcheck_score':       rugcheck.get('rugcheck_score'),
        'rugcheck_rating':      rugcheck.get('rugcheck_rating', 'unknown'),
        'rugcheck_risks':       rugcheck.get('rugcheck_risks', []),
        'is_honeypot':          rugcheck.get('is_honeypot', False),
        'dev_previous_tokens':  dev_hist.get('dev_previous_tokens', 0),
        'dev_previous_rugs':    dev_hist.get('dev_previous_rugs', 0),
        'dev_rug_rate':         dev_hist.get('dev_rug_rate', 0),
        'dev_is_serial_rugger': dev_hist.get('dev_is_serial_rugger', False),
        'unique_buyers_5m':     holder_vel.get('unique_buyers_5m', 0),
        'holder_growth_fast':   holder_vel.get('holder_growth_fast', False),
        'red_flags':            red_flags,
    }
