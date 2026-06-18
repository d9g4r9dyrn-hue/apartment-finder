#!/usr/bin/env python3
"""
Scam risk analyzer for apartment listings.

Scores a unit based on listing data, contacts, and interaction patterns.
No external API required — uses heuristic rules derived from common
rental scam patterns (FTC, HUD, consumer protection research).

Usage:
  python -m scripts.check_scam unit-0042
  python -m scripts.check_scam --all
  python -m scripts.check_scam unit-0042 --json
"""
import json
import re
import sys
from pathlib import Path
from statistics import median

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UNITS_JSON = PROJECT_ROOT / 'outputs' / 'units.json'
INTERACTIONS_JSON = PROJECT_ROOT / 'outputs' / 'interactions.json'

LOCAL_AREA_CODES = {'727', '813', '941', '352', '863'}

TRUSTED_SOURCES = {'realtor.com', 'zillow', 'apartments.com', 'trulia', 'hotpads'}


def load_units():
    if not UNITS_JSON.exists():
        return []
    data = json.loads(UNITS_JSON.read_text(encoding='utf-8'))
    return data.get('units', [])


def load_interactions():
    if not INTERACTIONS_JSON.exists():
        return {}
    data = json.loads(INTERACTIONS_JSON.read_text(encoding='utf-8'))
    return data.get('units', {})


def compute_market_stats(all_units):
    """Compute median prices per bed count using trusted sources only.
    Falls back to all sources if no trusted data exists for a bed count."""
    trusted_prices = {}
    all_prices = {}
    for u in all_units:
        beds = u.get('beds') or 0
        price = u.get('price') or 0
        if price <= 0:
            continue
        all_prices.setdefault(beds, []).append(price)
        source = (u.get('source') or '').lower()
        if source in TRUSTED_SOURCES:
            trusted_prices.setdefault(beds, []).append(price)

    result = {}
    for b, ps in all_prices.items():
        trusted = trusted_prices.get(b, [])
        if len(trusted) >= 3:
            result[str(b)] = {'median': median(trusted), 'count': len(trusted), 'source': 'trusted'}
        else:
            result[str(b)] = {'median': median(ps), 'count': len(ps), 'source': 'all'}
    return result


def compute_cross_refs(all_units):
    phone_map = {}
    email_map = {}
    for u in all_units:
        uid = u.get('id', '')
        phone = u.get('contact_phone')
        email = u.get('contact_email')
        if phone:
            phone_map.setdefault(phone, []).append(uid)
        if email:
            email_map.setdefault(email, []).append(uid)
    return {
        'phones': {p: ids for p, ids in phone_map.items() if len(ids) > 1},
        'emails': {e: ids for e, ids in email_map.items() if len(ids) > 1},
    }


# ---------------------------------------------------------------------------
# Keyword patterns for interaction message scanning
# ---------------------------------------------------------------------------

