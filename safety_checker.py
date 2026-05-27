"""
Safety Checker
For pump.fun tokens:
- NO rugcheck (returns unknown for all new pump.fun tokens — waste of time)
- Dev wallet history via pump.fun API (best rug signal available)
- Holder velocity via Helius RPC (bundler detection)
- pump.fun API for socials and reply count (instant, reliable)
"""

import asyncio
import aiohttp
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

PUMP_API_HEADERS = {"User-Agent": "Mozilla/5.0"}


async def check_pump_token_data(session: aiohttp.ClientSession, ca: str) -> dict:
    """
    Primary safety + social source for pump.fun tokens.
    Gemini confirmed: pump.fun frontend API indexes instantly on creation.
    We add a browser User-Agent to avoid Cloudflare blocks.
    """
    result = {
        'pumpfun_ok':      False,
        'reply_count':     0,
        'dev_wallet':      '',
        'usd_market_cap':  0,
        'has_twitter':     False,
        'has_telegram':    False,
        'has_website':     False,
        'twitter_url':     '',
        'telegram_url':    '',
        'website_url':     '',
        'king_of_hill':    False,
        'has_description': False,
    }
    try:
        url = f"https://frontend-api.pump.fun/coins/{ca}"
        async with session.get(
            url,
            headers=PUMP_API_HEADERS,
            timeout=aiohttp.ClientTimeout(total=8)
        ) as r:
            if r.status == 200:
                d = await r.json()
                twitter  = d.get('twitter', '')  or ''
                telegram = d.get('telegram', '') or ''
                website  = d.get('website', '')  or ''

                # Normalize URLs
                if twitter  and not twitter.startswith('http'):
                    twitter  = f"https://x.com/{twitter.lstrip('@')}"
                if telegram and not telegram.startswith('http'):
                    telegram = f"https://t.me/{telegram.lstrip('@')}"
                if website  and not website.startswith('http'):
                    website  = f"https://{website}"

                result.update({
                    'pumpfun_ok':      True,
                    'reply_count':     d.get('reply_count', 0),
                    'dev_wallet':      d.get('creator', ''),
                    'usd_market_cap':  d.get('usd_market_cap', 0),
                    'has_twitter':     bool(twitter),
                    'has_telegram':    bool(telegram),
                    'has_website':     bool(website),
                    'twitter_url':     twitter,
                    'telegram_url':    telegram,
                    'website_url':     website,
                    'king_of_hill':    d.get('is_currently_live', False),
                    'has_description': len(d.get('description', '') or '') > 20,
                })
    except Exception as e:
        log.warning(f"pump.fun API check failed for {ca}: {e}")
    return result


