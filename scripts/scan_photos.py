#!/usr/bin/env python3
"""
Backfill photos for units that have too few of them, re-fetch full
descriptions for units whose text was truncated by an earlier scrape, and
rate each unit's overall visual quality (1-5 stars) and primary flooring
type from its photos using Google Gemini's vision API.

Usage:
  python scripts/scan_photos.py
  python scripts/scan_photos.py --skip-backfill
  python scripts/scan_photos.py --skip-descriptions
  python scripts/scan_photos.py --skip-quality
  python scripts/scan_photos.py --rescan   (re-rate units that already have a quality_rating)
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Ensure emoji/unicode in print() output doesn't crash on Windows consoles (cp1252)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
PHOTOS_DIR = OUTPUTS_DIR / 'photos'
UNITS_JSON = OUTPUTS_DIR / 'units.json'

MIN_PHOTOS = 4
MAX_PHOTOS = 8
MAX_QUALITY_PHOTOS = 8
QUALITY_MODEL = 'gemini-2.5-flash'
# Free-tier Gemini quota is 5 requests/minute - pace requests to stay under it
QUALITY_REQUEST_DELAY = 13

FLOORING_TYPES = ['hardwood', 'tile', 'carpet', 'vinyl', 'concrete', 'mixed', 'unknown']

QUALITY_PROMPT = (
    "You are assessing rental apartment listing photos to judge how nice the unit looks - "
    "not its size or location. Look specifically at: paint condition (fresh vs. "
    "scuffed/dated), ceiling condition (clean vs. stained/damaged), flooring condition and "
    "quality, overall cleanliness, how new/updated the finishes look, and whether the space "
    "looks neat, orderly, and well cared-for. If the photos are too dark, blurry, staged "
    "from misleading angles, or too sparse to judge these things well, factor that into "
    "your rating and mention it briefly in the notes. Rate the unit from 1 to 5 stars, where "
    "1 means run-down or poorly maintained and 5 means modern, well-maintained, and "
    "move-in ready. Inspect every photo individually, not just the overall impression: if "
    "ANY single photo shows clear damage or disrepair - a stained, cracked, or "
    "water-damaged ceiling, mold, large wall cracks, broken fixtures, etc. - cap the rating "
    "at 3 even if the rest of the unit looks updated, and briefly note the issue and which "
    "room it's in. Second, identify the primary flooring type visible in the unit's main "
    "living areas - choose exactly one of "
    '"hardwood", "tile", "carpet", "vinyl", "concrete", "mixed" (if multiple types are '
    'clearly visible in different rooms), or "unknown" (if flooring isn\'t visible in any '
    "photo). "
    'Respond with ONLY a JSON object like '
    '{"rating": 3, "notes": "short reason, under 15 words", "flooring": "tile"}.'
)


def load_units():
    if not UNITS_JSON.exists():
        print(f"Error: {UNITS_JSON} not found")
        sys.exit(1)
    return json.loads(UNITS_JSON.read_text(encoding='utf-8'))


def save_units(data):
    UNITS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def fetch_html(url, session, timeout=15):
    headers = {"User-Agent": "apartment-poc-bot/1.0 (+https://example.com)"}
    r = session.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def download_image(url, dest_path, session, timeout=20):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    r = session.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in r.iter_content(1024 * 8):
            f.write(chunk)


def extract_craigslist_photo_urls(html):
    """Mirror the gallery-extraction logic in scrapers/craigslist.py."""
    soup = BeautifulSoup(html, 'html.parser')
    urls = []
    gallery = soup.find('div', {'id': 'thumbs'})
    if gallery:
        for img in gallery.find_all('img'):
            src = img.get('src')
            if src:
                src = re.sub(r'_\d+x\d+c?\.jpg$', '_600x450.jpg', src)
                urls.append(src)
    else:
        meta = soup.find('meta', {'property': 'og:image'})
        if meta and meta.get('content'):
            urls.append(meta.get('content'))
    return urls


def extract_craigslist_description(html):
    """Mirror the description-extraction logic in scrapers/craigslist.py."""
    soup = BeautifulSoup(html, 'html.parser')
    desc_el = soup.find('section', {'id': 'postingbody'})
    return desc_el.get_text('\n', strip=True) if desc_el else ''


# Matches the "available aug 15" form of Craigslist's availability badge
AVAILABLE_MONTH_DAY_RE = re.compile(r'available\s+([a-z]{3,9})\s+(\d{1,2})', re.IGNORECASE)

# Free-text fallback for listings whose description mentions move-in
# readiness without a structured availability badge
MOVE_IN_READY_RE = re.compile(
    r'(move[\s-]?in\s+ready|available\s+now|immediate(?:ly)?\s+availab|ready\s+(?:for\s+)?move[\s-]?in)',
    re.IGNORECASE
)


def extract_craigslist_specs(html):
    """Mirror the spec-extraction logic in scrapers/craigslist.py: parse the
    'attr important' badges (e.g. '2BR / 1Ba', '967ft2', 'available aug 15'
    / 'available now') for baths and move-in availability."""
    soup = BeautifulSoup(html, 'html.parser')
    result = {}
    for span in soup.find_all('span', {'class': 'attr important'}):
        text = span.get_text(' ', strip=True)

        m = re.match(r'(\d+)\s*BR\s*/\s*([\d.]+)\s*Ba', text, re.IGNORECASE)
        if m:
            result['baths'] = float(m.group(2))
            continue

        if text.lower().startswith('available'):
            if 'now' in text.lower():
                result['move_in_date'] = 'now'
            else:
                m = AVAILABLE_MONTH_DAY_RE.search(text)
                if m:
                    try:
                        parsed = datetime.strptime(f"{m.group(1)} {m.group(2)} {datetime.now().year}", '%b %d %Y').date()
                        if parsed < datetime.now().date():
                            parsed = parsed.replace(year=parsed.year + 1)
                        result['move_in_date'] = parsed.isoformat()
                    except ValueError:
                        pass

    return result


def extract_move_in_date(notes):
    """Best-effort fallback for listings with no structured availability
    badge: look for move-in-ready language in the free-text description.
    Returns 'now', or None if nothing is mentioned."""
    if notes and MOVE_IN_READY_RE.search(notes):
        return 'now'
    return None


def extract_amenities(html):
    """Mirror the amenity-extraction logic in scrapers/craigslist.py: parse
    the plain '<div class="attr ...">' entries in the 'attrgroup' divs (pet
    policy, laundry, parking, A/C, etc.), excluding the housing-type and
    rent-period attrs which are captured elsewhere."""
    soup = BeautifulSoup(html, 'html.parser')
    amenities = []
    for group in soup.find_all('div', {'class': 'attrgroup'}):
        for attr in group.find_all('div', {'class': 'attr'}):
            classes = attr.get('class') or []
            if 'rent_period' in classes:
                continue
            link = attr.find('a', href=True)
            if link and 'housing_type=' in link['href']:
                continue
            valu = attr.find('span', {'class': 'valu'})
            text = valu.get_text(strip=True) if valu else attr.get_text(strip=True)
            if text:
                amenities.append(text)
    return amenities


def normalize_amenities(amenities):
    """Mirror the amenity-normalization logic in scrapers/craigslist.py:
    derive has_washer_dryer/is_gated flags from a list of amenity strings."""
    joined = ' '.join(amenities).lower()
    has_washer_dryer = any(x in joined for x in ('washer', 'dryer', 'in-unit', 'in unit', 'w/d', 'stackable'))
    is_gated = any(x in joined for x in ('gated', 'gate', 'gated community'))
    return has_washer_dryer, is_gated


def backfill_descriptions(units_data, session):
    """Re-fetch the source listing for Craigslist units whose stored
    description was truncated by an earlier scrape (capped at 500 chars)."""
    changed = False
    for unit in units_data.get('units', []):
        notes = unit.get('notes') or ''
        if len(notes) < 500:
            continue
        if unit.get('source') != 'Craigslist' or not unit.get('source_url'):
            continue

        print(f"Backfilling description for {unit['id']} ({len(notes)} chars on file)...")
        try:
            html = fetch_html(unit['source_url'], session)
        except Exception as e:
            print(f"  failed to fetch listing: {e}")
            continue

        desc = extract_craigslist_description(html)
        if desc and desc != notes:
            unit['notes'] = desc
            print(f"  now {len(desc)} chars")
            changed = True

        time.sleep(1)

    return changed


def backfill_specs(units_data, session):
    """Re-fetch the source listing for Craigslist units missing 'baths' and
    populate baths + move_in_date from the structured 'attr important'
    badges (falling back to free-text move-in-ready language for the
    move-in date)."""
    changed = False
    for unit in units_data.get('units', []):
        if unit.get('baths') is not None:
            continue
        if unit.get('source') != 'Craigslist' or not unit.get('source_url'):
            continue

        print(f"Backfilling specs for {unit['id']}...")
        try:
            html = fetch_html(unit['source_url'], session)
        except Exception as e:
            print(f"  failed to fetch listing: {e}")
            continue

        specs = extract_craigslist_specs(html)
        if 'baths' in specs:
            unit['baths'] = specs['baths']
            print(f"  baths = {specs['baths']}")
            changed = True

        move_in = specs.get('move_in_date') or extract_move_in_date(unit.get('notes'))
        if move_in and unit.get('move_in_date') != move_in:
            unit['move_in_date'] = move_in
            print(f"  move_in_date = {move_in}")
            changed = True

        time.sleep(1)

    return changed


def backfill_amenities(units_data, session):
    """Re-fetch the source listing for Craigslist units with no amenities on
    file and populate amenities + has_washer_dryer/is_gated from the
    listing's attrgroup tags."""
    changed = False
    for unit in units_data.get('units', []):
        if unit.get('amenities'):
            continue
        if unit.get('source') != 'Craigslist' or not unit.get('source_url'):
            continue

        print(f"Backfilling amenities for {unit['id']}...")
        try:
            html = fetch_html(unit['source_url'], session)
        except Exception as e:
            print(f"  failed to fetch listing: {e}")
            continue

        amenities = extract_amenities(html)
        if amenities:
            unit['amenities'] = amenities
            unit['has_washer_dryer'], unit['is_gated'] = normalize_amenities(amenities)
            print(f"  {len(amenities)} amenities: {', '.join(amenities)}")
            changed = True

        time.sleep(1)

    return changed


