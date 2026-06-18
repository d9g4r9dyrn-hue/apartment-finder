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


# Broad phone regex: matches 10-digit numbers with flexible separators/grouping.
# Strategy: find any sequence that strips down to exactly 10 (or 11 starting with 1) digits.
# We accept non-standard grouping like "601 488 752 5" (poster obfuscation).
_PHONE_BROAD_RE = re.compile(
    r'\(?\d{3}\)?[\s.\-]{0,2}\d{3}[\s.\-]{0,2}\d{2,4}[\s.\-]{0,2}\d{0,4}'
)

EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')


def _format_phone(digits):
    """Format 10 clean digits as (NXX) NXX-XXXX."""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def extract_contact_info(text):
    """Scan free-text description for phone/email the poster typed inline."""
    info = {}
    if not text:
        return info
    for m in _PHONE_BROAD_RE.finditer(text):
        digits = re.sub(r'\D', '', m.group(0))
        if len(digits) == 10:
            info['contact_phone'] = _format_phone(digits)
            break
        elif len(digits) == 11 and digits[0] == '1':
            info['contact_phone'] = _format_phone(digits[1:])
            break
    email_match = EMAIL_RE.search(text)
    if email_match:
        info['contact_email'] = email_match.group(0).strip()
    return info


def extract_contact_from_soup(soup, desc_text=''):
    """Extract phone and email from Craigslist's structured reply section first
    (tel:/mailto: href links), falling back to free-text search of the description.
    The reply sidebar always has the authoritative contact info; description text
    is a secondary source for posters who typed it inline."""
    info = {}

    # Phone: Craigslist renders the number as <a href="tel:7277355941">
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('tel:'):
            digits = re.sub(r'\D', '', href[4:])
            if len(digits) >= 10:
                d = digits[-10:]
                info['contact_phone'] = f"({d[:3]}) {d[3:6]}-{d[6:]}"
                break

    # Email: Craigslist relay addresses appear as <a href="mailto:xxx@hous.craigslist.org">
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('mailto:'):
            email = href[7:].split('?')[0].strip()
            if email and '@' in email:
                info['contact_email'] = email
                break

    # Fallback: free-text scan of the description
    fallback = extract_contact_info(desc_text)
    if 'contact_phone' not in info and 'contact_phone' in fallback:
        info['contact_phone'] = fallback['contact_phone']
    if 'contact_email' not in info and 'contact_email' in fallback:
        info['contact_email'] = fallback['contact_email']

    return info


