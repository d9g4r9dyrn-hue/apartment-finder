#!/usr/bin/env python3
"""
Realtor.com scraper for rental listings near Clearwater, FL.

Realtor.com is a Next.js app — listing data is embedded as __NEXT_DATA__ JSON
in the search results page, so no JS execution is needed.

Usage:
  python -m scripts.scrapers.realtor_com
  python -m scripts.scrapers.realtor_com --max 50
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
PHOTOS_DIR = OUTPUTS_DIR / 'photos'
UNITS_JSON = OUTPUTS_DIR / 'units.json'
CONFIG_JSON = PROJECT_ROOT / 'config.json'

from scripts.scrapers.common import download_image

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
}

MAX_PHOTOS = 8


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
    print(f"Saved {data['total_units']} units to {UNITS_JSON}")


def make_unit_id(units):
    max_id = -1
    for u in units:
        m = re.match(r'unit-(\d+)$', u.get('id') or '')
        if m:
            max_id = max(max_id, int(m.group(1)))
    return f'unit-{max_id + 1:04d}'


def build_search_url(config):
    """Build Realtor.com rental search URL from config.json parameters."""
    min_price = config.get('min_price', 1000)
    max_price = config.get('max_price', 2000)
    min_beds = config.get('min_beds', 2)
    # Realtor.com URL format: /apartments/City_ST/beds-N-price-min-X-max-Y/
    slug = f'beds-{min_beds}-price-min-{min_price}-price-max-{max_price}'
    return f'https://www.realtor.com/apartments/Clearwater_FL/{slug}'


def extract_next_data(html):
    """Extract the __NEXT_DATA__ JSON embedded in a Next.js page."""
    soup = BeautifulSoup(html, 'html.parser')
    tag = soup.find('script', {'id': '__NEXT_DATA__'})
    if not tag:
        return None
    try:
        return json.loads(tag.string)
    except Exception:
        return None


def extract_listings_from_next_data(next_data):
    """Walk the __NEXT_DATA__ tree to find the array of listing results."""
    if not next_data:
        return []
    # Try known paths in Realtor.com's data structure
    try:
        props = next_data.get('props', {}).get('pageProps', {})
        # Search results page
        for key in ('searchResults', 'properties', 'listingsProps'):
            if key in props:
                sr = props[key]
                # Try common sub-keys
                for sub in ('home_search', 'results', 'listings', 'data'):
                    if isinstance(sr, dict) and sub in sr:
                        r = sr[sub]
                        if isinstance(r, dict) and 'results' in r:
                            return r['results']
                        if isinstance(r, list):
                            return r
                if isinstance(sr, list):
                    return sr
        # Fallback: scan all values for a list that looks like listings
        def find_list(obj, depth=0):
            if depth > 6:
                return None
            if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
                if 'list_price' in obj[0] or 'property_id' in obj[0] or 'listing_id' in obj[0]:
                    return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    r = find_list(v, depth + 1)
                    if r:
                        return r
            return None
        return find_list(next_data) or []
    except Exception:
        return []


def parse_listing(item):
    """Parse a single listing dict from __NEXT_DATA__ into our unit format."""
    desc = item.get('description') or {}
    location = item.get('location') or {}
    addr_info = location.get('address') or {}
    coord = addr_info.get('coordinate') or {}

    price = item.get('list_price') or item.get('list_price_max') or item.get('list_price_min') or item.get('price') or 0
    if isinstance(price, dict):
        price = price.get('min') or price.get('list') or 0
    try:
        price = int(price)
    except Exception:
        price = 0

    beds = desc.get('beds') or desc.get('beds_max') or desc.get('beds_min') or item.get('beds') or 0
    try:
        beds = int(beds)
    except Exception:
        beds = 0

    baths_raw = desc.get('baths_consolidated') or desc.get('baths') or desc.get('baths_max') or desc.get('baths_min') or item.get('baths')
    try:
        baths = float(baths_raw) if baths_raw else None
    except Exception:
        baths = None

    sqft_raw = desc.get('sqft') or desc.get('sqft_max') or desc.get('sqft_min') or item.get('sqft')
    try:
        sqft = int(sqft_raw) if sqft_raw else None
    except Exception:
        sqft = None

    # Address
    line = addr_info.get('line', '')
    city = addr_info.get('city', '')
    state = addr_info.get('state_code', 'FL')
    zipcode = addr_info.get('postal_code', '')
    address_parts = [p for p in [line, city, f'{state} {zipcode}'.strip()] if p]
    address = ', '.join(address_parts) or desc.get('name', '')

    lat = coord.get('lat') or coord.get('latitude')
    lon = coord.get('lon') or coord.get('longitude')
    try:
        lat = float(lat) if lat else None
        lon = float(lon) if lon else None
    except Exception:
        lat = lon = None

    # Listing URL
    permalink = item.get('permalink') or item.get('listing_id') or ''
    if permalink and not permalink.startswith('http'):
        source_url = f'https://www.realtor.com/realestateandhomes-detail/{permalink}'
    else:
        source_url = permalink or ''

    # Title
    title = desc.get('name') or address or 'Realtor.com listing'

    # Description text
    notes = desc.get('text') or item.get('description_text') or ''

    # Housing type
    prop_type = (desc.get('type') or item.get('property_type') or '').lower()
    if 'apartment' in prop_type or 'condo' in prop_type or 'flat' in prop_type:
        housing_type = 'Apartment'
    elif 'house' in prop_type or 'single' in prop_type or 'home' in prop_type:
        housing_type = 'House'
    elif 'townhouse' in prop_type or 'town' in prop_type:
        housing_type = 'Townhouse'
    else:
        housing_type = prop_type.capitalize() if prop_type else 'Other'

    # Photos
    photos_raw = item.get('photos') or item.get('primary_photo') or []
    if isinstance(photos_raw, dict):
        photos_raw = [photos_raw]
    photo_urls = []
    for p in photos_raw:
        if isinstance(p, dict):
            href = p.get('href') or p.get('url') or p.get('src') or ''
        else:
            href = str(p)
        if href:
            photo_urls.append(href)

    # Contact info (Realtor.com may include agent/broker info)
    advertiser = item.get('advertisers') or []
    if isinstance(advertiser, dict):
        advertiser = [advertiser]
    contact_phone = None
    contact_email = None
    contact_name = None
    for adv in (advertiser if isinstance(advertiser, list) else []):
        if isinstance(adv, dict):
            phones = adv.get('phones') or []
            if isinstance(phones, list) and phones:
                ph = phones[0].get('number') if isinstance(phones[0], dict) else str(phones[0])
                digits = re.sub(r'\D', '', ph or '')
                if len(digits) == 10:
                    contact_phone = f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'
                elif len(digits) == 11 and digits[0] == '1':
                    contact_phone = f'({digits[1:4]}) {digits[4:7]}-{digits[7:]}'
            contact_name = adv.get('name') or contact_name
            contact_email = adv.get('email') or contact_email

    # Amenities from details array
    amenities = []
    for detail_group in (item.get('details') or []):
        if isinstance(detail_group, dict):
            for text in (detail_group.get('text') or []):
                if isinstance(text, str):
                    amenities.append(text)

    pet_policy = item.get('pet_policy') or {}
    if pet_policy.get('cats'):
        amenities.append('Cats Allowed')
    if pet_policy.get('dogs'):
        amenities.append('Dogs Allowed')

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
        'source_url': source_url,
        'notes': notes,
        'photo_urls': photo_urls,
        'amenities': amenities,
        'contact_phone': contact_phone,
        'contact_email': contact_email,
        'contact_name': contact_name,
    }


def _fetch_with_playwright(search_url):
    """Use a real browser to fetch listings, bypassing Kasada bot protection."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None

    print('  Launching Chrome for Realtor.com...')
    listings = None

    with sync_playwright() as pw:
        chrome_args = [
            '--disable-blink-features=AutomationControlled',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-infobars',
        ]
        user_data = str(PROJECT_ROOT / '_chrome_profile_realtor')
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

        try:
            page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        except PWTimeout:
            print('  Page load timed out — proceeding anyway')

        # Wait for Kasada challenge to resolve and content to render
        print('  Waiting for page to fully load...')
        page.wait_for_timeout(8000)

        # Simulate scrolling
        page.mouse.move(640, 400)
        page.wait_for_timeout(1000)
        page.evaluate('window.scrollBy(0, 300)')
        page.wait_for_timeout(2000)

        html = page.content()
        ctx.close()

    if not html or 'could not be processed' in html.lower():
        print('  Kasada blocked the request.')
        return []

    next_data = extract_next_data(html)
    if next_data:
        listings = extract_listings_from_next_data(next_data)
    else:
        # Try parsing DOM directly for listing data
        print('  No __NEXT_DATA__ found, trying DOM parse...')
        listings = _parse_dom(html)

    return listings if listings else []


