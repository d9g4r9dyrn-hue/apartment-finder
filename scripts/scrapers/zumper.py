#!/usr/bin/env python3
"""
Zumper.com scraper for rental listings near Clearwater, FL.

Zumper is a React app with server-side rendered listing cards.
Uses Playwright to load pages and intercept API responses when available,
with DOM parsing as a fallback.

Usage:
  python -m scripts.scrapers.zumper
  python -m scripts.scrapers.zumper --max 50
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
    min_price = config.get('min_price', 1000)
    max_price = config.get('max_price', 2000)
    min_beds = config.get('min_beds', 2)
    return (
        f'https://www.zumper.com/apartments-for-rent/clearwater-fl'
        f'?price-minimum={min_price}&price-maximum={max_price}'
        f'&beds-minimum={min_beds}'
    )


def _parse_price(text):
    """Parse price from text like '$1,500' or '$1,500-$2,000'. Returns the lower bound."""
    if not text:
        return 0
    m = re.search(r'\$([0-9,]+)', text)
    if m:
        try:
            return int(m.group(1).replace(',', ''))
        except ValueError:
            pass
    return 0


def _parse_beds_baths(text):
    """Parse beds/baths from text like '2-3 beds / 1-2 baths' or '2 beds / 1 baths'."""
    beds = 0
    baths = None
    if not text:
        return beds, baths
    beds_m = re.search(r'(\d+)', text.split('/')[0] if '/' in text else text)
    if beds_m:
        beds = int(beds_m.group(1))
    if '/' in text:
        baths_part = text.split('/')[1]
        baths_m = re.search(r'(\d+\.?\d*)', baths_part)
        if baths_m:
            baths = float(baths_m.group(1))
    return beds, baths


def _parse_sqft(text):
    """Parse sqft from text like '1,200 sqft' or '900-1,200 sqft'."""
    if not text:
        return None
    m = re.search(r'(\d[\d,]*)\s*(?:sq\s*ft|sqft)', text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1).replace(',', ''))
        except ValueError:
            pass
    return None


def _fetch_detail_page(url, session=None):
    """Fetch a listing detail page and extract extra info (photos, sqft, description)."""
    try:
        sess = session or curl_requests.Session(impersonate='chrome')
        r = sess.get(url, timeout=20, headers=HEADERS)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

        photo_urls = []
        seen_ids = set()
        # Look for image tags with zumper CDN URLs
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or ''
            if 'zumpercdn.com' in src:
                img_id = re.search(r'zumpercdn\.com/(\d+)', src)
                key = img_id.group(1) if img_id else src
                if key not in seen_ids:
                    seen_ids.add(key)
                    photo_urls.append(src)
                    if len(photo_urls) >= MAX_PHOTOS:
                        break

        # Also check for background images in style attributes
        for el in soup.find_all(style=True):
            style = el.get('style', '')
            urls = re.findall(r'url\(["\']?(https://[^"\')\s]+zumpercdn\.com[^"\')\s]*)["\']?\)', style)
            for u in urls:
                img_id = re.search(r'zumpercdn\.com/(\d+)', u)
                key = img_id.group(1) if img_id else u
                if key not in seen_ids:
                    seen_ids.add(key)
                    photo_urls.append(u)
                    if len(photo_urls) >= MAX_PHOTOS:
                        break

        # Extract description
        description = ''
        for el in soup.find_all(['p', 'div', 'span']):
            text = el.get_text(strip=True)
            if len(text) > 100 and not any(kw in text.lower() for kw in ['cookie', 'privacy', 'terms']):
                description = text
                break

        # Extract sqft from page
        sqft = None
        page_text = soup.get_text()
        sqft = _parse_sqft(page_text)

        return {
            'photo_urls': photo_urls,
            'description': description,
            'sqft': sqft,
        }
    except Exception as e:
        print(f'    Detail fetch failed: {e}')
        return {'photo_urls': [], 'description': '', 'sqft': None}


def _parse_dom(html):
    """Parse listing cards from rendered Zumper HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    listings = []

    # Find all links that look like listing detail links
    listing_links = soup.find_all('a', href=re.compile(
        r'/(apartment-buildings/p\d+|address/)[^"]*'
    ))

    seen_urls = set()
    for link in listing_links:
        href = link.get('href', '')
        if not href or href in seen_urls:
            continue

        # Build full URL
        if not href.startswith('http'):
            full_url = f'https://www.zumper.com{href}'
        else:
            full_url = href

        # Find the parent card container by walking up from the link
        card = link
        for _ in range(8):
            parent = card.parent
            if parent is None:
                break
            card = parent
            # Stop when we find a container that has price info
            card_text = card.get_text()
            if '$' in card_text and ('bed' in card_text.lower() or 'bath' in card_text.lower()):
                break

        card_text = card.get_text(' ', strip=True)

        # Extract title from the link or its heading child
        title_el = link.find(['h2', 'h3', 'h4', 'h5']) or link
        title = title_el.get_text(strip=True) or ''

        # Extract address - usually near the title
        address = ''
        # Look for text that matches address patterns
        addr_m = re.search(
            r'(\d+\s+[A-Za-z][\w\s]+(?:St|Ave|Blvd|Dr|Rd|Ln|Way|Ct|Pl|Cir|Pkwy|Hwy)[^,]*,\s*\w[^,]+,\s*FL\s*\d{5})',
            card_text
        )
        if addr_m:
            address = addr_m.group(1)
        elif title and (',' in title or re.search(r'\d+\s+\w', title)):
            address = title

        # Extract price
        price = _parse_price(card_text)

        # Extract beds/baths
        beds, baths = _parse_beds_baths(card_text)

        # Extract sqft
        sqft = _parse_sqft(card_text)

        # Extract photo from card's img tags
        photo_urls = []
        for img in card.find_all('img'):
            src = img.get('src') or img.get('data-src') or ''
            if 'zumpercdn.com' in src:
                src_clean = re.sub(r'\?.*$', '', src)
                if src_clean not in photo_urls:
                    photo_urls.append(src_clean)

        # Amenities
        amenities = []
        amenity_text = re.findall(r'([A-Z][a-z]+(?:\s+[a-z]+)*)\s*\|', card_text)
        if amenity_text:
            amenities = [a.strip() for a in amenity_text if a.strip()]

        # Housing type
        housing_type = 'Apartment'
        type_m = re.search(r'(Condo|House|Townhouse|Apartment|Duplex)\s+for\s+rent', card_text, re.IGNORECASE)
        if type_m:
            housing_type = type_m.group(1).capitalize()

        if not price and not title:
            continue

        seen_urls.add(href)
        listings.append({
            'title': title or address or 'Zumper listing',
            'address': address,
            'price': price,
            'beds': beds,
            'baths': baths,
            'sqft': sqft,
            'source_url': full_url,
            'photo_urls': photo_urls,
            'amenities': amenities,
            'housing_type': housing_type,
        })

    return listings