_WIRE_KEYWORDS = re.compile(
    r'\b(zelle|cash\s*app|cashapp|venmo|western\s*union|wire\s*transfer|'
    r'money\s*order|bitcoin|btc|crypto|gift\s*card)\b', re.I
)
_PRESSURE_KEYWORDS = re.compile(
    r'\b(act\s+fast|won\'?t\s+last|other\s+applicants?|hurry|urgent|'
    r'today\s+only|first\s+come|limited\s+time|asap|immediately)\b', re.I
)
_ABSENT_KEYWORDS = re.compile(
    r'\b(out\s+of\s+(town|state|country)|deployed|overseas|traveling|'
    r'abroad|can\'?t\s+show|can\'?t\s+meet|unable\s+to\s+(show|meet))\b', re.I
)
_DEPOSIT_BEFORE_VIEWING = re.compile(
    r'\b(deposit\s+(before|without|prior\s+to)\s+(see|view|tour|visit|show)|'
    r'hold\s+(the\s+)?(unit|apartment|place|property)\s+(for|with)\s+\$|'
    r'send\s+(deposit|money|payment)\s+(first|now|before))\b', re.I
)
_PII_KEYWORDS = re.compile(
    r'\b(ssn|social\s+security|bank\s+account|routing\s+number|'
    r'credit\s+card|debit\s+card)\b', re.I
)
_KEY_MAIL_KEYWORDS = re.compile(
    r'\b(mail\s+(you\s+)?(the\s+)?keys?|send\s+(you\s+)?(the\s+)?keys?|'
    r'fedex\s+(the\s+)?keys?|ship\s+(the\s+)?keys?)\b', re.I
)
_LOCKBOX_KEYWORDS = re.compile(
    r'\b(lockbox|lock\s*box|key\s*box|combo\s*box|access\s*code|'
    r'go\s+to\s+(the|a)\s+link|click\s+(the|this)\s+link|'
    r'let\s+yourself\s+in|self[\s-]?show|self[\s-]?tour|'
    r'view\s+(it\s+)?on\s+your\s+own)\b', re.I
)
_DIFFERENT_NUMBER = re.compile(
    r'\b(different\s+(phone\s+)?number|called\s+back\s+from\s+(a\s+)?different|'
    r'new\s+number|another\s+number|second\s+number)\b', re.I
)
_HUNG_UP = re.compile(
    r'\b(hung\s+up|disconnected|ended\s+the\s+call|stopped\s+respond|'
    r'went\s+silent|ghosted|blocked)\b', re.I
)
_CONFIRMED_SCAM = re.compile(
    r'\b(confirmed?\s+scam|known\s+scam|definitely\s+(a\s+)?scam|'
    r'this\s+is\s+(a\s+)?scam|scam\s+confirmed|do\s+not\s+proceed)\b', re.I
)
_SHOWING_DONE = re.compile(
    r'\b(toured|visited|walked\s+through|saw\s+the\s+(unit|place|apartment)|'
    r'met\s+(them\s+)?(at|in\s+person)|viewed\s+the|completed\s+showing|'
    r'did\s+(a\s+|the\s+)?(tour|showing|walk[\s-]?through))\b', re.I
)
_STANDARD_PROCESS = re.compile(
    r'\b(application|background\s+check|credit\s+check|lease\s+agreement|'
    r'signed\s+(the\s+)?lease|move[\s-]in\s+checklist)\b', re.I
)


def _extract_phone_area(phone):
    if not phone:
        return None
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        return digits[:3]
    if len(digits) == 11 and digits[0] == '1':
        return digits[1:4]
    return None


# ---------------------------------------------------------------------------
# Core scoring engine
# ---------------------------------------------------------------------------

