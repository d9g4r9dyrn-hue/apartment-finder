#!/usr/bin/env python3
"""
Simple Craigslist scraper for Tampa Bay (text-based).
Saves normalized unit objects to `outputs/units.json` and downloads images to `outputs/photos/{unit-id}/`.

Usage: python3 scripts/scrapers/craigslist.py
"""
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

# Ensure emoji/unicode in listing titles don't crash print() on Windows consoles (cp1252)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
PHOTOS_DIR = OUTPUTS_DIR / 'photos'
UNITS_JSON = OUTPUTS_DIR / 'units.json'
SOURCES_JSON = OUTPUTS_DIR / 'sources.json'
CONFIG_JSON = PROJECT_ROOT / 'config.json'

from scripts.scrapers.common import fetch_html, download_image


def load_sources():
    if not SOURCES_JSON.exists():
        return None
    return json.loads(SOURCES_JSON.read_text())


def load_config():
    if not CONFIG_JSON.exists():
        return {}
    return json.loads(CONFIG_JSON.read_text(encoding='utf-8'))


def build_search_url(base_url, config):
    """Add postal/distance/price/bedroom filters from config.json to the
    Craigslist search so results are targeted at the configured area instead
    of returning the entire Tampa Bay region."""
    target = config.get('target_location', {})
    # Match the zip code (preceded by a state abbreviation), not the street number
    zip_match = re.search(r'[A-Z]{2}\s+(\d{5})', target.get('address', ''))

    params = {}
    if zip_match:
        params['postal'] = zip_match.group(1)
    if config.get('search_radius_miles') is not None:
        params['search_distance'] = config['search_radius_miles']
    if config.get('min_price') is not None:
        params['min_price'] = config['min_price']
    if config.get('max_price') is not None:
        params['max_price'] = config['max_price']
    if config.get('min_beds') is not None:
        params['min_bedrooms'] = config['min_beds']
    if config.get('max_beds') is not None:
        params['max_bedrooms'] = config['max_beds']

    if not params:
        return base_url
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def load_or_create_units():
    if UNITS_JSON.exists():
        return json.loads(UNITS_JSON.read_text(encoding='utf-8'))
    return {"last_updated": None, "total_units": 0, "units": []}


