#!/usr/bin/env python3
"""
Trulia scraper for rental listings.
Trulia is owned by Zillow and shares similar structure.

Usage: python3 scripts/scrapers/trulia.py
"""
import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime

# Ensure emoji/unicode in print() output doesn't crash on Windows consoles (cp1252)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Error: requests and beautifulsoup4 required")
    print("   Install with: pip install requests beautifulsoup4")
    exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
PHOTOS_DIR = OUTPUTS_DIR / 'photos'
UNITS_JSON = OUTPUTS_DIR / 'units.json'
CONFIG_JSON = PROJECT_ROOT / 'config.json'

from scripts.scrapers.common import fetch_html, download_image


def load_config():
    """Load target location from config.json"""
    if not CONFIG_JSON.exists():
        return {}
    return json.loads(CONFIG_JSON.read_text())


def load_units():
    """Load existing units or create new"""
    if UNITS_JSON.exists():
        return json.loads(UNITS_JSON.read_text(encoding='utf-8'))
    return {
        "last_updated": datetime.now().isoformat(),
        "total_units": 0,
        "units": []
    }


def save_units(data):
    """Save units to JSON"""
    data['last_updated'] = datetime.now().isoformat()
    data['total_units'] = len(data.get('units', []))
    UNITS_JSON.write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(f"✓ Saved {data['total_units']} units to {UNITS_JSON}")


def make_unit_id(units):
    """Generate the next unit id based on the highest existing numeric id
    (robust to gaps left by removed units)."""
    max_id = -1
    for u in units:
        m = re.match(r'unit-(\d+)$', u.get('id') or '')
        if m:
            max_id = max(max_id, int(m.group(1)))
    return f"unit-{max_id + 1:04d}"


def crawl_trulia(search_url, max_listings=24):
    """
    Crawl Trulia rental listings.
    
    Note: Trulia uses JS rendering like other Zillow properties.
    For best results, use Playwright or headless browser.
    """
    print(f"🔍 Fetching Trulia search: {search_url}")
    
    try:
        html, sess = fetch_html(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    except Exception as e:
        print(f"❌ Failed to fetch Trulia page: {e}")
        return

    soup = BeautifulSoup(html, 'html.parser')
    
    # Trulia listing cards
    listing_cards = []
    listing_cards.extend(soup.find_all('div', {'class': re.compile(r'.*searchResult.*')}))
    listing_cards.extend(soup.find_all('article'))
    
    if not listing_cards:
        print("⚠️  Could not find listing cards")
        print("   Trulia may be blocking requests or page structure changed")
        return
    
    units_data = load_units()
    count = 0
    
    for card in listing_cards:
        if count >= max_listings:
            break
        
        try:
            # Find address/link
            link = card.find('a', href=re.compile(r'/property/'))
            if not link:
                continue
            
            listing_url = link.get('href')
            if not listing_url.startswith('http'):
                listing_url = 'https://www.trulia.com' + listing_url
            
            address = link.get_text(strip=True)
            
            # Extract price
            price = None
            card_text = card.get_text()
            price_match = re.search(r'\$([0-9,]+)(?:/mo|/month)?', card_text, re.I)
            if price_match:
                price = int(price_match.group(1).replace(',', ''))
            
            # Extract beds/baths
            beds, baths = None, None
            bed_match = re.search(r'(\d+)\s*(?:Bed|BR)', card_text, re.I)
            if bed_match:
                beds = int(bed_match.group(1))
            
            bath_match = re.search(r'(\d+\.?\d*)\s*(?:Bath|BA)', card_text, re.I)
            if bath_match:
                baths = float(bath_match.group(1))
            
            # Check for duplicates
            existing_urls = {u.get('source_url') for u in units_data.get('units', [])}
            if listing_url in existing_urls:
                continue
            
            # Get image
            photos = []
            photo_sources = []
            img = card.find('img')
            if img and img.get('src'):
                photo_src = img.get('src')
                if img.get('data-src'):
                    photo_src = img.get('data-src')
                photo_sources.append(photo_src)
            
            # Create unit
            unit = {
                'title': f'Trulia - {address}',
                'address': address,
                'price': price or 0,
                'beds': beds,
                'baths': baths,
                'sqft': None,
                'amenities': [],
                'source': 'Trulia',
                'source_url': listing_url,
                'photos': [],
                'photo_sources': photo_sources,
                'notes': f'From Trulia on {datetime.now().isoformat()}'
            }
            
            # Download photos
            for i, photo_url in enumerate(photo_sources[:5]):
                try:
                    unit_id = make_unit_id(units_data.get('units', []))
                    photo_dir = PHOTOS_DIR / unit_id
                    photo_dir.mkdir(parents=True, exist_ok=True)
                    dest = photo_dir / f'photo-{i+1}.jpg'
                    download_image(photo_url, dest, timeout=20)
                    rel = (Path('outputs') / 'photos' / unit_id / dest.name).as_posix()
                    unit['photos'].append(rel)
                except Exception as e:
                    print(f"  photo download failed: {e}")
            
            # Add unit
            uid = make_unit_id(units_data.get('units', []))
            unit['id'] = uid
            unit['date_added'] = datetime.now().isoformat()
            units_data.setdefault('units', []).append(unit)
            print(f"Added {uid}: {address} — ${price}")
            count += 1
            time.sleep(2)
            
        except Exception as e:
            print(f"  Error processing card: {e}")
            continue
    
    save_units(units_data)


if __name__ == '__main__':
    search_url = "https://www.trulia.com/for_rent/Clearwater,FL/"
    
    config = load_config()
    target = config.get('target_location', {})
    if target:
        # Extract city/state from address, e.g. "...N, Clearwater, FL 33764" -> Clearwater, FL
        m = re.search(r',\s*([^,]+),\s*([A-Z]{2})\s*\d{5}', target.get('address', ''))
        if m:
            city, state = m.group(1).strip(), m.group(2).strip()
            search_url = f"https://www.trulia.com/for_rent/{city},{state}/"
    
    crawl_trulia(search_url, max_listings=24)