def _parse_dom(html):
    """Fallback: parse listing cards from rendered HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    listings = []
    cards = soup.select('[data-testid="card-content"], .BasePropertyCard_propertyCardWrap__30VCU, article')
    for card in cards:
        item = {}
        price_el = card.select_one('[data-testid="card-price"], .card-price')
        if price_el:
            m = re.search(r'\$([0-9,]+)', price_el.get_text())
            if m:
                item['list_price'] = int(m.group(1).replace(',', ''))
        addr_el = card.select_one('[data-testid="card-address"], .card-address')
        if addr_el:
            item['description'] = {'name': addr_el.get_text(strip=True)}
            item['location'] = {'address': {'line': addr_el.get_text(strip=True)}}
        link = card.select_one('a[href*="/realestateandhomes-detail/"]')
        if link:
            href = link.get('href', '')
            if not href.startswith('http'):
                href = 'https://www.realtor.com' + href
            item['permalink'] = href
        if item:
            listings.append(item)
    return listings


def crawl_realtor_com(search_url, max_listings=50):
    from scripts.scrape_tracker import ScrapeTracker
    tracker = ScrapeTracker()
    run = tracker.start_run('realtor_com')

    print(f'Fetching Realtor.com: {search_url}')

    # Try Playwright first (handles Kasada bot protection), fall back to HTTP
    raw_listings = _fetch_with_playwright(search_url)

    if raw_listings is None:
        print('  Playwright unavailable, trying HTTP...')
        sess = curl_requests.Session(impersonate='chrome')
        try:
            r = sess.get(search_url, timeout=20, headers=HEADERS)
            r.raise_for_status()
        except Exception as e:
            print(f'  ERROR fetching search page: {e}')
            run.error(str(e))
            run.finish('failed')
            return

        next_data = extract_next_data(r.text)
        if not next_data:
            print('  ERROR: Could not find __NEXT_DATA__ in page.')
            run.error('No __NEXT_DATA__ found')
            run.finish('failed')
            return

        raw_listings = extract_listings_from_next_data(next_data)

    run.found(len(raw_listings))
    print(f'  Found {len(raw_listings)} listings in page data')
    if not raw_listings:
        run.finish('completed')
        return

    units_data = load_units()
    existing_urls = {u.get('source_url') for u in units_data.get('units', [])}

    added = 0
    skipped = 0
    for item in raw_listings[:max_listings]:
        if added >= max_listings:
            break

        info = parse_listing(item)
        if not info['source_url'] or info['source_url'] in existing_urls:
            if info['source_url'] in existing_urls:
                print(f'  Skip duplicate: {info["source_url"]}')
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
            'source': 'Realtor.com',
            'source_url': info['source_url'],
            'photos': photo_paths,
            'photo_sources': photo_sources,
            'notes': info['notes'],
            'amenities': info.get('amenities', []),
            'has_washer_dryer': None,
            'is_gated': None,
            'age_restriction': None,
            'move_in_date': None,
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
        existing_urls.add(info['source_url'])
        added += 1
        run.added()
        print(f'  + {unit_id}: {info["address"]} — ${info["price"]}/mo, {info["beds"]}bd')
        time.sleep(1)

    run.skipped(skipped)
    if added:
        save_units(units_data)
        print(f'\nAdded {added} new units from Realtor.com.')
    else:
        print('\nNo new units added (all duplicates or no results).')
    run.finish()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Scrape Realtor.com rental listings')
    parser.add_argument('--max', type=int, default=50, help='Max listings to fetch (default: 50)')
    parser.add_argument('--url', type=str, default=None, help='Override search URL')
    args = parser.parse_args()

    config = load_config()
    search_url = args.url or build_search_url(config)
    crawl_realtor_com(search_url, max_listings=args.max)
    print('\nRun: python scripts/generate-html.py   to refresh the summary page')


if __name__ == '__main__':
    main()