async def check_dev_history(session: aiohttp.ClientSession, dev_wallet: str) -> dict:
    """Check dev wallet's previous tokens on pump.fun."""
    result = {
        'dev_previous_tokens':  0,
        'dev_previous_rugs':    0,
        'dev_rug_rate':         0,
        'dev_is_serial_rugger': False,
    }
    if not dev_wallet:
        return result
    try:
        url = f"https://frontend-api.pump.fun/coins?creator={dev_wallet}&limit=20&offset=0"
        async with session.get(
            url,
            headers=PUMP_API_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status == 200:
                coins = await r.json()
                if coins and isinstance(coins, list):
                    total     = len(coins)
                    completed = sum(1 for c in coins if c.get('complete', False))
                    not_done  = total - completed
                    rug_rate  = round(not_done / total * 100) if total > 0 else 0

                    result.update({
                        'dev_previous_tokens':  total,
                        'dev_previous_rugs':    not_done,
                        'dev_rug_rate':         rug_rate,
                        'dev_is_serial_rugger': total >= 3 and rug_rate >= 80,
                    })
    except Exception as e:
        log.warning(f"Dev history check failed for {dev_wallet}: {e}")
    return result


async def check_holder_velocity(session: aiohttp.ClientSession, ca: str) -> dict:
    """
    Detect bundled launches via Helius getTokenLargestAccounts.
    If top wallets hold unreasonable % it's likely a bundled sniper launch.
    """
    result = {
        'unique_buyers_5m':   0,
        'holder_growth_fast': False,
        'top_wallet_pct':     0,
        'looks_bundled':      False,
    }
    import os
    HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "2842e504-2c6d-41a1-b013-962ee1263e23")
    try:
        url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [ca]
        }
        async with session.post(url, json=payload,
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data    = await r.json()
                accounts = data.get('result', {}).get('value', [])
                if accounts:
                    total_supply = 1_000_000_000 * (10 ** 6)  # 1B tokens with 6 decimals
                    top_amount   = int(accounts[0].get('amount', 0)) if accounts else 0
                    top_pct      = round(top_amount / total_supply * 100, 1)
                    # Top 3 wallets combined
                    top3_amount  = sum(int(a.get('amount', 0)) for a in accounts[:3])
                    top3_pct     = round(top3_amount / total_supply * 100, 1)

                    result['top_wallet_pct']   = top_pct
                    result['looks_bundled']    = top3_pct >= 30  # top 3 hold 30%+ = suspicious
                    result['holder_growth_fast'] = len(accounts) >= 10
    except Exception as e:
        log.warning(f"Holder velocity check failed for {ca}: {e}")
    return result


async def run_safety_checks(signal: dict, session: aiohttp.ClientSession) -> dict:
    """Run all safety checks concurrently."""
    ca = signal.get('ca', '')

    # Run pump.fun data fetch + holder velocity concurrently
    pump_data, holder_data = await asyncio.gather(
        check_pump_token_data(session, ca),
        check_holder_velocity(session, ca),
        return_exceptions=True
    )

    pump_data   = pump_data   if isinstance(pump_data,   dict) else {}
    holder_data = holder_data if isinstance(holder_data, dict) else {}

    # Dev history needs dev wallet from pump data
    dev_wallet = pump_data.get('dev_wallet') or signal.get('dev_wallet', '')
    dev_hist   = await check_dev_history(session, dev_wallet)

    # Build red flags
    red_flags = []
    if dev_hist.get('dev_is_serial_rugger'):
        red_flags.append(f"🚨 Serial rugger — {dev_hist.get('dev_rug_rate')}% rug rate on {dev_hist.get('dev_previous_tokens')} tokens")
    if holder_data.get('looks_bundled'):
        red_flags.append(f"🚨 Possible bundled launch — top 3 wallets hold large %")
    if not pump_data.get('has_twitter') and not pump_data.get('has_telegram'):
        red_flags.append("⚠️ No social links on pump.fun")

    return {
        # pump.fun data
        'pumpfun_ok':           pump_data.get('pumpfun_ok', False),
        'reply_count':          pump_data.get('reply_count', 0),
        'dev_wallet':           dev_wallet,
        'king_of_hill':         pump_data.get('king_of_hill', False),
        'has_description':      pump_data.get('has_description', False),
        # Socials from pump.fun (most reliable source)
        'twitter_url':          pump_data.get('twitter_url', ''),
        'telegram_url':         pump_data.get('telegram_url', ''),
        'website_url':          pump_data.get('website_url', ''),
        'has_twitter':          pump_data.get('has_twitter', False),
        'has_telegram':         pump_data.get('has_telegram', False),
        'has_website':          pump_data.get('has_website', False),
        # Dev history
        'dev_previous_tokens':  dev_hist.get('dev_previous_tokens', 0),
        'dev_previous_rugs':    dev_hist.get('dev_previous_rugs', 0),
        'dev_rug_rate':         dev_hist.get('dev_rug_rate', 0),
        'dev_is_serial_rugger': dev_hist.get('dev_is_serial_rugger', False),
        # Holder/bundler check
        'top_wallet_pct':       holder_data.get('top_wallet_pct', 0),
        'looks_bundled':        holder_data.get('looks_bundled', False),
        'holder_growth_fast':   holder_data.get('holder_growth_fast', False),
        'unique_buyers_5m':     holder_data.get('unique_buyers_5m', 0),
        # Rugcheck removed — always unknown for pump.fun tokens
        'rugcheck_score':       None,
        'rugcheck_rating':      'n/a (pump.fun)',
        'rugcheck_risks':       [],
        'is_honeypot':          False,  # impossible on pump.fun by design
        'red_flags':            red_flags,
    }
