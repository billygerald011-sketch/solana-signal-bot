"""
Social Checker
Per Gemini's advice:
- Get socials directly from safety_checker's pump.fun API call (already done there)
- Skip Nitter scraping — too slow, too unreliable
- Skip pytrends — gets blocked quickly
- Just check if website URL is live (fast, reliable)
- Check Telegram group member count via Telethon
"""

import asyncio
import aiohttp
import logging
import re

log = logging.getLogger(__name__)


async def check_url_live(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=6),
            allow_redirects=True
        ) as r:
            return r.status == 200
    except Exception:
        return False


async def check_telegram_group(client, telegram_url: str) -> dict:
    """Use Telethon to check Telegram group member count."""
    result = {'telegram_members': 0, 'telegram_active': False}
    if not telegram_url:
        return result
    try:
        match = re.search(r't\.me/([A-Za-z0-9_]+)', telegram_url)
        if not match:
            return result
        username = match.group(1)
        entity   = await client.get_entity(username)
        if hasattr(entity, 'participants_count') and entity.participants_count:
            result['telegram_members'] = entity.participants_count
            result['telegram_active']  = entity.participants_count > 50
    except Exception as e:
        log.warning(f"Telegram group check failed for {telegram_url}: {e}")
    return result


async def check_socials(signal: dict, tg_client=None, safety_data: dict = None) -> dict:
    """
    Build social data.
    Primary source: safety_checker's pump.fun API data (passed in as safety_data).
    This avoids duplicate API calls and is faster.
    """
    # Use social links already fetched by safety_checker
    twitter_url  = ''
    telegram_url = ''
    website_url  = ''

    if safety_data:
        twitter_url  = safety_data.get('twitter_url', '')
        telegram_url = safety_data.get('telegram_url', '')
        website_url  = safety_data.get('website_url', '')

    # Fallback to signal message text if safety_data didn't have them
    twitter_url  = twitter_url  or signal.get('twitter_url', '')
    telegram_url = telegram_url or signal.get('telegram_url', '')
    website_url  = website_url  or signal.get('website_url', '')

    # Only check website liveness and Telegram members (fast, reliable)
    tasks = []
    if website_url:
        async with aiohttp.ClientSession() as session:
            website_live = await check_url_live(session, website_url)
    else:
        website_live = False

    # Check Telegram group members if we have a client
    tg_data = {'telegram_members': 0, 'telegram_active': False}
    if tg_client and telegram_url:
        tg_data = await check_telegram_group(tg_client, telegram_url)

    return {
        'twitter_url':       twitter_url,
        'telegram_url':      telegram_url,
        'website_url':       website_url,
        'has_twitter':       bool(twitter_url),
        'has_website':       bool(website_url),
        'has_telegram':      bool(telegram_url),
        'website_live':      website_live,
        'twitter_followers': 0,  # skipping Nitter — too slow/unreliable
        'tweet_count':       0,
        'twitter_active':    bool(twitter_url),  # presence = active enough
        'telegram_members':  tg_data.get('telegram_members', 0),
        'telegram_active':   tg_data.get('telegram_active', False),
        'pumpfun_replies':   safety_data.get('reply_count', 0) if safety_data else 0,
    }
