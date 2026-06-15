#!/usr/bin/env python3
"""
Zillow scraper for rental listings in Clearwater, FL area.
Uses Zillow API endpoint for rental data.

Usage: python3 scripts/scrapers/zillow.py
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
        "schema_notes": {
            "photos": "Array of local relative paths to downloaded images",
            "photo_sources": "Original URLs the photos were downloaded from"
        },
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


def extract_location_from_address(address):
    """Extract zip code and parts from address"""
    # Zillow addresses typically include city, state, zip
    parts = address.split(',')
    if len(parts) >= 3:
        return {
            'street': parts[0].strip(),
            'city_state': parts[1].strip(),
            'zip': parts[2].strip() if len(parts) > 2 else None
        }
    return {'street': address}


def crawl_zillow(search_url, max_listings=24):
    """
    Crawl Zillow rental listings.
    
    NOTE: Zillow actively blocks scrapers. For production use, consider:
    - Using Zillow's official API if available
    - Using a reverse proxy service
    - Using Playwright with headless browser to render JS
    - Implementing delays and rotating user agents
    
    Current implementation attempts basic crawling with fallback handling.
    """
    print(f"🔍 Attempting to fetch Zillow search: {search_url}")
    print("   (Note: Zillow actively blocks scrapers. This may fail.)")
    
    try:
        html, sess = fetch_html(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    except Exception as e:
        print(f"❌ Failed to fetch Zillow page: {e}")
        print("   Zillow may be blocking this request. Try:")
        print("   1. Use Playwright for JS rendering: pip install playwright")
        print("   2. Use Zillow API endpoint if available")
        print("   3. Add delays between requests")
        return

    soup = BeautifulSoup(html, 'html.parser')
    
    # Try to find listing cards (structure varies, common patterns below)
    listing_cards = []
    
    # Pattern 1: div with article tag
    listing_cards.extend(soup.find_all('article'))
    
    # Pattern 2: div with specific data attributes
    listing_cards.extend(soup.find_all('div', {'data-test': 'property-card'}))
    
    if not listing_cards:
        print("⚠️  Could not find listing cards in Zillow HTML")
        print("   The site structure may have changed or JS content may not be loaded")
        print("   Try using Playwright to render JavaScript")
        return
    
    units_data = load_units()
    count = 0
    
    for card in listing_cards:
        if count >= max_listings:
            break
        
        try:
            # Try to extract basic info from card
            # Zillow structure: usually has price, address, beds/baths in the card
            
            # Find address link
            addr_link = card.find('a', href=re.compile(r'/homedetails/'))
            if not addr_link:
                continue
            
            listing_url = addr_link.get('href')
            if not listing_url.startswith('http'):
                listing_url = 'https://www.zillow.com' + listing_url
            
            address_text = addr_link.get_text(strip=True)
            
            # Find price
            price_elem = card.find(re.compile(r'^h[1-4]$'))
            price = None
            if price_elem:
                m = re.search(r'\$([0-9,]+)', price_elem.get_text())
                if m:
                    price = int(m.group(1).replace(',', ''))
            
            # Find beds/baths - usually in specific divs
            beds, baths = None, None
            specs = card.find_all('b')
            if specs:
                for i, spec in enumerate(specs):
                    text = spec.get_text(strip=True)
                    if 'bd' in text:
                        m = re.search(r'(\d+)', text)
                        if m:
                            beds = int(m.group(1))
                    if 'ba' in text:
                        m = re.search(r'(\d+)', text)
                        if m:
                            baths = int(m.group(1))
            
            # Check for duplicates
            existing_urls = {u.get('source_url') for u in units_data.get('units', [])}
            if listing_url in existing_urls:
                print(f"  Skipping duplicate: {address_text}")
                continue
            
            # Try to extract photos from card image
            photos = []
            photo_sources = []
            img = card.find('img')
            if img and img.get('src'):
                photo_sources.append(img.get('src'))
            
            # Create unit entry
            unit = {
                'title': f'Zillow Rental - {address_text}',
                'address': address_text,
                'price': price or 0,
                'beds': beds,
                'baths': baths,
                'sqft': None,
                'amenities': [],
                'source': 'Zillow',
                'source_url': listing_url,
                'photos': [],
                'photo_sources': photo_sources,
                'notes': f'Scraped from Zillow on {datetime.now().isoformat()}'
            }
            
            # Download photos
            for i, photo_url in enumerate(photo_sources[:5]):
                try:
                    unit_id = make_unit_id(units_data.get('units', []))
                    photo_dir = PHOTOS_DIR / unit_id
                    photo_dir.mkdir(parents=True, exist_ok=True)
                    dest = photo_dir / f'photo-{i+1}.jpg'
                    download_image(photo_url, dest)
                    rel = (Path('outputs') / 'photos' / unit_id / dest.name).as_posix()
                    unit['photos'].append(rel)
                except Exception as e:
                    print(f"  photo download failed: {e}")
            
            # Add to units
            uid = make_unit_id(units_data.get('units', []))
            unit['id'] = uid
            unit['date_added'] = datetime.now().isoformat()
            units_data.setdefault('units', []).append(unit)
            print(f"Added {uid}: {address_text} — ${price}")
            count += 1
            time.sleep(2)  # Be respectful with rate limiting
            
        except Exception as e:
            print(f"  Error processing card: {e}")
            continue
    
    save_units(units_data)


def crawl_zillow_api(zipcode, max_listings=24):
    """
    Alternative: Use Zillow search parameters to construct API URL.
    Note: This may not work if Zillow's API endpoints have changed.
    """
    print(f"🔍 Searching Zillow API for rentals in {zipcode}...")
    
    # Construct Zillow search URL
    search_url = f"https://www.zillow.com/homes/for_rent/search?searchQueryState=%7B%22pagination%22%3A%7B%7D%2C%22mapBounds%22%3Anull%2C%22regionSelection%22%3A%5B%7B%22regionId%22%3A33764%2C%22regionType%22%3A3%7D%5D%2C%22filterState%22%3A%7B%7D%2C%22isMapVisible%22%3Afalse%2C%22usersSearchTerm%22%3A%22{zipcode}%20FL%22%7D&page=1"
    
    crawl_zillow(search_url, max_listings)


if __name__ == '__main__':
    config = load_config()
    
    # Use target location zip code if available, otherwise default
    target = config.get('target_location', {})
    target_address = target.get('address', '')
    
    # Extract zip from target address (e.g., "19135 US Hwy 19 N, Clearwater, FL 33764" -> "33764")
    # Match the zip code (preceded by a state abbreviation), not the street number
    zip_match = re.search(r'[A-Z]{2}\s+(\d{5})', target_address)
    zip_code = zip_match.group(1) if zip_match else "33764"
    
    crawl_zillow_api(zip_code, max_listings=24)
