#!/usr/bin/env python3
"""
Apartments.com scraper for rental listings.

Usage: python3 scripts/scrapers/apartments_com.py
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
SOURCES_JSON = OUTPUTS_DIR / 'sources.json'
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


def crawl_apartments_com(search_url, max_listings=24):
    """
    Crawl Apartments.com rental listings.
    
    NOTE: Apartments.com uses JavaScript heavily. For best results, use Playwright:
    pip install playwright
    
    This basic scraper attempts to parse server-rendered content and may miss
    JavaScript-loaded listings. If content is limited, try the Playwright version.
    """
    print(f"🔍 Fetching Apartments.com search: {search_url}")
    print("   (Note: Apartments.com uses heavy JS. Consider using Playwright.)")
    
    try:
        html, sess = fetch_html(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    except Exception as e:
        print(f"❌ Failed to fetch Apartments.com page: {e}")
        return

    soup = BeautifulSoup(html, 'html.parser')
    
    # Apartments.com typically wraps listings in divs with specific class patterns
    listing_cards = []
    
    # Try common patterns
    listing_cards.extend(soup.find_all('div', {'class': re.compile(r'.*apartment-card.*')}))
    listing_cards.extend(soup.find_all('div', {'class': re.compile(r'.*listing.*')}))
    
    # Fallback: look for links to property pages
    if not listing_cards:
        for a in soup.find_all('a', href=re.compile(r'/apartments/')):
            parent = a.find_parent('div', class_=re.compile(r'.*card.*'))
            if parent:
                listing_cards.append(parent)
    
    if not listing_cards:
        print("⚠️  Could not find listing cards in Apartments.com HTML")
        print("   The site uses JavaScript heavily. Try the Playwright version:")
        print("   pip install playwright && python scripts/scrapers/apartments_com_playwright.py")
        return
    
    units_data = load_units()
    count = 0
    
    for card in listing_cards:
        if count >= max_listings:
            break
        
        try:
            # Find property link
            prop_link = card.find('a', href=re.compile(r'/apartments/'))
            if not prop_link:
                continue
            
            listing_url = prop_link.get('href')
            if not listing_url.startswith('http'):
                listing_url = 'https://www.apartments.com' + listing_url
            
            address = prop_link.get_text(strip=True)
            
            # Find price
            price = None
            price_pattern = re.compile(r'\$[\d,]+')
            price_match = price_pattern.search(card.get_text())
            if price_match:
                m = re.search(r'(\d+)', price_match.group(0))
                if m:
                    price = int(m.group(1))
            
            # Find beds/baths (usually in separate elements)
            beds, baths = None, None
            full_text = card.get_text(' ')
            
            bed_match = re.search(r'(\d+)\s*(?:Bed|BR)', full_text, re.I)
            if bed_match:
                beds = int(bed_match.group(1))
            
            bath_match = re.search(r'(\d+\.?\d*)\s*(?:Bath|BA)', full_text, re.I)
            if bath_match:
                baths = float(bath_match.group(1))
            
            # Check for duplicates
            existing_urls = {u.get('source_url') for u in units_data.get('units', [])}
            if listing_url in existing_urls:
                continue
            
            # Try to get image
            photos = []
            photo_sources = []
            img = card.find('img')
            if img and img.get('src'):
                photo_src = img.get('src')
                # Apartments.com uses lazy loading, may have data-src
                if 'placeholder' in photo_src.lower() and img.get('data-src'):
                    photo_src = img.get('data-src')
                photo_sources.append(photo_src)
            
            # Create unit entry
            unit = {
                'title': f'Apartments.com - {address}',
                'address': address,
                'price': price or 0,
                'beds': beds,
                'baths': baths,
                'sqft': None,
                'amenities': [],
                'source': 'Apartments.com',
                'source_url': listing_url,
                'photos': [],
                'photo_sources': photo_sources,
                'notes': f'From Apartments.com on {datetime.now().isoformat()}'
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
    # Default search URL for Clearwater
    search_url = "https://www.apartments.com/clearwater-fl/"
    
    # Could also use config target location
    config = load_config()
    target = config.get('target_location', {})
    if target:
        # Extract city/state from address, e.g. "...N, Clearwater, FL 33764" -> Clearwater, FL
        m = re.search(r',\s*([^,]+),\s*([A-Z]{2})\s*\d{5}', target.get('address', ''))
        if m:
            city, state = m.group(1).strip(), m.group(2).strip()
            search_url = f"https://www.apartments.com/{city.lower()}-{state.lower()}/"
    
    crawl_apartments_com(search_url, max_listings=24)