def backfill_photos(units_data, session):
    """Re-fetch the source listing for units with fewer than MIN_PHOTOS photos
    and download any photos we're missing."""
    changed = False
    for unit in units_data.get('units', []):
        photos = unit.get('photos') or []
        if len(photos) >= MIN_PHOTOS:
            continue
        if unit.get('source') != 'Craigslist' or not unit.get('source_url'):
            continue

        print(f"Backfilling photos for {unit['id']} ({len(photos)} on file)...")
        try:
            html = fetch_html(unit['source_url'], session)
        except Exception as e:
            print(f"  failed to fetch listing: {e}")
            continue

        photo_urls = extract_craigslist_photo_urls(html)
        if not photo_urls:
            print("  no photos found on listing page")
            continue

        existing_sources = set(unit.get('photo_sources') or [])
        new_photos = list(photos)
        new_sources = list(unit.get('photo_sources') or [])
        photo_dir = PHOTOS_DIR / unit['id']
        next_index = len(new_photos) + 1

        for img_url in photo_urls:
            if len(new_photos) >= MAX_PHOTOS:
                break
            if img_url in existing_sources:
                continue
            dest = photo_dir / f'photo-{next_index}.jpg'
            try:
                download_image(img_url, dest, session)
            except Exception as e:
                print(f"  image download failed: {e}")
                continue
            rel = (Path('outputs') / 'photos' / unit['id'] / dest.name).as_posix()
            new_photos.append(rel)
            new_sources.append(img_url)
            next_index += 1
            time.sleep(0.3)

        if len(new_photos) > len(photos):
            unit['photos'] = new_photos
            unit['photo_sources'] = new_sources
            print(f"  now have {len(new_photos)} photo(s)")
            changed = True

        time.sleep(1)

    return changed


