#!/usr/bin/env python3
"""
Apartments.com scraper using Playwright.

Strategy: launch a headless Chromium browser, intercept the JSON search API
response that apartments.com loads on the search page, and parse it directly.
Falls back to DOM scraping if the API interception doesn't fire.

Setup (one-time):
  pip install playwright
  playwright install chromium

Usage:
  python -m scripts.scrapers.apartments_com
  python -m scripts.scrapers.apartments_com --max 50 --url "https://www.apartments.com/clearwater-fl/"
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
PHOTOS_DIR = OUTPUTS_DIR / 'photos'
UNITS_JSON = OUTPUTS_DIR / 'units.json'
CONFIG_JSON = PROJECT_ROOT / 'config.json'

from scripts.scrapers.common import download_image

MAX_PHOTOS = 8

# How long to wait (seconds) for the page's XHR listing data to arrive
PAGE_LOAD_WAIT = 14

# Injected into every page before any scripts run to hide headless Chromium fingerprints
_STEALTH_JS = """
// Remove the webdriver property that Akamai/bots detect
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Fake a realistic plugin list
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    arr.__proto__ = PluginArray.prototype;
    return arr;
  }
});

// Fake multiple languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'es'] });

// Provide a chrome object (headless Chrome omits it)
window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {}, app: {} };

// Fix permissions.query for notifications (headless returns 'denied', real browser may not)
const _origPermissions = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (params) =>
  params.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : _origPermissions(params);