def analyze_unit(unit, interactions_entry, market_stats, cross_refs):
    """Return a dict with score, level, factors[], and summary."""
    score = 0
    factors = []

    uid = unit.get('id', '')
    price = unit.get('price') or 0
    beds = unit.get('beds') or 0
    sqft = unit.get('sqft')
    source = (unit.get('source') or '').lower()
    notes = unit.get('notes') or ''
    photos = unit.get('photos') or []
    lat = unit.get('lat')
    lon = unit.get('lon')
    contact_phone = unit.get('contact_phone')
    contact_email = unit.get('contact_email')
    contact_name = unit.get('contact_name')
    amenities = unit.get('amenities') or []

    contacts = (interactions_entry or {}).get('contacts', [])
    interactions = (interactions_entry or {}).get('interactions', [])
    all_messages = ' '.join(i.get('message', '') for i in interactions)

    # Merge contact info from interactions into consideration
    all_phones = set()
    all_emails = set()
    all_names = set()
    if contact_phone:
        all_phones.add(contact_phone)
    if contact_email:
        all_emails.add(contact_email)
    if contact_name:
        all_names.add(contact_name.lower())
    for c in contacts:
        if c.get('phone'):
            all_phones.add(c['phone'])
        if c.get('email'):
            all_emails.add(c['email'])
        if c.get('name'):
            all_names.add(c['name'].lower())

    # ---- PRICE ANALYSIS ----
    # Trusted platforms set real market prices — underpricing is only a signal for unverified sources
    beds_key = str(beds)
    if price > 0 and beds_key in market_stats and source not in TRUSTED_SOURCES:
        med = market_stats[beds_key].get('median', 0)
        if med > 0:
            ratio = price / med
            pct_below = round((1 - ratio) * 100)
            if ratio < 0.60:
                score += 50
                factors.append({
                    'severity': 'high',
                    'category': 'price',
                    'text': f'Price ${price:,} is {pct_below}% below median ${med:,.0f} for {beds}bd units — extreme underpricing',
                })
            elif ratio < 0.75:
                score += 30
                factors.append({
                    'severity': 'high',
                    'category': 'price',
                    'text': f'Price ${price:,} is {pct_below}% below median ${med:,.0f} for {beds}bd units',
                })
            elif ratio < 0.85:
                score += 15
                factors.append({
                    'severity': 'medium',
                    'category': 'price',
                    'text': f'Price ${price:,} is {pct_below}% below median ${med:,.0f} for {beds}bd units',
                })
            elif ratio < 0.92:
                score += 5
                factors.append({
                    'severity': 'low',
                    'category': 'price',
                    'text': f'Price is somewhat below market median for {beds}bd',
                })

    # ---- CONTACT ANALYSIS ----
    # Trusted platforms use their own contact forms; missing info is normal
    if source not in TRUSTED_SOURCES:
        if not all_phones and not all_emails:
            score += 10
            factors.append({
                'severity': 'medium',
                'category': 'contact',
                'text': 'No contact information available at all',
            })
        else:
            if not all_phones:
                score += 7
                factors.append({
                    'severity': 'low',
                    'category': 'contact',
                    'text': 'No phone number — email only',
                })

            relay_emails = [e for e in all_emails if '@hous.craigslist.org' in e]
            real_emails = [e for e in all_emails if '@hous.craigslist.org' not in e]
            if relay_emails and not real_emails:
                score += 5
                factors.append({
                    'severity': 'low',
                    'category': 'contact',
                    'text': 'Only has Craigslist relay email (no direct email)',
                })

            for phone in all_phones:
                area = _extract_phone_area(phone)
                if area and area not in LOCAL_AREA_CODES:
                    score += 10
                    factors.append({
                        'severity': 'medium',
                        'category': 'contact',
                        'text': f'Phone {phone} has out-of-area code ({area}) — not local Tampa Bay',
                    })
                    break

    if not all_names and source not in TRUSTED_SOURCES:
        score += 5
        factors.append({
            'severity': 'low',
            'category': 'contact',
            'text': 'No contact name provided',
        })
    elif all_names:
        for name in all_names:
            parts = name.strip().split()
            if len(parts) == 1 and len(parts[0]) <= 6:
                score += 3
                factors.append({
                    'severity': 'low',
                    'category': 'contact',
                    'text': f'Contact name "{name}" is a single short first name (no surname)',
                })
                break

    # ---- CROSS-LISTING ANALYSIS ----
    for phone in all_phones:
        other_units = cross_refs.get('phones', {}).get(phone, [])
        others = [u for u in other_units if u != uid]
        if others:
            score += 12
            factors.append({
                'severity': 'medium',
                'category': 'cross_listing',
                'text': f'Phone {phone} also appears on {len(others)} other unit(s): {", ".join(others)}',
            })
            break

    for email in all_emails:
        if '@hous.craigslist.org' in email:
            continue
        other_units = cross_refs.get('emails', {}).get(email, [])
        others = [u for u in other_units if u != uid]
        if others:
            score += 8
            factors.append({
                'severity': 'medium',
                'category': 'cross_listing',
                'text': f'Email also used on {len(others)} other unit(s)',
            })
            break

    # ---- LISTING QUALITY ----
    if len(photos) == 0:
        score += 15
        factors.append({
            'severity': 'medium',
            'category': 'listing',
            'text': 'No photos at all',
        })
    elif len(photos) <= 2:
        score += 7
        factors.append({
            'severity': 'low',
            'category': 'listing',
            'text': f'Only {len(photos)} photo(s) — legitimate listings usually have more',
        })

    if not sqft:
        score += 3
        factors.append({
            'severity': 'low',
            'category': 'listing',
            'text': 'No square footage listed',
        })

    if len(notes) < 100 and source not in TRUSTED_SOURCES:
        score += 5
        factors.append({
            'severity': 'low',
            'category': 'listing',
            'text': f'Very short description ({len(notes)} chars)',
        })

    if lat is None or lon is None:
        score += 8
        factors.append({
            'severity': 'medium',
            'category': 'listing',
            'text': 'No map coordinates — address may be approximate or fake',
        })

    if 'craigslist' in source:
        score += 5
        factors.append({
            'severity': 'low',
            'category': 'listing',
            'text': 'Listed on Craigslist (higher scam rate than managed platforms)',
        })

    # ---- INTERACTION MESSAGE SCANNING ----
    if all_messages:
        if _WIRE_KEYWORDS.search(all_messages):
            score += 30
            matches = _WIRE_KEYWORDS.findall(all_messages)
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': f'Payment via non-standard method mentioned: {", ".join(set(m if isinstance(m, str) else m[0] for m in matches))}',
            })

        if _DEPOSIT_BEFORE_VIEWING.search(all_messages):
            score += 25
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Requested deposit before viewing the property',
            })

        if _PII_KEYWORDS.search(all_messages):
            score += 25
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Asked for sensitive personal information (SSN, bank details)',
            })

        if _ABSENT_KEYWORDS.search(all_messages):
            score += 20
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Claims to be unavailable to show property (out of town/deployed/etc)',
            })

        if _KEY_MAIL_KEYWORDS.search(all_messages):
            score += 25
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Offered to mail/ship keys without in-person meeting',
            })

        if _PRESSURE_KEYWORDS.search(all_messages):
            score += 15
            factors.append({
                'severity': 'medium',
                'category': 'interaction',
                'text': 'Uses pressure/urgency language',
            })

        if _LOCKBOX_KEYWORDS.search(all_messages):
            score += 25
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Directed to lockbox/link for self-access instead of in-person showing',
            })

        if _DIFFERENT_NUMBER.search(all_messages):
            score += 20
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Called back from a different phone number than listing',
            })

        if _HUNG_UP.search(all_messages):
            score += 20
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Hung up / went silent when questioned',
            })

        if _CONFIRMED_SCAM.search(all_messages):
            score += 50
            factors.append({
                'severity': 'high',
                'category': 'interaction',
                'text': 'Manually confirmed as a scam',
            })

    # ---- CONFIRMED SCAM FLAG (from interactions.json) ----
    if (interactions_entry or {}).get('confirmed_scam'):
        score += 50
        scam_notes = (interactions_entry or {}).get('scam_notes', '')
        factors.append({
            'severity': 'high',
            'category': 'interaction',
            'text': f'Confirmed scam: {scam_notes}' if scam_notes else 'Explicitly marked as confirmed scam',
        })

    # ---- POSITIVE SIGNALS (reduce score) ----
    if interactions:
        if any(_SHOWING_DONE.search(i.get('message', '')) for i in interactions):
            score -= 15
            factors.append({
                'severity': 'positive',
                'category': 'interaction',
                'text': 'In-person showing completed or scheduled',
            })

        if any(_STANDARD_PROCESS.search(i.get('message', '')) for i in interactions):
            score -= 10
            factors.append({
                'severity': 'positive',
                'category': 'interaction',
                'text': 'Standard application/lease process mentioned',
            })

    if source in TRUSTED_SOURCES:
        score -= 15
        factors.append({
            'severity': 'positive',
            'category': 'listing',
            'text': f'Verified listing platform ({unit.get("source")}) — properties are vetted',
        })
    elif source and 'craigslist' not in source:
        score -= 5
        factors.append({
            'severity': 'positive',
            'category': 'listing',
            'text': f'Listed on managed platform ({unit.get("source")})',
        })

    if len(photos) >= 5:
        score -= 5
        factors.append({
            'severity': 'positive',
            'category': 'listing',
            'text': f'{len(photos)} photos — good photo coverage',
        })

    for phone in all_phones:
        area = _extract_phone_area(phone)
        if area and area in LOCAL_AREA_CODES:
            score -= 3
            factors.append({
                'severity': 'positive',
                'category': 'contact',
                'text': f'Local area code ({area})',
            })
            break

    score = max(0, score)

    if score >= 60:
        level = 'very_high'
    elif score >= 35:
        level = 'high'
    elif score >= 15:
        level = 'moderate'
    else:
        level = 'low'

    level_labels = {
        'low': 'Low risk',
        'moderate': 'Moderate risk — proceed with caution',
        'high': 'High risk — likely scam',
        'very_high': 'Very high risk — almost certainly a scam',
    }

    return {
        'unit_id': uid,
        'score': score,
        'level': level,
        'level_label': level_labels[level],
        'factors': factors,
    }