def get_quality_client():
    """Return (client, error_message). error_message is set (and client is
    None) if quality scanning isn't currently usable."""
    try:
        from google import genai
    except ImportError:
        return None, (
            "The 'google-genai' package isn't installed. Install dependencies with:\n"
            "  pip install -r requirements.txt"
        )

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return None, (
            "Set the GEMINI_API_KEY environment variable to enable quality scanning.\n"
            "Get a free key at https://aistudio.google.com/apikey"
        )

    return genai.Client(api_key=api_key), None


def encode_photo(path):
    from google.genai import types
    media_type = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=media_type)


class DailyQuotaExceeded(Exception):
    """Raised when the Gemini free-tier per-day request quota is exhausted -
    not worth retrying, since it won't recover for hours."""


def generate_content_with_retry(client, contents, max_retries=5):
    """Call the Gemini API, retrying on transient 429 (per-minute rate limit),
    503 (model overloaded), and "API key expired" errors (the latter appears
    to be a flaky/transient response for some keys - it doesn't fail every
    request) with the server-suggested backoff or a short fixed delay.
    Raises DailyQuotaExceeded immediately (no retries) if the per-day quota
    is exhausted."""
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(model=QUALITY_MODEL, contents=contents)
        except Exception as e:
            msg = str(e)
            if 'PerDay' in msg:
                raise DailyQuotaExceeded(msg) from e
            transient = '429' in msg or '503' in msg or 'API_KEY_INVALID' in msg
            if not transient:
                raise
            if attempt == max_retries - 1:
                raise
            match = re.search(r"'retryDelay': '(\d+)", msg)
            wait = int(match.group(1)) + 2 if match else 10
            print(f"  rate-limited/unavailable, retrying in {wait}s...")
            time.sleep(wait)