def save_units(data):
    data['last_updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    data['total_units'] = len(data.get('units', []))
    UNITS_JSON.write_text(json.dumps(data, indent=2), encoding='utf-8')


def make_unit_id(units):
    """Generate the next unit id based on the highest existing numeric id.

    Using len(units) breaks once a unit is ever removed (e.g. by
    remove_sample.py), since a later run can reuse an id that's
    still present in the data, overwriting its photos/detail page.
    """
    max_id = -1
    for u in units:
        m = re.match(r'unit-(\d+)$', u.get('id') or '')
        if m:
            max_id = max(max_id, int(m.group(1)))
    return f"unit-{max_id + 1:04d}"


# schema.org @type values that show up in CL's structured data but aren't the
# listing's housing type (breadcrumbs, addresses, etc.)
NON_HOUSING_TYPES = {'BreadcrumbList', 'ListItem', 'PostalAddress', 'SearchResultsPage', 'WebPage', 'ItemList'}


def extract_housing_type(soup):
    """Read Craigslist's own housing-type classification (Apartment, House,
    Condo, Townhouse, etc.) from the listing's embedded JSON-LD."""
    for script in soup.find_all('script', {'type': 'application/ld+json'}):
        try:
            data = json.loads(script.string or script.get_text() or '')
        except Exception:
            continue
        if isinstance(data, dict):
            t = data.get('@type')
            if t and t not in NON_HOUSING_TYPES:
                return t
    return None


# Matches the "available aug 15" form of Craigslist's availability badge
AVAILABLE_MONTH_DAY_RE = re.compile(r'available\s+([a-z]{3,9})\s+(\d{1,2})', re.IGNORECASE)

# Free-text fallback for listings whose description mentions move-in
# readiness without a structured availability badge
MOVE_IN_READY_RE = re.compile(
    r'(move[\s-]?in\s+ready|available\s+now|immediate(?:ly)?\s+availab|ready\s+(?:for\s+)?move[\s-]?in)',
    re.IGNORECASE
)


def extract_specs(soup):
    """Parse the listing's 'attr important' badges (e.g. '2BR / 1Ba',
    '967ft2', 'available aug 15' / 'available now') for beds, baths, sqft,
    and move-in availability. Returns a dict with whichever of 'beds',
    'baths', 'sqft', 'move_in_date' could be determined (move_in_date is an
    ISO 'YYYY-MM-DD' string, or 'now')."""
    result = {}
    for span in soup.find_all('span', {'class': 'attr important'}):
        text = span.get_text(' ', strip=True)

        m = re.match(r'(\d+)\s*BR\s*/\s*([\d.]+)\s*Ba', text, re.IGNORECASE)
        if m:
            result['beds'] = int(m.group(1))
            result['baths'] = float(m.group(2))
            continue

        m = re.match(r'(\d+)\s*ft', text, re.IGNORECASE)
        if m:
            result['sqft'] = int(m.group(1))
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


def extract_amenities(soup):
    """Parse the listing's amenity tags - the plain '<div class="attr ...">'
    entries in the 'attrgroup' divs (pet policy, laundry, parking, A/C,
    etc.), excluding the housing-type and rent-period attrs which are
    captured elsewhere."""
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
    """Derive has_washer_dryer/is_gated flags from a list of amenity
    strings."""
    joined = ' '.join(amenities).lower()
    has_washer_dryer = any(x in joined for x in ('washer', 'dryer', 'in-unit', 'in unit', 'w/d', 'stackable'))
    is_gated = any(x in joined for x in ('gated', 'gate', 'gated community'))
    return has_washer_dryer, is_gated


# Matches "55+", "55 plus", or "55 and older" / "62 or over" style minimum-age
# language used by age-restricted (e.g. 55+ senior) communities
AGE_RESTRICTION_RE = re.compile(
    r'\b(\d{2})\s*\+|\b(\d{2})\s*plus\b|\b(\d{2})\s*(?:years?\s*)?(?:and|or)\s*(?:older|over)\b',
    re.IGNORECASE
)


def extract_age_restriction(text):
    """Look for '55+' / '55 plus' / '55 and older' style minimum-age
    requirements in listing text. Returns the minimum age as an int, or
    None if no such requirement is mentioned."""
    if not text:
        return None
    m = AGE_RESTRICTION_RE.search(text)
    if not m:
        return None
    return int(next(g for g in m.groups() if g))


# Matches formatted US phone numbers, e.g. "(727) 555-1234", "727-555-1234",
# "727.555.1234" - requires separators/parens to avoid false-positives on
# bare 10-digit numbers (prices, sqft, etc.) in free text
PHONE_RE = re.compile(r'\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}')

EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')


def extract_contact_info(text):
    """Best-effort extraction of a phone number and/or email address from a
    listing's free-text description, for following up with the poster.
    Craigslist's own reply-relay addresses are anonymized via JS/CAPTCHA and
    aren't recoverable from static HTML, so this only picks up contact info
    the poster included directly in the listing text."""
    info = {}
    if not text:
        return info
    phone_match = PHONE_RE.search(text)
    if phone_match:
        info['contact_phone'] = phone_match.group(0).strip()
    email_match = EMAIL_RE.search(text)
    if email_match and 'craigslist.org' not in email_match.group(0).lower():
        info['contact_email'] = email_match.group(0).strip()
    return info


def parse_listing_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title_el = soup.find('span', {'id': 'titletextonly'})
    title = title_el.get_text(strip=True) if title_el else 'No title'
    price_el = soup.find('span', {'class': 'price'})
    price = None
    if price_el:
        m = re.search(r"\$([0-9,]+)", price_el.get_text())
        if m:
            price = int(m.group(1).replace(',', ''))

    housing = soup.find('span', {'class': 'housing'})
    beds = None
    sqft = None
    if housing:
        # housing text example: "2br - 1000ft2 -"
        txt = housing.get_text(' ', strip=True)
        m = re.search(r"(\d+)br", txt)
        if m:
            beds = int(m.group(1))
        m2 = re.search(r"(\d+)[ ]?ft", txt)
        if m2:
            sqft = int(m2.group(1))

    # neighborhood
    hood_el = soup.find('small')
    hood = hood_el.get_text(strip=True).strip('()') if hood_el else None

    # try to get a more complete address if available
    mapaddr = None
    mapaddr_el = soup.find('div', {'class': 'mapaddress'})
    if mapaddr_el:
        mapaddr = mapaddr_el.get_text(strip=True)

    # description
    desc_el = soup.find('section', {'id': 'postingbody'})
    desc = desc_el.get_text('\n', strip=True) if desc_el else ''

    # beds/baths/sqft/availability from the structured 'attr important' badges,
    # falling back to the housing-span values and free-text description
    specs = extract_specs(soup)
    if 'beds' in specs:
        beds = specs['beds']
    if 'sqft' in specs:
        sqft = specs['sqft']
    move_in_date = specs.get('move_in_date') or extract_move_in_date(desc)

    age_restriction = extract_age_restriction(f"{title} {desc}")

    contact = extract_contact_info(desc)

    amenities = extract_amenities(soup)

    # images: Craigslist stores image ids in a div with id 'thumbs' or in a data-ids attribute
    images = []
    gallery = soup.find('div', {'id': 'thumbs'})
    if gallery:
        for img in gallery.find_all('img'):
            src = img.get('src')
            if src:
                # gallery <img> src points at a 50x50 cropped thumbnail;
                # request a larger size for the saved photo
                src = re.sub(r'_\d+x\d+c?\.jpg$', '_600x450.jpg', src)
                images.append(src)
    else:
        # try meta og:image
        meta = soup.find('meta', {'property': 'og:image'})
        if meta and meta.get('content'):
            images.append(meta.get('content'))

    return {
        'title': title,
        'price': price,
        'beds': beds,
        'baths': specs.get('baths'),
        'sqft': sqft,
        'move_in_date': move_in_date,
        'age_restriction': age_restriction,
        'amenities': amenities,
        'neighborhood': hood,
        'address': mapaddr,
        'description': desc,
        'photos': images,
        'housing_type': extract_housing_type(soup),
        'source_url': url,
        'contact_phone': contact.get('contact_phone'),
        'contact_email': contact.get('contact_email'),
    }


def crawl_craigslist(search_url, max_listings=24):
    print(f"Fetching Craigslist search: {search_url}")
    html, sess = fetch_html(search_url)
    soup = BeautifulSoup(html, 'html.parser')
    # Adapt to new Craigslist markup: look for legacy 'result-row' or new 'cl-static-search-result'
    results = []
    for li in soup.find_all('li'):
        classes = ' '.join(li.get('class') or [])
        if 'result-row' in classes or 'cl-static-search-result' in classes:
            results.append(li)
            continue
        # fallback: if the li contains a listing anchor with '/apa/d/' path, treat as result
        a = li.find('a', href=True)
        if a and '/apa/d/' in a['href']:
            results.append(li)
    units_data = load_or_create_units()

    count = 0
    for r in results:
        if count >= max_listings:
            break
        # anchor may not have class in newer markup; pick first listing anchor
        link = r.find('a', href=True)
        if not link:
            continue
        url = link.get('href')
        try:
            page_html, _ = fetch_html(url)
        except Exception as e:
            print(f"Failed to fetch listing {url}: {e}")
            continue
        info = parse_listing_page(page_html, url)
        # avoid duplicates by source_url
        existing_urls = {u.get('source_url') for u in units_data.get('units', [])}
        if info.get('source_url') in existing_urls:
            print(f"Skipping duplicate: {info.get('source_url')}")
            continue

        # prefer explicit address if available
        addr = info.get('address') or info.get('neighborhood') or ''

        amenities = info.get('amenities') or []
        has_washer_dryer, is_gated = normalize_amenities(amenities)

        unit = {
            'title': info['title'],
            'address': addr,
            'price': info.get('price') or 0,
            'beds': info.get('beds') or 0,
            'baths': info.get('baths'),
            'sqft': info.get('sqft') or None,
            'housing_type': info.get('housing_type'),
            'amenities': amenities,
            'has_washer_dryer': has_washer_dryer,
            'is_gated': is_gated,
            'age_restriction': info.get('age_restriction'),
            'source': 'Craigslist',
            'source_url': info.get('source_url'),
            'photos': [],
            'photo_sources': [],
            'notes': info.get('description') or '',
            'move_in_date': info.get('move_in_date'),
            'contact_phone': info.get('contact_phone'),
            'contact_email': info.get('contact_email'),
        }

        # try to extract lat/lon from data attributes in the listing page
        listing_soup = BeautifulSoup(page_html, 'html.parser')
        lat = None
        lon = None
        lat_el = listing_soup.find(attrs={'data-latitude': True})
        if lat_el and lat_el.get('data-latitude'):
            try:
                lat = float(lat_el.get('data-latitude'))
                lon = float(lat_el.get('data-longitude')) if lat_el.get('data-longitude') else None
            except Exception:
                lat = None
                lon = None
        else:
            # sometimes map coordinates are in a div with id="map" data-latitude
            map_div = listing_soup.find('div', {'id': 'map'})
            if map_div and map_div.get('data-latitude'):
                try:
                    lat = float(map_div.get('data-latitude'))
                    lon = float(map_div.get('data-longitude')) if map_div.get('data-longitude') else None
                except Exception:
                    lat = None
                    lon = None

        if lat and lon:
            unit['lat'] = lat
            unit['lon'] = lon

        # download up to 8 photos
        for i, img_url in enumerate(info.get('photos', [])[:8]):
            try:
                unit_id = make_unit_id(units_data.get('units', []))
                photo_dir = PHOTOS_DIR / unit_id
                photo_dir.mkdir(parents=True, exist_ok=True)
                dest = photo_dir / f'photo-{i+1}.jpg'
                download_image(img_url, dest)
                # Use forward slashes regardless of OS so paths work as HTML src
                rel = (Path('outputs') / 'photos' / unit_id / dest.name).as_posix()
                unit['photos'].append(rel)
                unit['photo_sources'].append(img_url)
            except Exception as e:
                print(f"  image download failed: {e}")

        # generate id and add
        uid = make_unit_id(units_data.get('units', []))
        unit['id'] = uid
        unit['date_added'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        units_data.setdefault('units', []).append(unit)
        print(f"Added {uid}: {unit['title']} — ${unit['price']}")
        count += 1
        time.sleep(1)

    save_units(units_data)
    print(f"Saved {len(units_data.get('units', []))} units to {UNITS_JSON}")


if __name__ == '__main__':
    sources = load_sources()
    craigslist_url = None
    if sources:
        for s in sources.get('sources', []):
            if s.get('id') == 'craigslist_tampa':
                craigslist_url = s.get('url')
                break
    if not craigslist_url:
        craigslist_url = 'https://tampa.craigslist.org/search/apa'

    config = load_config()
    craigslist_url = build_search_url(craigslist_url, config)
    crawl_craigslist(craigslist_url, max_listings=24)