def print_report(result, unit):
    level_icons = {
        'low': 'LOW',
        'moderate': 'MODERATE',
        'high': 'HIGH',
        'very_high': 'VERY HIGH',
    }
    uid = result['unit_id']
    title = unit.get('title') or unit.get('address') or uid
    price = unit.get('price') or 0

    print(f'\n{"=" * 60}')
    print(f'SCAM RISK ANALYSIS: {uid}')
    print(f'  {title}')
    print(f'  ${price:,}/mo | {unit.get("beds") or "?"}bd | {unit.get("source") or "unknown"}')
    print(f'{"=" * 60}')
    print(f'  Risk score: {result["score"]} / 100')
    print(f'  Level:      [{level_icons[result["level"]]}] {result["level_label"]}')
    print()

    severity_order = {'high': 0, 'medium': 1, 'low': 2, 'positive': 3}
    sorted_factors = sorted(result['factors'], key=lambda f: severity_order.get(f['severity'], 9))

    if sorted_factors:
        print('  Risk factors:')
        for f in sorted_factors:
            icon = {'high': '!!', 'medium': '! ', 'low': '- ', 'positive': '+ '}.get(f['severity'], '  ')
            label = {'high': 'HIGH', 'medium': 'MED ', 'low': 'LOW ', 'positive': 'GOOD'}.get(f['severity'], '    ')
            print(f'    [{label}] {icon} {f["text"]}')
    else:
        print('  No specific risk factors identified.')

    print(f'\n{"=" * 60}\n')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Analyze scam risk for apartment listings')
    parser.add_argument('unit_id', nargs='?', help='Unit ID to analyze (e.g. unit-0042)')
    parser.add_argument('--all', action='store_true', help='Analyze all units')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--threshold', type=int, default=0, help='Only show units with score >= threshold')
    args = parser.parse_args()

    if not args.unit_id and not args.all:
        parser.print_help()
        return

    all_units = load_units()
    all_interactions = load_interactions()
    market_stats = compute_market_stats(all_units)
    cross_refs = compute_cross_refs(all_units)

    units_to_check = all_units if args.all else [u for u in all_units if u.get('id') == args.unit_id]

    if not units_to_check:
        print(f'Unit {args.unit_id} not found in units.json')
        return

    results = []
    for unit in units_to_check:
        uid = unit.get('id', '')
        ix = all_interactions.get(uid, {})
        result = analyze_unit(unit, ix, market_stats, cross_refs)
        results.append(result)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    results.sort(key=lambda r: r['score'], reverse=True)
    for result in results:
        if result['score'] >= args.threshold:
            unit = next(u for u in all_units if u.get('id') == result['unit_id'])
            print_report(result, unit)

    if args.all:
        print(f'\nSummary: {len(results)} units analyzed')
        by_level = {}
        for r in results:
            by_level.setdefault(r['level'], []).append(r)
        for level in ['very_high', 'high', 'moderate', 'low']:
            if level in by_level:
                label = {'very_high': 'Very high', 'high': 'High', 'moderate': 'Moderate', 'low': 'Low'}[level]
                print(f'  {label}: {len(by_level[level])} units')


if __name__ == '__main__':
    main()
