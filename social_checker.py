"""
Social Checker
Checks Twitter/X presence, website liveness, and community activity.
Returns a social score (0-25) and detailed social data.
"""

import asyncio
import aiohttp
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

async def check_url_live(session: aiohttp.ClientSession, url: str) -> bool:
    """Returns True if URL responds with 200."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6), allow_redirects=True) as r:
            return r.status == 200
    except Exception:
        return False


async def check_twitter(session: aiohttp.ClientSession, twitter_url: str) -> dict:
    """
    Check if Twitter/X account exists and is active.
    Uses nitter as a proxy to avoid auth requirements.
    """
    result = {
        'has_twitter': False,
        'twitter_followers': 0,
        'tweet_count': 0,
        'account_age_days': 0,
        'last_tweet_hours_ago': 999,
        'is_active': False,
    }

    try:
        # Extract username
        username = re.search(r'(?:twitter|x)\.com/([A-Za-z0-9_]+)', twitter_url)
        if not username:
            return result
        handle = username.group(1)

        # Try nitter instances (public, no auth needed)
        nitter_instances = [
            f"https://nitter.net/{handle}",
            f"https://nitter.privacydev.net/{handle}",
            f"https://nitter.poast.org/{handle}",
        ]

        html = None
        for nitter_url in nitter_instances:
            try:
                async with session.get(
                    nitter_url,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={'User-Agent': 'Mozilla/5.0'}
                ) as r:
                    if r.status == 200:
                        html = await r.text()
                        break
            except Exception:
                continue

        if not html or 'User not found' in html or 'account doesn' in html.lower():
            return result

        result['has_twitter'] = True

        # Extract follower count
        followers_match = re.search(r'(\d[\d,]*)\s*Followers', html)
        if followers_match:
            result['twitter_followers'] = int(followers_match.group(1).replace(',', ''))

        # Extract tweet count
        tweets_match = re.search(r'(\d[\d,]*)\s*(?:Tweets|Posts)', html)
        if tweets_match:
            result['tweet_count'] = int(tweets_match.group(1).replace(',', ''))

        # Is it active? (has tweets and followers)
        result['is_active'] = result['twitter_followers'] > 0 or result['tweet_count'] > 0

    except Exception as e:
        log.warning(f"Twitter check failed for {twitter_url}: {e}")

    return result


async def check_pumpfun_data(session: aiohttp.ClientSession, ca: str) -> dict:
    """Fetch live pump.fun data for the token."""
    result = {
        'pumpfun_live': False,
        'reply_count': 0,
        'king_of_hill': False,
    }
    try:
        url = f"https://frontend-api.pump.fun/coins/{ca}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json()
                result['pumpfun_live']  = True
                result['reply_count']   = d.get('reply_count', 0)
                result['king_of_hill']  = d.get('is_currently_live', False)
                result['total_supply']  = d.get('total_supply', 0)
                result['description']   = d.get('description', '')
                # Check if description has substance
                desc = result['description']
                result['has_description'] = len(desc) > 20 if desc else False
    except Exception as e:
        log.warning(f"pump.fun check failed for {ca}: {e}")
    return result


def score_socials(twitter: dict, website_live: bool, pumpfun: dict) -> int:
    """Convert social data into a score 0-25."""
    score = 0

    # Twitter presence (0-12)
    if twitter.get('has_twitter'):
        score += 4
        followers = twitter.get('twitter_followers', 0)
        if followers >= 1000:   score += 5
        elif followers >= 500:  score += 4
        elif followers >= 100:  score += 3
        elif followers >= 10:   score += 2
        else:                   score += 1

        if twitter.get('is_active'):
            score += 3

    # Website (0-5)
    if website_live:
        score += 5

    # pump.fun community (0-8)
    if pumpfun.get('pumpfun_live'):
        replies = pumpfun.get('reply_count', 0)
        if replies >= 50:       score += 5
        elif replies >= 20:     score += 4
        elif replies >= 10:     score += 3
        elif replies >= 5:      score += 2
        elif replies >= 1:      score += 1

        if pumpfun.get('has_description'):
            score += 2

        if pumpfun.get('king_of_hill'):
            score += 1

    return min(score, 25)


# ── Main Entry Point ──────────────────────────────────────────────────────────

async def check_socials(signal: dict) -> dict:
    """
    Run all social checks concurrently.
    Returns combined social data + score.
    """
    ca           = signal.get('ca', '')
    twitter_url  = signal.get('twitter_url', '')
    website_url  = signal.get('website_url', '')

    async with aiohttp.ClientSession() as session:
        tasks = [
            check_twitter(session, twitter_url) if twitter_url else asyncio.sleep(0),
            check_url_live(session, website_url) if website_url else asyncio.sleep(0),
            check_pumpfun_data(session, ca) if ca else asyncio.sleep(0),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

    twitter_data = results[0] if isinstance(results[0], dict) else {
        'has_twitter': bool(twitter_url), 'twitter_followers': 0,
        'tweet_count': 0, 'is_active': False
    }
    website_live = results[1] if isinstance(results[1], bool) else False
    pumpfun_data = results[2] if isinstance(results[2], dict) else {'pumpfun_live': False, 'reply_count': 0}

    social_score = score_socials(twitter_data, website_live, pumpfun_data)

    return {
        'score':              social_score,
        'has_twitter':        twitter_data.get('has_twitter', False),
        'twitter_followers':  twitter_data.get('twitter_followers', 0),
        'tweet_count':        twitter_data.get('tweet_count', 0),
        'twitter_active':     twitter_data.get('is_active', False),
        'website_live':       website_live,
        'pumpfun_replies':    pumpfun_data.get('reply_count', 0),
        'has_description':    pumpfun_data.get('has_description', False),
        'king_of_hill':       pumpfun_data.get('king_of_hill', False),
    }