def fetch_cl_contact_via_2captcha(listing_url, twocaptcha_api_key, session=None, delay_between=3):
    """Fetch Craigslist contact info using 2captcha to solve the hCaptcha gate.

    Returns dict with zero or more of: contact_phone, contact_email, contact_name.
    Raises on network/API errors. Returns {} if the listing has no contact options.

    Flow:
      GET listing_url → extract reply base URL from data-href on reply button
      POST /init        → {nonce, siteKey_hCaptcha}
      [2captcha solve]  → captcha_token
      POST /captcha     → {nonce}
      POST /popup       → {options: {emailOk, phoneOk, textOk}, contactName, ...}
      POST /mailto      → {email}   (if emailOk)
      POST /tel         → {phone}   (if phoneOk or textOk)
    """
    import requests as _req

    sess = session or _req.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120',
        'Accept': 'application/json, text/html, */*',
    })

    # Step 0: fetch listing page and extract reply base URL
    r = sess.get(listing_url, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')
    reply_btn = soup.find('button', class_='reply-button')
    if not reply_btn or not reply_btn.get('data-href'):
        return {}
    # data-href = "https://tampa.craigslist.org/reply/tpa/apa/7939048315/__SERVICE_ID__"
    base_url = reply_btn['data-href']  # still has __SERVICE_ID__ placeholder

    def cl_post(step, data=None):
        url = base_url.replace('__SERVICE_ID__', step)
        resp = sess.post(url, data=data or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # Step 1: init
    init_data = cl_post('init', {'browserinfo3': '{}'})
    nonce = init_data.get('nonce')
    site_key = init_data.get('siteKey_hCaptcha')
    if not nonce:
        return {}

    # Step 2: solve hCaptcha via 2captcha if required
    if site_key:
        # Submit task
        submit_resp = _req.post('https://2captcha.com/in.php', data={
            'key': twocaptcha_api_key,
            'method': 'hcaptcha',
            'sitekey': site_key,
            'pageurl': listing_url,
        }, timeout=15)
        submit_resp.raise_for_status()
        text = submit_resp.text.strip()
        if not text.startswith('OK|'):
            raise RuntimeError(f'2captcha submit failed: {text}')
        task_id = text.split('|', 1)[1]

        # Poll for result (up to 120s)
        captcha_token = None
        for _ in range(24):
            time.sleep(5)
            poll_resp = _req.get('https://2captcha.com/res.php', params={
                'key': twocaptcha_api_key,
                'action': 'get',
                'id': task_id,
            }, timeout=15)
            poll_resp.raise_for_status()
            poll_text = poll_resp.text.strip()
            if poll_text.startswith('OK|'):
                captcha_token = poll_text.split('|', 1)[1]
                break
            elif poll_text != 'CAPCHA_NOT_READY':
                raise RuntimeError(f'2captcha error: {poll_text}')
        if not captcha_token:
            raise RuntimeError('2captcha timed out after 120s')

        # Step 3: submit captcha to CL
        captcha_resp = cl_post('captcha', {'h-captcha-response': captcha_token, 'n': nonce})
        if captcha_resp.get('error'):
            raise RuntimeError(f'CL captcha error: {captcha_resp["error"]}')
        nonce = captcha_resp.get('nonce') or nonce

    # Step 4: get popup (available contact methods)
    popup = cl_post('popup', {'n': nonce})
    if popup.get('error'):
        raise RuntimeError(f'CL popup error: {popup["error"]}')

    result = {}
    contact_name = popup.get('contactName')
    if contact_name:
        result['contact_name'] = contact_name

    options = popup.get('options') or {}
    nonce = popup.get('nonce') or nonce  # popup may refresh the nonce

    # Step 5a: fetch email if available
    if options.get('emailOk'):
        try:
            time.sleep(delay_between)
            mailto_resp = cl_post('mailto', {'n': nonce})
            email = mailto_resp.get('email')
            if email:
                result['contact_email'] = email
                nonce = mailto_resp.get('nonce') or nonce
        except Exception:
            pass

    # Step 5b: fetch phone if available
    if options.get('phoneOk') or options.get('textOk'):
        try:
            time.sleep(delay_between)
            tel_resp = cl_post('tel', {'n': nonce})
            phone = tel_resp.get('phone')
            if phone:
                digits = re.sub(r'\D', '', phone)
                if len(digits) == 10:
                    result['contact_phone'] = _format_phone(digits)
                elif len(digits) == 11 and digits[0] == '1':
                    result['contact_phone'] = _format_phone(digits[1:])
                else:
                    result['contact_phone'] = phone
        except Exception:
            pass

    return result


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

    contact = extract_contact_from_soup(soup, desc)

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
    from scripts.scrape_tracker import ScrapeTracker
    tracker = ScrapeTracker()
    run = tracker.start_run('craigslist')

    print(f"Fetching Craigslist search: {search_url}")
    try:
        html, sess = fetch_html(search_url)
    except Exception as e:
        print(f"Failed to fetch search page: {e}")
        run.error(str(e))
        run.finish('failed')
        return
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
    run.found(len(results))
    units_data = load_or_create_units()

    count = 0
    skipped = 0
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
            skipped += 1
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
                run.photo()
            except Exception as e:
                print(f"  image download failed: {e}")
                run.error(f'Photo download: {e}')

        # generate id and add
        uid = make_unit_id(units_data.get('units', []))
        unit['id'] = uid
        unit['date_added'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        units_data.setdefault('units', []).append(unit)
        print(f"Added {uid}: {unit['title']} — ${unit['price']}")
        count += 1
        run.added()
        time.sleep(1)

    run.skipped(skipped)
    save_units(units_data)
    print(f"Saved {len(units_data.get('units', []))} units to {UNITS_JSON}")
    run.finish()


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
