import asyncio
import re
import json
import os
import logging
import aiohttp
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telegram import Bot
from telegram.constants import ParseMode
from scorer import score_signal
from tracker import save_signal, update_price, get_all_signals, get_stats
from social_checker import check_socials

# ── Config ────────────────────────────────────────────────────────────────────
API_ID        = int(os.environ.get("API_ID", "22062932"))
API_HASH      = os.environ.get("API_HASH", "fa408cb00846e274bd4f79d219493923")
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8978544822:AAE3vbDBZeCYSNPjJnOrJTMLZ-Ue5_WMzWk")
OWNER_ID      = int(os.environ.get("OWNER_ID", "6514156935"))
CHANNEL       = os.environ.get("CHANNEL", "pumpfunvolumeby4AM")
MIN_SCORE      = int(os.environ.get("MIN_SCORE", "60"))
SESSION_STRING = os.environ.get("SESSION_STRING", "1BJWap1wBu6lWuGrvMz3YfdlyzqpKN-kjP2iG4zWB2XC_eeLjNKYMA61n0aZHynzwv75SFBayEywEbzSE3994Iiaxpunc3jSWnJM709w91TBHIMUExs_aMIjMxsJY5xNK12wigG80wRmEJUzZ5koDFg0HjGl28gsVo-MwSzwnZLGF0oQpLRsV97jHpv2z-vwyHiGZE-8cd72FdMdo2a8xzWz7QI1EYGhlzOjgKDYAJPra3i-E759-GJKfTW6evJyWFIaRaNszXwCANWV75O-7Mdh4uWJ-3uqcDp_2vbVQ7J9PMfKRdQsakeGRIKFlLpoAMuHHK_HNn9hdgveJkoK-zK6Zb3c8ANo=")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

alert_bot = Bot(token=BOT_TOKEN)

# ── Signal Parser ─────────────────────────────────────────────────────────────
def parse_signal(text: str) -> dict | None:
    """Extract all fields from a 4AM signal message."""
    try:
        data = {}

        # Token name & CA
        name_match = re.search(r'🔔\s*(.+?)\s*\|', text)
        ca_match   = re.search(r'([A-Za-z0-9]{32,44})pump', text)
        if name_match: data['name']    = name_match.group(1).strip()
        if ca_match:   data['ca']      = ca_match.group(1) + 'pump'

        # Marketcap
        mc = re.search(r'Marketcap[:\s]*\$([0-9,]+)', text)
        if mc: data['marketcap'] = int(mc.group(1).replace(',', ''))

        # Age
        age = re.search(r'Age[:\s]*(\d+)\s*(m|h|s)', text)
        if age:
            val, unit = int(age.group(1)), age.group(2)
            data['age_minutes'] = val if unit == 'm' else (val * 60 if unit == 'h' else val / 60)

        # Dev %
        dev = re.search(r'Dev[:\s].*?\(\s*💰\s*(\d+(?:\.\d+)?)%\)', text)
        if dev: data['dev_pct'] = float(dev.group(1))

        # Holders
        holders = re.search(r'Holders[:\s]*(\d+)', text)
        if holders: data['holders'] = int(holders.group(1))

        # Top 10 holders
        top10 = re.search(r'Top 10 holders[:\s]*(\d+(?:\.\d+)?)%', text)
        if top10: data['top10_pct'] = float(top10.group(1))

        # Volume
        vol = re.search(r'Volume[:\s]*\$([0-9,]+)', text)
        if vol: data['volume'] = int(vol.group(1).replace(',', ''))

        # Liquidity
        liq = re.search(r'Liquidity[:\s]*\$([0-9,]+)', text)
        if liq: data['liquidity'] = int(liq.group(1).replace(',', ''))

        # Bonding curve
        bc = re.search(r'Bonding Curve[:\s]*(\d+(?:\.\d+)?)%', text)
        if bc: data['bonding_curve'] = float(bc.group(1))

        # Social links
        x_profile = re.search(r'(https?://(?:twitter|x)\.com/\S+)', text)
        website   = re.search(r'(https?://(?!t\.me|pump\.fun|twitter|x\.com)\S+\.\S+)', text)
        if x_profile: data['twitter_url'] = x_profile.group(1)
        if website:   data['website_url'] = website.group(1)

        # Must have at least CA and marketcap
        if 'ca' not in data or 'marketcap' not in data:
            return None

        data['raw_text']   = text
        data['timestamp']  = datetime.utcnow().isoformat()
        return data

    except Exception as e:
        log.error(f"Parse error: {e}")
        return None


