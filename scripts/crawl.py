#!/usr/bin/env python3
"""
Apartment Finder Crawler
Fetches listings from websites, downloads photos, and populates units.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
try:
    from dateutil import parser as dateparser
except Exception:
    dateparser = None
from datetime import timedelta

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
INPUTS_DIR = PROJECT_ROOT / "inputs"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
PHOTOS_DIR = OUTPUTS_DIR / "photos"
UNITS_JSON = OUTPUTS_DIR / "units.json"

def load_criteria():
    """Load search criteria from criteria.md"""
    criteria_path = PROJECT_ROOT / "criteria.md"
    if not criteria_path.exists():
        print("❌ Error: criteria.md not found")
        sys.exit(1)
    return criteria_path.read_text()

def load_websites():
    """Load websites to crawl from websites.md"""
    websites_path = PROJECT_ROOT / "websites.md"
    if not websites_path.exists():
        print("❌ Error: websites.md not found")
        sys.exit(1)
    return websites_path.read_text()

def load_or_create_units():
    """Load existing units.json or create new one"""
    if UNITS_JSON.exists():
        return json.loads(UNITS_JSON.read_text())
    
    return {
        "last_updated": datetime.now().isoformat(),
        "total_units": 0,
        "schema_notes": {
            "photos": "Array of local relative paths to downloaded images",
            "photo_sources": "Original URLs the photos were downloaded from"
        },
        "units": []
    }


def load_current_lease_end():
    """Read criteria.md and extract the lease end date if present"""
    criteria_path = PROJECT_ROOT / "criteria.md"
    if not criteria_path.exists():
        return None
    import re
    text = criteria_path.read_text()
    m = re.search(r"Current lease end:\*\*\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
    if m:
        return parse_date(m.group(1))
    # try other formats like YYYY-MM-DD in the file
    m2 = re.search(r"([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
    if m2:
        return parse_date(m2.group(1))
    return None


def parse_date(s):
    """Parse date string to datetime.date; returns None if parse fails"""
    if not s:
        return None
    try:
        if dateparser:
            return dateparser.parse(s).date()
        # fallback simple ISO parse
        return datetime.fromisoformat(s).date()
    except Exception:
        # try common formats
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d %Y", "%B %d %Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except Exception:
                continue
        return None


def compute_move_cost(current_lease_end, unit_move_in, unit_rent, first_free_months=0, concession_amount=0):
    """Compute net cost impact around move dates.

    Returns a dict with:
      - date_conflict: 'gap'|'overlap'|None
      - overlap_days or gap_days
      - net_move_cost: positive is extra cash needed (USD)
    """
    result = {"date_conflict": None, "days": 0, "net_move_cost": 0}
    if not current_lease_end or not unit_move_in:
        return result

    lease_end = current_lease_end
    move_in = unit_move_in

    # If move_in > lease_end => gap
    if move_in > lease_end:
        gap = (move_in - lease_end).days
        # Estimate hotel + storage cost: naive default $100/day hotel + $50/mo storage prorated
        hotel_cost = gap * 100
        storage_months = max(1, int((gap / 30)))
        storage_cost = storage_months * 50
        net = hotel_cost + storage_cost - (first_free_months * unit_rent) - concession_amount
        result.update({"date_conflict": "gap", "days": gap, "net_move_cost": net})
    elif move_in < lease_end:
        overlap = (lease_end - move_in).days
        # Overlap cost: paying both rents for overlap_period (prorate days)
        daily_rent = unit_rent / 30.0 if unit_rent else 0
        overlap_cost = overlap * daily_rent
        net = overlap_cost - (first_free_months * unit_rent) - concession_amount
        result.update({"date_conflict": "overlap", "days": overlap, "net_move_cost": net})
    else:
        # same day
        result.update({"date_conflict": None, "days": 0, "net_move_cost": 0})

    return result


def normalize_amenities(unit):
    """Normalize amenities into boolean flags for downstream filtering."""
    amenities = unit.get('amenities', []) or []
    # normalize to lowercase single string
    joined = ' '.join(amenities).lower()
    unit['has_washer_dryer'] = any(x in joined for x in ('washer', 'dryer', 'in-unit', 'in unit', 'w/d', 'stackable'))
    unit['is_gated'] = any(x in joined for x in ('gated', 'gate', 'gated community'))
    # also set explicit amenity list normalized
    unit['amenities_normalized'] = amenities
    return unit

def save_units(data):
    """Save units to units.json"""
    data["last_updated"] = datetime.now().isoformat()
    data["total_units"] = len(data.get("units", []))
    UNITS_JSON.write_text(json.dumps(data, indent=2))
    print(f"✓ Saved {data['total_units']} units to {UNITS_JSON}")

def add_unit(units_data, unit_info):
    """Add a unit to units.json"""
    unit_id = f"unit-{len(units_data['units']):04d}"
    unit_info["id"] = unit_id
    unit_info["date_added"] = datetime.now().isoformat()
    units_data["units"].append(unit_info)
    return unit_id

def download_photos(unit_id, photo_urls):
    """Download photos for a unit (skeleton)"""
    # TODO: Implement actual photo download logic
    # - Use requests.get() to fetch images
    # - Save to outputs/photos/{unit_id}/
    # - Store relative paths in units_data
    photo_dir = PHOTOS_DIR / unit_id
    photo_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Photos would be saved to: {photo_dir}")

def crawl_websites():
    """
    Main crawler function
    TODO: Implement actual web scraping using:
    - requests for HTTP
    - BeautifulSoup for HTML parsing
    - selenum or playwright for JS-heavy sites
    """
    print("🔍 Apartment Crawler Starting...")
    print()
    
    # Load criteria and sources
    criteria = load_criteria()
    websites = load_websites()
    units = load_or_create_units()
    
    print("📋 Criteria loaded from criteria.md")
    print("🌐 Websites loaded from websites.md")
    print()
    
    # Example: Add a test unit
    test_unit = {
        "title": "Example Unit (from crawl.py template)",
        "address": "123 Main St, Clearwater, FL 33755",
        "price": 1500,
        "beds": 2,
        "baths": 1.5,
        "sqft": 950,
        "amenities": ["washer/dryer", "gated"],
        "source_url": "https://example.com",
        "source": "Example Site",
        "photos": [],
        "photo_sources": [],
        "notes": "This is a template example — replace with real crawling logic"
    }
    
    # Example move-in evaluation using lease end from criteria
    lease_end = load_current_lease_end()
    # Add example move-in date and rent for demonstration
    test_unit["move_in_date"] = "2026-07-21"
    test_unit["first_free_months"] = 1
    test_unit["concession_amount"] = 0
    test_unit["rent"] = test_unit.get("price", 1500)
    # Example amenities
    test_unit['amenities'] = ['Washer/Dryer in-unit', 'Gated community', 'Pool']

    # Normalize amenities and compute move cost
    normalize_amenities(test_unit)
    move_in_parsed = parse_date(test_unit["move_in_date"])
    eval_result = compute_move_cost(lease_end, move_in_parsed, test_unit["rent"], test_unit.get("first_free_months", 0), test_unit.get("concession_amount", 0))
    test_unit.update(eval_result)

    # Add the test unit to units.json for now so generators have data
    unit_id = add_unit(units, test_unit)
    print(f"✓ Added unit: {unit_id} with move date evaluation: {eval_result}")
    
    save_units(units)
    print()
    print("✓ Crawler complete!")
    print(f"  View results: open outputs/units-summary.html in your browser")

if __name__ == "__main__":
    crawl_websites()