def rate_unit_quality(client, unit):
    photos = unit.get('photos') or []
    paths = [PROJECT_ROOT / p for p in photos[:MAX_QUALITY_PHOTOS]]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None

    contents = [encode_photo(p) for p in paths]
    contents.append(QUALITY_PROMPT)

    response = generate_content_with_retry(client, contents)
    text = response.text or ''
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return None
    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    try:
        rating = int(result.get('rating'))
    except (TypeError, ValueError):
        return None
    rating = max(1, min(5, rating))

    flooring = str(result.get('flooring', '')).strip().lower()
    if flooring not in FLOORING_TYPES:
        flooring = 'unknown'

    return rating, str(result.get('notes', '')).strip(), flooring


def scan_quality(units_data, rescan=False):
    client, error = get_quality_client()
    if error:
        print(f"\nSkipping quality scan: {error}")
        return False

    todo = [
        unit for unit in units_data.get('units', [])
        if unit.get('photos') and (
            rescan or unit.get('quality_rating') is None or unit.get('flooring_type') is None
        )
    ]
    total = len(todo)
    if total == 0:
        print("\nNo units need a quality scan.")
        return False

    changed = False
    for i, unit in enumerate(todo, start=1):
        print(f"[{i}/{total}] Rating quality for {unit['id']}...")
        try:
            result = rate_unit_quality(client, unit)
        except DailyQuotaExceeded:
            print("  daily Gemini quota reached - stopping scan, try again tomorrow.")
            break
        except Exception as e:
            print(f"  quality scan failed: {e}")
            time.sleep(QUALITY_REQUEST_DELAY)
            continue

        if not result:
            print("  no rating returned")
            time.sleep(QUALITY_REQUEST_DELAY)
            continue

        rating, notes, flooring = result
        unit['quality_rating'] = rating
        unit['quality_notes'] = notes
        unit['flooring_type'] = flooring
        print(f"  {rating}/5 stars, {flooring} flooring - {notes}")
        changed = True
        time.sleep(QUALITY_REQUEST_DELAY)

    return changed


def main():
    parser = argparse.ArgumentParser(description="Backfill unit photos and rate visual quality from photos")
    parser.add_argument('--skip-backfill', action='store_true', help='Skip the photo backfill step')
    parser.add_argument('--skip-descriptions', action='store_true', help='Skip the description backfill step')
    parser.add_argument('--skip-quality', action='store_true', help='Skip the Claude vision quality scan')
    parser.add_argument('--rescan', action='store_true', help='Re-rate units that already have a quality_rating')
    args = parser.parse_args()

    units_data = load_units()
    changed = False

    if not args.skip_backfill or not args.skip_descriptions:
        session = requests.Session()
        if not args.skip_backfill:
            changed = backfill_photos(units_data, session) or changed
            changed = backfill_specs(units_data, session) or changed
            changed = backfill_amenities(units_data, session) or changed
        if not args.skip_descriptions:
            changed = backfill_descriptions(units_data, session) or changed

    if not args.skip_quality:
        changed = scan_quality(units_data, rescan=args.rescan) or changed

    if changed:
        save_units(units_data)
        print(f"\nSaved updates to {UNITS_JSON}")
        print("Run: python scripts/generate-html.py   to refresh the summary page")
    else:
        print("\nNo changes made.")


if __name__ == '__main__':
    main()