# ── Alert Formatter ───────────────────────────────────────────────────────────
def format_alert(signal: dict, score: dict, socials: dict) -> str:
    grade = "🟢 STRONG" if score['total'] >= 75 else "🟡 DECENT" if score['total'] >= 60 else "🔴 WEAK"
    flags = "\n".join([f"  ⚠️ {f}" for f in score['red_flags']]) if score['red_flags'] else "  ✅ None"

    lines = [
        f"{'='*35}",
        f"🚨 *NEW SIGNAL — {grade} ({score['total']}/100)*",
        f"{'='*35}",
        f"🪙 *{signal.get('name','Unknown')}*",
        f"`{signal.get('ca','')}`",
        f"",
        f"📊 *ON-CHAIN*",
        f"  💰 Mcap: ${signal.get('marketcap',0):,}",
        f"  ⏱ Age: {signal.get('age_minutes',0):.0f}m",
        f"  👥 Holders: {signal.get('holders',0)}",
        f"  🏆 Top10: {signal.get('top10_pct',0)}%",
        f"  🚀 Volume: ${signal.get('volume',0):,}",
        f"  💧 Liquidity: ${signal.get('liquidity',0):,}",
        f"  📈 Bonding: {signal.get('bonding_curve',0)}%",
        f"  👨‍💻 Dev: {signal.get('dev_pct',0)}%",
        f"",
        f"🌐 *SOCIALS*",
        f"  Twitter: {'✅ ' + str(socials.get('twitter_followers','?')) + ' followers' if socials.get('has_twitter') else '❌ None'}",
        f"  Website: {'✅ Live' if socials.get('website_live') else '❌ None'}",
        f"  Tweets: {socials.get('tweet_count', '?')}",
        f"",
        f"🎯 *SCORE BREAKDOWN*",
        f"  On-chain:  {score['onchain']}/35",
        f"  Socials:   {score['social']}/25",
        f"  Narrative: {score['narrative']}/20",
        f"  ──────────────",
        f"  *TOTAL: {score['total']}/100*",
        f"",
        f"🚩 *RED FLAGS*",
        f"{flags}",
        f"",
        f"📝 *PAPER TRADE*",
        f"  Entry Mcap: ${signal.get('marketcap',0):,}",
        f"  Paper Buy: $10",
        f"  Target 2x: ${signal.get('marketcap',0)*2:,} mcap",
        f"",
        f"🔗 [pump.fun](https://pump.fun/{signal.get('ca','')})",
    ]
    return "\n".join(lines)


# ── Price Checker ─────────────────────────────────────────────────────────────
async def check_prices_loop():
    """Every 5 minutes, update prices for all active paper trades."""
    CHECK_INTERVALS = [5, 15, 30, 60, 120]  # minutes
    while True:
        await asyncio.sleep(300)  # every 5 min
        signals = get_all_signals(active_only=True)
        for sig in signals:
            try:
                ca = sig['ca']
                async with aiohttp.ClientSession() as session:
                    url = f"https://frontend-api.pump.fun/coins/{ca}"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        if r.status == 200:
                            d = await r.json()
                            current_mcap = d.get('usd_market_cap', 0)
                            entry_mcap   = sig['marketcap']
                            if entry_mcap > 0:
                                multiplier = current_mcap / entry_mcap
                                update_price(ca, current_mcap, multiplier)
                                # Alert on milestones
                                prev = sig.get('peak_multiplier', 0)
                                for milestone in [2, 5, 10, 20, 50, 100]:
                                    if multiplier >= milestone and prev < milestone:
                                        await alert_bot.send_message(
                                            chat_id=OWNER_ID,
                                            text=f"🎯 *{sig['name']}* just hit *{milestone}x!*\n"
                                                 f"Entry: ${entry_mcap:,} → Now: ${current_mcap:,}\n"
                                                 f"`{ca}`",
                                            parse_mode=ParseMode.MARKDOWN
                                        )
            except Exception as e:
                log.warning(f"Price check failed for {sig.get('ca','?')}: {e}")


# ── Daily Summary ─────────────────────────────────────────────────────────────
async def daily_summary_loop():
    """Send daily performance summary."""
    while True:
        await asyncio.sleep(86400)  # every 24 hours
        stats = get_stats()
        msg = (
            f"📊 *DAILY SUMMARY*\n"
            f"{'='*30}\n"
            f"Signals caught: {stats['total']}\n"
            f"Scored 60+: {stats['qualified']}\n"
            f"Hit 2x: {stats['hit_2x']} ({stats['rate_2x']}%)\n"
            f"Hit 5x: {stats['hit_5x']} ({stats['rate_5x']}%)\n"
            f"Hit 10x: {stats['hit_10x']} ({stats['rate_10x']}%)\n"
            f"Rugged: {stats['rugged']}\n"
            f"Paper profit (sim): ${stats['paper_profit']:.2f}\n"
            f"Best pick: {stats['best_pick']}\n"
        )
        await alert_bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.MARKDOWN)


# ── Main Listener ─────────────────────────────────────────────────────────────
async def main():
    log.info("Starting Solana Signal Bot...")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start(bot_token=None)
    log.info("Telegram client connected.")

    await alert_bot.send_message(
        chat_id=OWNER_ID,
        text="🤖 *Solana Signal Bot is LIVE!*\nListening to 4AM Pumpfun Volume Signal...",
        parse_mode=ParseMode.MARKDOWN
    )

    @client.on(events.NewMessage(chats=CHANNEL))
    async def handler(event):
        text = event.message.message
        if 'Marketcap' not in text:
            return  # not a signal message

        log.info("Signal received, parsing...")
        signal = parse_signal(text)
        if not signal:
            log.warning("Could not parse signal")
            return

        # Score it
        score = score_signal(signal)
        log.info(f"Signal {signal.get('name')} scored {score['total']}/100")

        # Check socials
        socials = await check_socials(signal)

        # Add social score
        score['social'] = socials.get('score', 0)
        score['total']  = min(100, score['total'] + score['social'])

        # Save to tracker
        save_signal(signal, score)

        # Alert if score above threshold
        if score['total'] >= MIN_SCORE:
            msg = format_alert(signal, score, socials)
            await alert_bot.send_message(
                chat_id=OWNER_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN
            )
            log.info(f"Alert sent for {signal.get('name')} — score {score['total']}")
        else:
            log.info(f"Signal below threshold ({score['total']} < {MIN_SCORE}), not alerting")

    # Run background tasks
    asyncio.create_task(check_prices_loop())
    asyncio.create_task(daily_summary_loop())

    log.info(f"Listening to channel: {CHANNEL}")
    await client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