def _fetch_with_playwright(search_url, max_pages=3):
    """Use Playwright to load Zumper and capture listings."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None

    print('  Launching Chrome for Zumper...')
    all_listings = []
    api_listings = []

    with sync_playwright() as pw:
        chrome_args = [
            '--disable-blink-features=AutomationControlled',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-infobars',
        ]
        user_data = str(PROJECT_ROOT / '_chrome_profile_zumper')
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

        # Intercept API responses to capture listing data
        def handle_response(response):
            url = response.url
            if '/api/' in url and response.status == 200:
                try:
                    body = response.json()
                    if isinstance(body, list) and len(body) > 0 and isinstance(body[0], dict):
                        if any(k in body[0] for k in ['address', 'price', 'bedrooms', 'building_name']):
                            api_listings.extend(body)
                            print(f'    Intercepted API: {len(body)} listings from {url[:80]}')
                    elif isinstance(body, dict):
                        for key in ('listables', 'listings', 'results', 'data'):
                            if key in body and isinstance(body[key], list):
                                api_listings.extend(body[key])
                                print(f'    Intercepted API: {len(body[key])} listings from {url[:80]}')
                                break
                except Exception:
                    pass

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on('response', handle_response)

        for page_num in range(1, max_pages + 1):
            url = search_url if page_num == 1 else f'{search_url}&page={page_num}'
            print(f'  Loading page {page_num}: {url}')

            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
            except PWTimeout:
                print('  Page load timed out - proceeding anyway')

            page.wait_for_timeout(5000)

            # Scroll to trigger lazy loading
            page.evaluate('window.scrollBy(0, 500)')
            page.wait_for_timeout(1500)
            page.evaluate('window.scrollBy(0, 1000)')
            page.wait_for_timeout(1500)
            page.evaluate('window.scrollBy(0, 1500)')
            page.wait_for_timeout(1500)

            html = page.content()
            dom_listings = _parse_dom(html)
            if dom_listings:
                all_listings.extend(dom_listings)
                print(f'    Parsed {len(dom_listings)} listings from DOM (page {page_num})')

            # Check if there's a next page
            has_next = page.query_selector('a[href*="page="]') is not None
            if not has_next and page_num < max_pages:
                print(f'  No more pages after page {page_num}')
                break

            if page_num < max_pages:
                time.sleep(2)

        ctx.close()

    # Prefer API-intercepted listings if we got them
    if api_listings:
        return _normalize_api_listings(api_listings)

    return all_listings


def _normalize_api_listings(api_items):
    """Normalize listings captured from intercepted API responses."""
    listings = []
    for item in api_items:
        if not isinstance(item, dict):
            continue

        title = (
            item.get('building_name')
            or item.get('name')
            or item.get('address')
            or 'Zumper listing'
        )

        address_parts = []
        if item.get('address'):
            address_parts.append(item['address'])
        if item.get('city'):
            address_parts.append(item['city'])
        if item.get('state_code') or item.get('state'):
            state = item.get('state_code') or item.get('state', '')
            zipcode = item.get('zip_code') or item.get('postal_code') or ''
            address_parts.append(f'{state} {zipcode}'.strip())
        address = ', '.join(address_parts) or title

        price = item.get('price') or item.get('min_price') or item.get('price_min') or 0
        try:
            price = int(price)
        except (ValueError, TypeError):
            price = 0

        beds = item.get('bedrooms') or item.get('beds') or item.get('min_bedrooms') or 0
        try:
            beds = int(beds)
        except (ValueError, TypeError):
            beds = 0

        baths = item.get('bathrooms') or item.get('baths') or item.get('min_bathrooms')
        try:
            baths = float(baths) if baths else None
        except (ValueError, TypeError):
            baths = None

        sqft = item.get('sqft') or item.get('square_feet') or item.get('min_sqft')
        try:
            sqft = int(sqft) if sqft else None
        except (ValueError, TypeError):
            sqft = None

        lat = item.get('latitude') or item.get('lat')
        lon = item.get('longitude') or item.get('lng') or item.get('lon')
        try:
            lat = float(lat) if lat else None
            lon = float(lon) if lon else None
        except (ValueError, TypeError):
            lat = lon = None

        # Build source URL
        source_url = item.get('url') or ''
        if not source_url:
            listing_id = item.get('id') or item.get('listing_id') or item.get('group_id') or ''
            if listing_id:
                slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
                source_url = f'https://www.zumper.com/apartment-buildings/p{listing_id}/{slug}'
        if source_url and not source_url.startswith('http'):
            source_url = f'https://www.zumper.com{source_url}'

        # Photos — Zumper API returns image_ids (numeric IDs for their CDN)
        photo_urls = []
        image_ids = item.get('image_ids') or []
        if isinstance(image_ids, list) and image_ids:
            for img_id in image_ids:
                photo_urls.append(
                    f'https://img.zumpercdn.com/{int(img_id)}/1280x960?fit=crop&w=1280&h=960'
                )
        else:
            for key in ('photos', 'images', 'image_urls', 'photo_urls'):
                photos_raw = item.get(key, [])
                if isinstance(photos_raw, list):
                    for p in photos_raw:
                        if isinstance(p, dict):
                            u = p.get('url') or p.get('src') or p.get('href') or ''
                        elif isinstance(p, (int, float)):
                            u = f'https://img.zumpercdn.com/{int(p)}/1280x960?fit=crop&w=1280&h=960'
                        else:
                            u = str(p) if p else ''
                        if u and isinstance(u, str) and u.startswith('http'):
                            photo_urls.append(u)
        if not photo_urls:
            img_url = item.get('image_url') or item.get('image') or ''
            if img_url:
                photo_urls.append(str(img_url))

        # Housing type
        prop_type = str(item.get('property_type') or item.get('category') or '').lower()
        if 'condo' in prop_type:
            housing_type = 'Condo'
        elif 'house' in prop_type or 'single' in prop_type:
            housing_type = 'House'
        elif 'townhouse' in prop_type or 'town' in prop_type:
            housing_type = 'Townhouse'
        elif 'duplex' in prop_type:
            housing_type = 'Duplex'
        else:
            housing_type = 'Apartment'

        # Amenities
        amenities = item.get('amenities') or []
        if isinstance(amenities, str):
            amenities = [a.strip() for a in amenities.split(',') if a.strip()]

        listings.append({
            'title': title,
            'address': address,
            'price': price,
            'beds': beds,
            'baths': baths,
            'sqft': sqft,
            'lat': lat,
            'lon': lon,
            'source_url': source_url,
            'photo_urls': photo_urls[:MAX_PHOTOS],
            'amenities': amenities,
            'housing_type': housing_type,
        })

    return listings


def crawl_zumper(search_url, max_listings=50):
    from scripts.scrape_tracker import ScrapeTracker
    tracker = ScrapeTracker()
    run = tracker.start_run('zumper')

    print(f'Fetching Zumper: {search_url}')

    raw_listings = _fetch_with_playwright(search_url)

    if raw_listings is None:
        print('  Playwright unavailable, trying HTTP fallback...')
        sess = curl_requests.Session(impersonate='chrome')
        try:
            r = sess.get(search_url, timeout=20, headers=HEADERS)
            r.raise_for_status()
        except Exception as e:
            print(f'  ERROR fetching search page: {e}')
            run.error(str(e))
            run.finish('failed')
            return

        raw_listings = _parse_dom(r.text)

    run.found(len(raw_listings))
    print(f'  Found {len(raw_listings)} total listings')
    if not raw_listings:
        run.finish('completed')
        return

    units_data = load_units()
    existing_urls = {u.get('source_url') for u in units_data.get('units', [])}

    # Deduplicate within the batch by source_url
    seen = set()
    unique_listings = []
    for item in raw_listings:
        url = item.get('source_url', '')
        if url and url not in seen:
            seen.add(url)
            unique_listings.append(item)
        elif not url:
            unique_listings.append(item)

    added = 0
    skipped = 0
    sess = curl_requests.Session(impersonate='chrome')

    for item in unique_listings[:max_listings]:
        if added >= max_listings:
            break

        source_url = item.get('source_url', '')
        if source_url in existing_urls:
            print(f'  Skip duplicate: {source_url}')
            skipped += 1
            continue

        # Fetch detail page for extra info if we don't have photos
        if not item.get('photo_urls') and source_url:
            print(f'    Fetching detail page for photos...')
            detail = _fetch_detail_page(source_url, session=sess)
            if detail.get('photo_urls'):
                item['photo_urls'] = detail['photo_urls']
            if detail.get('sqft') and not item.get('sqft'):
                item['sqft'] = detail['sqft']
            if detail.get('description') and not item.get('notes'):
                item['notes'] = detail['description']
            time.sleep(1.5)

        unit_id = make_unit_id(units_data.get('units', []))

        # Download photos — use curl_cffi to bypass CDN bot detection
        photo_paths = []
        photo_sources = []
        photo_dir = PHOTOS_DIR / unit_id
        for i, img_url in enumerate(item.get('photo_urls', [])[:MAX_PHOTOS]):
            try:
                photo_dir.mkdir(parents=True, exist_ok=True)
                dest = photo_dir / f'photo-{i + 1}.jpg'
                r = sess.get(img_url, timeout=20, headers={
                    'User-Agent': HEADERS['User-Agent'],
                    'Referer': 'https://www.zumper.com/',
                })
                r.raise_for_status()
                with open(dest, 'wb') as f:
                    f.write(r.content)
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
            'title': item.get('title', 'Zumper listing'),
            'address': item.get('address', ''),
            'price': item.get('price', 0),
            'beds': item.get('beds', 0),
            'baths': item.get('baths'),
            'sqft': item.get('sqft'),
            'lat': item.get('lat'),
            'lon': item.get('lon'),
            'housing_type': item.get('housing_type', 'Apartment'),
            'source': 'Zumper',
            'source_url': source_url,
            'photos': photo_paths,
            'photo_sources': photo_sources,
            'notes': item.get('notes', ''),
            'amenities': item.get('amenities', []),
            'has_washer_dryer': None,
            'is_gated': None,
            'age_restriction': None,
            'move_in_date': None,
            'contact_phone': None,
            'contact_email': None,
            'contact_name': None,
            'quality_rating': None,
            'quality_notes': None,
            'flooring_type': None,
            'kitchen_style': None,
            'outdoor_space': None,
            'size_impression': None,
            'date_added': datetime.now().isoformat(),
        }

        units_data['units'].append(unit)
        existing_urls.add(source_url)
        added += 1
        run.added()
        print(f'  + {unit_id}: {item.get("address", "?")} - ${item.get("price", 0)}/mo, {item.get("beds", "?")}bd')
        time.sleep(1)

    run.skipped(skipped)

    if added:
        save_units(units_data)
        print(f'\nAdded {added} new units from Zumper.')
    else:
        print('\nNo new units added (all duplicates or no results).')

    run.finish()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Scrape Zumper rental listings')
    parser.add_argument('--max', type=int, default=50, help='Max listings to fetch (default: 50)')
    parser.add_argument('--url', type=str, default=None, help='Override search URL')
    parser.add_argument('--pages', type=int, default=3, help='Max pages to scrape (default: 3)')
    args = parser.parse_args()

    config = load_config()
    search_url = args.url or build_search_url(config)
    crawl_zumper(search_url, max_listings=args.max)
    print('\nRun: python scripts/generate-html.py   to refresh the summary page')


if __name__ == '__main__':
    main()
