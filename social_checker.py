"""
Social Checker - Phase 1
Gets social links from pump.fun API first (most reliable),
then falls back to parsing signal message text.
Checks Twitter, website, pump.fun replies, and Telegram group activity.
"""

import asyncio
import aiohttp
import logging
import re

log = logging.getLogger(__name__)


async def check_url_live(session, url: str) -> bool:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6), allow_redirects=True) as r:
            return r.status == 200
    except Exception:
        return False


async def check_twitter(session, twitter_url: str) -> dict:
    result = {
        'has_twitter': False,
        'twitter_followers': 0,
        'tweet_count': 0,
        'is_active': False,
    }
    if not twitter_url:
        return result
    try:
        username = re.search(r'(?:twitter|x)\.com/([A-Za-z0-9_]+)', twitter_url)
        if not username:
            return result
        handle = username.group(1)

        nitter_instances = [
            f"https://nitter.net/{handle}",
            f"https://nitter.privacydev.net/{handle}",
            f"https://nitter.poast.org/{handle}",
        ]
        html = None
        for url in nitter_instances:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8),
                                       headers={'User-Agent': 'Mozilla/5.0'}) as r:
                    if r.status == 200:
                        html = await r.text()
                        break
            except Exception:
                continue

        if not html or 'User not found' in html:
            return result

        result['has_twitter'] = True
        followers = re.search(r'(\d[\d,]*)\s*Followers', html)
        tweets    = re.search(r'(\d[\d,]*)\s*(?:Tweets|Posts)', html)
        if followers:
            result['twitter_followers'] = int(followers.group(1).replace(',', ''))
        if tweets:
            result['tweet_count'] = int(tweets.group(1).replace(',', ''))
        result['is_active'] = result['twitter_followers'] > 0 or result['tweet_count'] > 0

    except Exception as e:
        log.warning(f"Twitter check failed: {e}")
    return result


async def get_pumpfun_data(session, ca: str) -> dict:
    """
    Fetch full token data from pump.fun API.
    This is our PRIMARY source for social links —
    pump.fun stores twitter, telegram, website directly.
    """
    result = {
        'pumpfun_live':    False,
        'reply_count':     0,
        'has_description': False,
        'king_of_hill':    False,
        'twitter_url':     '',
        'telegram_url':    '',
        'website_url':     '',
    }
    if not ca:
        return result
    try:
        url = f"https://frontend-api.pump.fun/coins/{ca}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json()
                result['pumpfun_live']    = True
                result['reply_count']     = d.get('reply_count', 0)
                result['king_of_hill']    = d.get('is_currently_live', False)
                result['has_description'] = len(d.get('description', '') or '') > 20

                # ── Social links from pump.fun (most reliable source) ──
                twitter  = d.get('twitter', '')  or ''
                telegram = d.get('telegram', '') or ''
                website  = d.get('website', '')  or ''

                # Normalize Twitter URL
                if twitter:
                    if not twitter.startswith('http'):
                        twitter = f"https://x.com/{twitter.lstrip('@')}"
                    result['twitter_url'] = twitter

                # Normalize Telegram URL
                if telegram:
                    if not telegram.startswith('http'):
                        telegram = f"https://t.me/{telegram.lstrip('@')}"
                    result['telegram_url'] = telegram

                # Normalize website URL
                if website:
                    if not website.startswith('http'):
                        website = f"https://{website}"
                    result['website_url'] = website

    except Exception as e:
        log.warning(f"pump.fun data fetch failed for {ca}: {e}")
    return result


async def check_telegram_group(client, telegram_url: str) -> dict:
    """Use Telethon client to check Telegram group member count."""
    result = {'telegram_members': 0, 'telegram_active': False}
    if not telegram_url:
        return result
    try:
        match = re.search(r't\.me/([A-Za-z0-9_]+)', telegram_url)
        if not match:
            return result
        username = match.group(1)
        entity = await client.get_entity(username)
        if hasattr(entity, 'participants_count') and entity.participants_count:
            result['telegram_members'] = entity.participants_count
            result['telegram_active']  = entity.participants_count > 50
    except Exception as e:
        log.warning(f"Telegram group check failed for {telegram_url}: {e}")
    return result


async def check_socials(signal: dict, tg_client=None) -> dict:
    """
    Run all social checks.
    Priority for links: pump.fun API > signal message text
    """
    ca = signal.get('ca', '')

    async with aiohttp.ClientSession() as session:
        # Always fetch pump.fun data first — it has the most reliable social links
        pumpfun_data = await get_pumpfun_data(session, ca)

    # Merge links: pump.fun takes priority, fall back to signal message text
    twitter_url  = pumpfun_data.get('twitter_url')  or signal.get('twitter_url', '')
    telegram_url = pumpfun_data.get('telegram_url') or signal.get('telegram_url', '')
    website_url  = pumpfun_data.get('website_url')  or signal.get('website_url', '')

    log.info(f"Socials for {signal.get('name')}: twitter={bool(twitter_url)} tg={bool(telegram_url)} web={bool(website_url)}")

    async with aiohttp.ClientSession() as session:
        twitter_task = check_twitter(session, twitter_url)
        website_task = check_url_live(session, website_url) if website_url else asyncio.sleep(0)

        twitter_data, website_live = await asyncio.gather(
            twitter_task, website_task,
            return_exceptions=True
        )

    twitter_data = twitter_data if isinstance(twitter_data, dict) else {
        'has_twitter': False, 'twitter_followers': 0,
        'tweet_count': 0, 'is_active': False
    }
    website_live = website_live if isinstance(website_live, bool) else False

    # Check Telegram group members if we have a client
    tg_data = {'telegram_members': 0, 'telegram_active': False}
    if tg_client and telegram_url:
        tg_data = await check_telegram_group(tg_client, telegram_url)

    return {
        # Links (merged from pump.fun + message)
        'twitter_url':       twitter_url,
        'telegram_url':      telegram_url,
        'website_url':       website_url,
        # Twitter stats
        'has_twitter':       twitter_data.get('has_twitter', False),
        'twitter_followers': twitter_data.get('twitter_followers', 0),
        'tweet_count':       twitter_data.get('tweet_count', 0),
        'twitter_active':    twitter_data.get('is_active', False),
        # Website
        'website_live':      website_live,
        # pump.fun
        'pumpfun_replies':   pumpfun_data.get('reply_count', 0),
        'has_description':   pumpfun_data.get('has_description', False),
        'king_of_hill':      pumpfun_data.get('king_of_hill', False),
        # Telegram
        'telegram_members':  tg_data.get('telegram_members', 0),
        'telegram_active':   tg_data.get('telegram_active', False),
    }
