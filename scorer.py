"""
Signal Scorer
Scores every pump.fun signal from 0-100 based on on-chain metrics.
Social score is added separately by social_checker.py

Score Breakdown:
  On-chain metrics  → 35 pts
  Narrative         → 20 pts
  Social (external) → 25 pts
  Red flag penalty  → up to -30 pts
  ─────────────────────────────
  Total             → /100
"""

import re

# ── Viral name patterns ───────────────────────────────────────────────────────
VIRAL_KEYWORDS = [
    'pepe', 'doge', 'shib', 'cat', 'dog', 'frog', 'wojak', 'chad',
    'based', 'cope', 'mog', 'giga', 'sigma', 'ape', 'moon', 'sol',
    'trump', 'elon', 'ai', 'gpt', 'baby', 'mini', 'super', 'mega',
    'inu', 'kun', 'chan', 'sama', 'kun', 'nyan', 'uwu', 'sus',
    'country', 'nation', 'flag', 'world', 'global', 'tralalero',
    'brainrot', 'rizz', 'slay', 'bussin', 'goat', 'npc', 'skill issue'
]

RUGGED_KEYWORDS = ['scam', 'rug', 'fake', 'honeypot']


def score_onchain(signal: dict) -> tuple[int, list]:
    """Score on-chain metrics. Returns (score, red_flags)."""
    score = 0
    red_flags = []

    # ── Market Cap (0-10 pts) ─────────────────────────────────────────────────
    mcap = signal.get('marketcap', 0)
    if mcap <= 15000:       score += 10   # ultra early
    elif mcap <= 25000:     score += 9    # very early
    elif mcap <= 35000:     score += 7    # early
    elif mcap <= 50000:     score += 5    # decent
    elif mcap <= 100000:    score += 2    # late
    else:
        score += 0
        red_flags.append(f"High mcap ${mcap:,} — late entry risk")

    # ── Age (0-8 pts) ─────────────────────────────────────────────────────────
    age = signal.get('age_minutes', 999)
    if age <= 2:            score += 8    # brand new
    elif age <= 5:          score += 7
    elif age <= 10:         score += 5
    elif age <= 20:         score += 3
    elif age <= 30:         score += 1
    else:
        score += 0
        red_flags.append(f"Old token ({age:.0f}m) — momentum may be gone")

    # ── Volume/Mcap Ratio (0-7 pts) ───────────────────────────────────────────
    vol = signal.get('volume', 0)
    ratio = (vol / mcap) if mcap > 0 else 0
    if ratio >= 2.0:        score += 7    # insane volume
    elif ratio >= 1.5:      score += 6
    elif ratio >= 1.0:      score += 5
    elif ratio >= 0.75:     score += 4
    elif ratio >= 0.5:      score += 2
    else:
        score += 0
        red_flags.append(f"Low volume ratio {ratio:.2f} — weak momentum")

    # ── Dev Wallet % (0-5 pts) ────────────────────────────────────────────────
    dev_pct = signal.get('dev_pct', 100)
    if dev_pct == 0:        score += 5    # dev sold/never held
    elif dev_pct <= 2:      score += 3
    elif dev_pct <= 5:      score += 1
    else:
        score += 0
        red_flags.append(f"Dev holding {dev_pct}% — dump risk")

    # ── Top 10 Holders (0-5 pts) ──────────────────────────────────────────────
    top10 = signal.get('top10_pct', 100)
    if top10 <= 15:         score += 5    # very distributed
    elif top10 <= 25:       score += 4
    elif top10 <= 35:       score += 2
    elif top10 <= 50:       score += 1
    else:
        score += 0
        red_flags.append(f"Top 10 hold {top10}% — manipulation risk")

    # ── Bonding Curve (0-0 pts, but red flag if too low) ─────────────────────
    bc = signal.get('bonding_curve', 0)
    if bc >= 95:            score += 0    # graduating soon = pump incoming
    elif bc >= 80:          score += 0
    elif bc < 30:
        red_flags.append(f"Very low bonding curve {bc}% — far from graduation")

    return score, red_flags


def score_narrative(signal: dict) -> int:
    """Score the token name/narrative virality. Returns 0-20."""
    score = 0
    name = signal.get('name', '').lower()

    # Viral keyword match
    for kw in VIRAL_KEYWORDS:
        if kw in name:
            score += 8
            break

    # Short punchy name (under 10 chars = more memorable)
    if len(name) <= 6:      score += 6
    elif len(name) <= 10:   score += 4
    elif len(name) <= 15:   score += 2

    # All caps = energy
    raw_name = signal.get('name', '')
    if raw_name == raw_name.upper() and len(raw_name) > 2:
        score += 3

    # Funny/ironic names (e.g. "scum" pumped 7x)
    ironic = ['scum', 'rug', 'trash', 'garbage', 'useless', 'worthless', 'nothing', 'zero']
    for kw in ironic:
        if kw in name:
            score += 6
            break

    return min(score, 20)


def score_signal(signal: dict) -> dict:
    """
    Master scoring function.
    Returns dict with breakdown and red flags.
    Social score (0-25) is added later by social_checker.
    """
    onchain_score, red_flags = score_onchain(signal)
    narrative_score          = score_narrative(signal)

    # Bonding curve bonus — near graduation = imminent pump
    bc = signal.get('bonding_curve', 0)
    bc_bonus = 3 if bc >= 95 else 2 if bc >= 85 else 0

    total = onchain_score + narrative_score + bc_bonus

    return {
        'onchain':    onchain_score,
        'narrative':  narrative_score,
        'bc_bonus':   bc_bonus,
        'social':     0,           # filled in by social_checker
        'total':      min(total, 75),  # capped at 75 until social added
        'red_flags':  red_flags,
    }