"""


def load_config():
    if not CONFIG_JSON.exists():
        return {}
    return json.loads(CONFIG_JSON.read_text(encoding='utf-8'))


def load_units():
    if UNITS_JSON.exists():
        return json.loads(UNITS_JSON.read_text(encoding='utf-8'))
    return {
        'last_updated': datetime.now().isoformat(),
        'total_units': 0,
        'schema_notes': {
            'photos': 'Array of local relative paths to downloaded images',
            'photo_sources': 'Original URLs the photos were downloaded from',
        },
        'units': [],
    }


def save_units(data):
    data['last_updated'] = datetime.now().isoformat()
    data['total_units'] = len(data.get('units', []))
    UNITS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Saved {data["total_units"]} units to {UNITS_JSON}')


def make_unit_id(units):
    max_id = -1
    for u in units:
        m = re.match(r'unit-(\d+)$', u.get('id') or '')
        if m:
            max_id = max(max_id, int(m.group(1)))
    return f'unit-{max_id + 1:04d}'


def build_search_url(config):
    min_price = config.get('min_price', 1000)
    max_price = config.get('max_price', 2000)
    min_beds  = config.get('min_beds', 2)
    # apartments.com URL format: /city-st/N-bedrooms/min-Xto-Y/
    beds_slug  = f'{min_beds}-bedrooms'
    price_slug = f'min-{min_price}to{max_price}'
    return f'https://www.apartments.com/clearwater-fl/{beds_slug}/{price_slug}/'


# ---------------------------------------------------------------------------
# JSON response interception helpers
# ---------------------------------------------------------------------------

def _looks_like_listings(data):
    """Return True if a parsed JSON object looks like it contains listing data."""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            keys = set(first.keys())
            return bool(keys & {'listingId', 'propertyId', 'unitId', 'rentRange', 'rent', 'beds', 'bedrooms'})
    if isinstance(data, dict):
        for key in ('listings', 'units', 'searchResults', 'results', 'properties', 'data'):
            if key in data and isinstance(data[key], list) and data[key]:
                return _looks_like_listings(data[key])
        # Check for top-level listing fields
        if {'rentRange', 'listingId', 'propertyId'} & set(data.keys()):
            return True
    return False


def _extract_listing_array(data):
    """Walk the data tree to return a flat list of listing dicts."""
    if isinstance(data, list):
        if data and _looks_like_listings(data):
            return data
        return []
    if isinstance(data, dict):
        for key in ('listings', 'units', 'searchResults', 'results', 'properties', 'data', 'placards'):
            if key in data and isinstance(data[key], list):
                sub = data[key]
                if sub and _looks_like_listings(sub):
                    return sub
        # Recurse one more level
        for v in data.values():
            result = _extract_listing_array(v)
            if result:
                return result
    return []


# ---------------------------------------------------------------------------
# Listing parser — handles apartments.com's JSON schema
# ---------------------------------------------------------------------------

def _digits_only(s):
    return re.sub(r'\D', '', str(s or ''))


def _fmt_phone(digits):
    if len(digits) == 10:
        return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'
    if len(digits) == 11 and digits[0] == '1':
        return f'({digits[1:4]}) {digits[4:7]}-{digits[7:]}'
    return None


def parse_listing(item):
    """Parse one listing dict from apartments.com API into our unit format."""
    # --- price ---
    price = 0
    for key in ('rentRange', 'rent', 'price', 'minRent'):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            low = raw.get('low') or raw.get('min') or raw.get('from') or 0
            try:
                price = int(low)
            except Exception:
                pass
        else:
            try:
                price = int(str(raw).replace(',', '').replace('$', ''))
            except Exception:
                pass
        if price:
            break

    # --- beds ---
    beds = 0
    for key in ('beds', 'bedrooms', 'minBeds', 'bed'):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            raw = raw.get('low') or raw.get('min') or raw.get('from') or 0
        try:
            beds = int(raw)
            break
        except Exception:
            pass

    # --- baths ---
    baths = None
    for key in ('baths', 'bathrooms', 'minBaths', 'bath'):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            raw = raw.get('low') or raw.get('min') or raw.get('from')
        try:
            baths = float(raw)
            break
        except Exception:
            pass

    # --- sqft ---
    sqft = None
    for key in ('sqft', 'squareFeet', 'minSqft', 'size'):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            raw = raw.get('low') or raw.get('min') or raw.get('from')
        try:
            sqft = int(raw)
            break
        except Exception:
            pass

    # --- address ---
    addr = item.get('address') or {}
    if isinstance(addr, str):
        address = addr
    else:
        parts = [
            addr.get('streetAddress') or addr.get('line1') or addr.get('street') or '',
            addr.get('city') or '',
            addr.get('stateCode') or addr.get('state') or 'FL',
            addr.get('zipCode') or addr.get('postalCode') or '',
        ]
        address = ', '.join(p for p in parts if p).strip(', ')
    if not address:
        address = (
            item.get('addressFull')
            or item.get('fullAddress')
            or item.get('location', {}).get('prettyAddress', '')
        )

    # --- lat/lon ---
    lat = lon = None
    for loc_key in ('location', 'coordinates', 'coordinate', 'latLong', 'geo'):
        loc = item.get(loc_key)
        if isinstance(loc, dict):
            lat = loc.get('lat') or loc.get('latitude')
            lon = loc.get('lng') or loc.get('lon') or loc.get('longitude')
            if lat and lon:
                break
    try:
        lat = float(lat) if lat else None
        lon = float(lon) if lon else None
    except Exception:
        lat = lon = None

    # --- title / name ---
    title = (
        item.get('propertyName')
        or item.get('name')
        or item.get('title')
        or address
        or 'Apartments.com listing'
    )

    # --- URL ---
    url = item.get('url') or item.get('listingUrl') or item.get('detailUrl') or ''
    if url and not url.startswith('http'):
        url = 'https://www.apartments.com' + url

    # --- housing type ---
    prop_type = (item.get('propertyType') or item.get('type') or '').lower()
    if 'apartment' in prop_type or 'condo' in prop_type:
        housing_type = 'Apartment'
    elif 'house' in prop_type or 'single' in prop_type or 'home' in prop_type:
        housing_type = 'House'
    elif 'townhouse' in prop_type or 'town' in prop_type:
        housing_type = 'Townhouse'
    else:
        housing_type = prop_type.capitalize() if prop_type else 'Apartment'

    # --- photos ---
    photo_urls = []
    for key in ('photos', 'images', 'media', 'photoUrls'):
        raw = item.get(key)
        if not raw:
            continue
        if isinstance(raw, list):
            for p in raw:
                if isinstance(p, dict):
                    href = p.get('url') or p.get('src') or p.get('href') or p.get('path') or ''
                else:
                    href = str(p)
                if href and href.startswith('http'):
                    photo_urls.append(href)
        break
    # Also check single thumbnail
    thumb = item.get('photo') or item.get('thumbnail') or item.get('primaryPhoto')
    if isinstance(thumb, dict):
        thumb = thumb.get('url') or thumb.get('src') or ''
    if thumb and isinstance(thumb, str) and thumb.startswith('http') and thumb not in photo_urls:
        photo_urls.insert(0, thumb)

    # --- amenities ---
    amenities = []
    for key in ('amenities', 'features', 'tags'):
        raw = item.get(key)
        if isinstance(raw, list):
            for a in raw:
                if isinstance(a, str):
                    amenities.append(a)
                elif isinstance(a, dict):
                    amenities.append(a.get('name') or a.get('label') or '')
            break

    # --- notes / description ---
    notes = item.get('description') or item.get('notes') or item.get('summary') or ''

    # --- contact ---
    contact_phone = contact_email = contact_name = None
    for key in ('phone', 'phoneNumber', 'contactPhone'):
        raw = item.get(key)
        if raw:
            digits = _digits_only(raw)
            contact_phone = _fmt_phone(digits)
            break
    contact_email = item.get('email') or item.get('contactEmail')
    contact_name  = item.get('contactName') or item.get('managerName') or item.get('agentName')

    # --- move-in date ---
    move_in = item.get('availableDate') or item.get('moveInDate') or item.get('availableOn')
    if isinstance(move_in, dict):
        move_in = move_in.get('raw') or move_in.get('text') or ''

    return {
        'title': title,
        'address': address,
        'price': price,
        'beds': beds,
        'baths': baths,
        'sqft': sqft,
        'lat': lat,
        'lon': lon,
        'housing_type': housing_type,
        'source_url': url,
        'notes': notes,
        'amenities': [a for a in amenities if a],
        'photo_urls': photo_urls,
        'contact_phone': contact_phone,
        'contact_email': contact_email,
        'contact_name': contact_name,
        'move_in_date': move_in,
    }


# ---------------------------------------------------------------------------
# DOM fallback — parse rendered HTML when API interception misses
# ---------------------------------------------------------------------------

def _parse_dom_listings(page):
    """Extract listings from the rendered DOM as a last resort."""
    from playwright.sync_api import TimeoutError as PWTimeout
    listings = []
    try:
        page.wait_for_selector('article.placard, div[data-listingid]', timeout=8000)
    except PWTimeout:
        return listings

    cards = page.query_selector_all('article.placard, div[data-listingid]')
    for card in cards:
        try:
            item = {}
            # listing ID / URL
            lid = card.get_attribute('data-listingid') or ''
            link = card.query_selector('a[href*="apartments.com"]')
            if link:
                item['url'] = link.get_attribute('href') or ''
            elif lid:
                item['url'] = f'https://www.apartments.com/{lid}/'

            # price
            price_el = card.query_selector('.price-range, .rent, [class*="price"]')
            if price_el:
                m = re.search(r'\$([0-9,]+)', price_el.inner_text())
                if m:
                    item['rent'] = int(m.group(1).replace(',', ''))

            # address
            addr_el = card.query_selector('.property-address, [class*="address"], h3')
            if addr_el:
                item['addressFull'] = addr_el.inner_text().strip()

            # name / title
            name_el = card.query_selector('.property-title, [class*="title"], h4')
            if name_el:
                item['propertyName'] = name_el.inner_text().strip()

            # beds/baths
            beds_el = card.query_selector('[class*="bed"], .beds')
            if beds_el:
                m = re.search(r'(\d+)', beds_el.inner_text())
                if m:
                    item['beds'] = int(m.group(1))
            baths_el = card.query_selector('[class*="bath"], .baths')
            if baths_el:
                m = re.search(r'(\d+)', baths_el.inner_text())
                if m:
                    item['baths'] = float(m.group(1))

            # photo
            img = card.query_selector('img[src*="http"]')
            if img:
                item['photos'] = [{'url': img.get_attribute('src')}]

            listings.append(item)
        except Exception:
            continue
    return listings


# ---------------------------------------------------------------------------
# Main crawl function
# ---------------------------------------------------------------------------

def crawl_apartments_com(search_url, max_listings=50):
    from scripts.scrape_tracker import ScrapeTracker
    tracker = ScrapeTracker()
    run = tracker.start_run('apartments_com')

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print('ERROR: playwright not installed.')
        print('  pip install playwright')
        print('  playwright install chromium')
        run.error('playwright not installed')
        run.finish('failed')
        return

    print(f'Launching Chromium for: {search_url}')

    intercepted_listings = []

    def on_response(response):
        if intercepted_listings:
            return  # already got what we need
        url = response.url
        ct = response.headers.get('content-type', '')
        if 'json' not in ct:
            return
        # Only inspect apartments.com API responses
        if 'apartments.com' not in url:
            return
        try:
            data = response.json()
        except Exception:
            return
        found = _extract_listing_array(data)
        if found:
            print(f'  Intercepted {len(found)} listings from: {url}')
            intercepted_listings.extend(found)

    with sync_playwright() as pw:
        chrome_args = [
            '--disable-blink-features=AutomationControlled',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-infobars',
        ]
        user_data = str(PROJECT_ROOT / '_chrome_profile')
        print('  Launching Chrome with persistent profile...')
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data,
                channel='chrome',
                headless=False,
                args=chrome_args,
                ignore_default_args=['--enable-automation'],
                viewport={'width': 1366, 'height': 768},
                locale='en-US',
            )
            print('  Using real Chrome (persistent context)')
        except Exception:
            ctx = pw.chromium.launch_persistent_context(
                user_data,
                headless=False,
                args=chrome_args,
                ignore_default_args=['--enable-automation'],
                viewport={'width': 1366, 'height': 768},
                locale='en-US',
            )
            print('  Using bundled Chromium (persistent context)')

        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on('response', on_response)

        # Visit homepage first to establish cookies
        print('  Visiting apartments.com homepage first...')
        try:
            page.goto('https://www.apartments.com/', wait_until='domcontentloaded', timeout=30000)
        except PWTimeout:
            print('  Homepage load timed out — proceeding anyway')

        page.wait_for_timeout(3000)

        # Check if homepage itself is blocked
        if 'Access Denied' in (page.title() or ''):
            print('  Akamai blocked even the homepage — IP is still flagged.')
            ctx.close()
            return

        # Simulate human interaction on homepage
        page.mouse.move(400, 350)
        page.wait_for_timeout(800)
        page.evaluate('window.scrollBy(0, 200)')
        page.wait_for_timeout(1200)

        print(f'  Navigating to search: {search_url}')
        try:
            page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        except PWTimeout:
            print('  Page load timed out (domcontentloaded) — proceeding anyway')

        # If Akamai served a challenge page, wait for its JS to run
        title = page.title() or ''
        if 'Access Denied' in title or 'denied' in title.lower():
            print('  Akamai challenge detected — waiting up to 25s for it to resolve...')
            try:
                page.wait_for_function(
                    "!document.title.includes('Access Denied')",
                    timeout=25000,
                )
                print('  Challenge passed, page loaded.')
            except (PWTimeout, Exception):
                print('  Challenge did not resolve — Akamai is blocking this IP/fingerprint.')

        # Simulate scrolling to trigger lazy-loaded content
        page.mouse.move(640, 400)
        page.wait_for_timeout(2000)
        page.evaluate('window.scrollBy(0, 300)')
        page.wait_for_timeout(1500)
        page.evaluate('window.scrollBy(0, 400)')

        # Give XHR responses time to arrive
        print(f'  Waiting {PAGE_LOAD_WAIT}s for XHR listing data...')
        page.wait_for_timeout(PAGE_LOAD_WAIT * 1000)

        raw_listings = list(intercepted_listings)

        if not raw_listings:
            print('  API interception got nothing — trying DOM fallback...')
            raw_listings = _parse_dom_listings(page)

        if not raw_listings:
            # Dump the page source for debugging
            debug_path = OUTPUTS_DIR / '_debug_apartments_com.html'
            debug_path.write_text(page.content(), encoding='utf-8')
            print(f'  Saved page source to {debug_path} for inspection.')
            print('  Apartments.com may be blocking headless Chromium or the page structure changed.')

        ctx.close()

    if not raw_listings:
        run.finish('completed')
        return

    run.found(len(raw_listings))
    print(f'  Parsing {len(raw_listings)} raw listings...')
    units_data = load_units()
    existing_urls = {u.get('source_url') for u in units_data.get('units', [])}

    added = 0
    skipped = 0
    for item in raw_listings[:max_listings]:
        if added >= max_listings:
            break

        info = parse_listing(item)

        # Skip if no useful data
        if not info['source_url'] and not info['address']:
            continue

        # Deduplicate by URL, then by address
        if info['source_url'] and info['source_url'] in existing_urls:
            skipped += 1
            continue

        unit_id = make_unit_id(units_data.get('units', []))

        # Download photos
        photo_paths = []
        photo_sources = []
        photo_dir = PHOTOS_DIR / unit_id
        for i, img_url in enumerate(info['photo_urls'][:MAX_PHOTOS]):
            try:
                photo_dir.mkdir(parents=True, exist_ok=True)
                dest = photo_dir / f'photo-{i + 1}.jpg'
                download_image(img_url, dest)
                rel = (Path('outputs') / 'photos' / unit_id / dest.name).as_posix()
                photo_paths.append(rel)
                photo_sources.append(img_url)
                run.photo()
                time.sleep(0.3)
            except Exception as e:
                print(f'    Photo {i+1} failed: {e}')
                run.error(f'Photo download: {e}')

        unit = {
            'id': unit_id,
            'title': info['title'],
            'address': info['address'],
            'price': info['price'],
            'beds': info['beds'],
            'baths': info['baths'],
            'sqft': info['sqft'],
            'lat': info['lat'],
            'lon': info['lon'],
            'housing_type': info['housing_type'],
            'source': 'Apartments.com',
            'source_url': info['source_url'],
            'photos': photo_paths,
            'photo_sources': photo_sources,
            'notes': info['notes'],
            'amenities': info['amenities'],
            'has_washer_dryer': None,
            'is_gated': None,
            'age_restriction': None,
            'move_in_date': info['move_in_date'],
            'contact_phone': info['contact_phone'],
            'contact_email': info['contact_email'],
            'contact_name': info['contact_name'],
            'quality_rating': None,
            'quality_notes': None,
            'flooring_type': None,
            'kitchen_style': None,
            'outdoor_space': None,
            'size_impression': None,
            'date_added': datetime.now().isoformat(),
        }

        units_data['units'].append(unit)
        if info['source_url']:
            existing_urls.add(info['source_url'])
        added += 1
        run.added()
        print(f'  + {unit_id}: {info["address"] or info["title"]} — ${info["price"]}/mo, {info["beds"]}bd')
        time.sleep(0.5)

    run.skipped(skipped)
    if added:
        save_units(units_data)
        print(f'\nAdded {added} new units from Apartments.com.')
    else:
        print('\nNo new units added (all duplicates or failed to parse).')
    run.finish()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Scrape Apartments.com rental listings via Playwright')
    parser.add_argument('--max', type=int, default=50, help='Max listings to add (default: 50)')
    parser.add_argument('--url', type=str, default=None, help='Override search URL')
    args = parser.parse_args()

    config = load_config()
    url = args.url or build_search_url(config)
    crawl_apartments_com(url, max_listings=args.max)
    print('\nRun: python scripts/generate-html.py   to refresh the summary page')


if __name__ == '__main__':
    main()
