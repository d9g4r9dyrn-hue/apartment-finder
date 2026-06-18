#!/usr/bin/env python3
"""
Generate HTML Dashboards from units.json
Updates outputs/units-summary.html based on discovered units
"""

import json
import re
import sys
import math
from html import escape as html_escape
from pathlib import Path
from datetime import date, datetime
from statistics import median

# Ensure emoji/unicode in print() output doesn't crash on Windows consoles (cp1252)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UNITS_JSON = PROJECT_ROOT / "outputs" / "units.json"
SUMMARY_HTML = PROJECT_ROOT / "outputs" / "units-summary.html"
CONFIG_JSON = PROJECT_ROOT / "config.json"
INTERACTIONS_JSON = PROJECT_ROOT / "outputs" / "interactions.json"

def load_units():
    """Load units from JSON"""
    if not UNITS_JSON.exists():
        print(f"❌ Error: {UNITS_JSON} not found")
        sys.exit(1)
    return json.loads(UNITS_JSON.read_text(encoding='utf-8'))

def load_config():
    """Load configuration from config.json"""
    if not CONFIG_JSON.exists():
        print(f"⚠️  Warning: {CONFIG_JSON} not found, using defaults")
        return {"search_radius_miles": None, "target_location": None}
    return json.loads(CONFIG_JSON.read_text(encoding='utf-8'))

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in miles between two lat/lon coordinates"""
    R = 3959  # Earth's radius in miles

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.asin(math.sqrt(a))

    return R * c

def filter_units_by_distance(units, config):
    """Filter units outside search radius and add distance to each unit"""
    target = config.get('target_location')
    radius = config.get('search_radius_miles')

    # If no target or radius specified, skip distance filtering but still compute distance when possible
    filtered = []
    target_lat = target['lat'] if target else None
    target_lon = target['lon'] if target else None

    # Additional criteria
    min_beds = config.get('min_beds')
    min_baths = config.get('min_baths')
    min_sqft = config.get('min_sqft')
    min_price = config.get('min_price')
    max_price = config.get('max_price')

    for unit in units:
        distance = None
        if target_lat is not None and target_lon is not None and unit.get('lat') and unit.get('lon'):
            distance = haversine_distance(target_lat, target_lon, unit['lat'], unit['lon'])
            unit['distance_miles'] = round(distance, 1)

        # Decide inclusion by distance
        if radius is not None and distance is not None and distance > radius:
            # excluded by distance
            unit['_excluded_reason'] = unit.get('_excluded_reason', []) + [f'distance>{radius}']
            continue

        # Apply min beds/baths/sqft if provided
        if min_beds is not None:
            try:
                beds_val = int(unit.get('beds') or 0)
            except Exception:
                beds_val = 0
            if beds_val < min_beds:
                unit['_excluded_reason'] = unit.get('_excluded_reason', []) + [f'beds<{min_beds}']
                continue

        if min_baths is not None:
            try:
                baths_val = float(unit.get('baths') or 0)
            except Exception:
                baths_val = 0
            if baths_val < min_baths:
                unit['_excluded_reason'] = unit.get('_excluded_reason', []) + [f'baths<{min_baths}']
                continue

        if min_sqft is not None and unit.get('sqft'):
            try:
                sqft_val = int(unit.get('sqft') or 0)
            except Exception:
                sqft_val = 0
            if sqft_val < min_sqft:
                unit['_excluded_reason'] = unit.get('_excluded_reason', []) + [f'sqft<{min_sqft}']
                continue

        try:
            price_val = int(unit.get('price') or 0)
        except Exception:
            price_val = 0

        if min_price is not None and price_val < min_price:
            unit['_excluded_reason'] = unit.get('_excluded_reason', []) + [f'price<{min_price}']
            continue

        if max_price is not None and price_val > max_price:
            unit['_excluded_reason'] = unit.get('_excluded_reason', []) + [f'price>{max_price}']
            continue

        # If no exclusion, include the unit
        filtered.append(unit)

    return filtered

def move_in_sort_key(move_in_date):
    """Compute a numeric sort key for a move-in date: 'now' sorts first,
    ISO dates sort by day, and unknown dates sort last (None)."""
    if not move_in_date:
        return None
    if move_in_date == 'now':
        return 0
    try:
        d = date.fromisoformat(move_in_date)
        return (d - date(1970, 1, 1)).days
    except ValueError:
        return None

def find_linked_units(units):
    """Detect listings that appear to be the same rental and store linked_ids on each."""
    n = len(units)
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Signal 1: shared photo source URLs
    photo_to_idx = {}
    for i, u in enumerate(units):
        for ps in (u.get('photo_sources') or []):
            if ps in photo_to_idx:
                union(i, photo_to_idx[ps])
            else:
                photo_to_idx[ps] = i

    # Signal 2: proximity + beds + price within 10%
    for i in range(n):
        u1 = units[i]
        if not u1.get('lat') or not u1.get('lon'):
            continue
        for j in range(i + 1, n):
            u2 = units[j]
            if not u2.get('lat') or not u2.get('lon'):
                continue
            if u1.get('beds') != u2.get('beds'):
                continue
            p1, p2 = u1.get('price', 0), u2.get('price', 0)
            if p1 and p2 and abs(p1 - p2) / max(p1, p2) > 0.10:
                continue
            dlat = u1['lat'] - u2['lat']
            dlon = u1['lon'] - u2['lon']
            dist = math.sqrt(dlat ** 2 + dlon ** 2) * 69
            if dist < 0.05:
                union(i, j)

    # Signal 3: shared phone at same coordinates
    phone_loc = {}
    for i, u in enumerate(units):
        phone = u.get('contact_phone')
        if not phone or not u.get('lat') or not u.get('lon'):
            continue
        key = (phone, round(u['lat'], 3), round(u['lon'], 3))
        if key in phone_loc:
            union(i, phone_loc[key])
        else:
            phone_loc[key] = i

    from collections import defaultdict
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    for _root, members in groups.items():
        if len(members) < 2:
            units[members[0]]['linked_ids'] = []
            units[members[0]]['linked_primary'] = None
            continue
        ids = [units[m]['id'] for m in members]
        best = max(members, key=lambda m: (
            len(units[m].get('photos') or []),
            1 if units[m].get('quality_rating') else 0,
            -m,
        ))
        primary_id = units[best]['id']
        for m in members:
            others = [uid for uid in ids if uid != units[m]['id']]
            units[m]['linked_ids'] = others
            units[m]['linked_primary'] = primary_id

    linked_groups = [ms for ms in groups.values() if len(ms) > 1]
    total_linked = sum(len(ms) for ms in linked_groups)
    print(f"  \U0001f517 Found {len(linked_groups)} groups of linked listings ({total_linked} units)")

def unit_to_js(u):
    """Trim a unit down to the fields the page renders client-side."""
    photos = u.get('photos', [])
    photo_paths = [p.replace('outputs/', '') if 'outputs/' in p else p for p in photos]
    return {
        'id': u.get('id'),
        'title': u.get('title') or u.get('address') or u.get('id'),
        'address': u.get('address'),
        'price': u.get('price'),
        'beds': u.get('beds'),
        'baths': u.get('baths'),
        'sqft': u.get('sqft'),
        'housing_type': u.get('housing_type'),
        'source': u.get('source'),
        'source_url': u.get('source_url'),
        'lat': u.get('lat'),
        'lon': u.get('lon'),
        'photos': photo_paths,
        'has_washer_dryer': u.get('has_washer_dryer'),
        'is_gated': u.get('is_gated'),
        'amenities': u.get('amenities'),
        'age_restriction': u.get('age_restriction'),
        'quality_rating': u.get('quality_rating'),
        'quality_notes': u.get('quality_notes'),
        'flooring_type': u.get('flooring_type'),
        'kitchen_style': u.get('kitchen_style'),
        'outdoor_space': u.get('outdoor_space'),
        'size_impression': u.get('size_impression'),
        'move_in_date': u.get('move_in_date'),
        'move_in_sort': move_in_sort_key(u.get('move_in_date')),
        'notes': u.get('notes'),
        'contact_phone': u.get('contact_phone'),
        'contact_email': u.get('contact_email'),
        'contact_name': u.get('contact_name'),
        'scam_score': u.get('scam_score'),
        'scam_level': u.get('scam_level'),
        'linked_ids': u.get('linked_ids', []),
        'linked_primary': u.get('linked_primary'),
    }

def generate_html():
    """Generate the complete units summary + map page"""
    units_data = load_units()
    config = load_config()
    all_units = units_data.get('units', [])

    # Detect duplicate/linked listings before any other processing
    find_linked_units(all_units)

    # Apply distance/price/beds filtering (console stats only - the page itself
    # re-applies these filters client-side)
    filtered_units = filter_units_by_distance(all_units, config)
    total = len(filtered_units)

    # Lease end context, used in the stats bar and on each detail page
    lease_end = None
    lease_end_str = config.get('current_lease_end')
    if lease_end_str:
        try:
            lease_end = datetime.strptime(lease_end_str, '%Y-%m-%d').date()
        except Exception:
            lease_end = None

    if lease_end:
        days_until_lease_end = (lease_end - datetime.now().date()).days
        lease_end_display = lease_end.strftime('%b %d, %Y')
        lease_end_label = f'Your Lease Ends ({days_until_lease_end}d)'
    else:
        lease_end_display = 'N/A'
        lease_end_label = 'Your Lease Ends'

    # Load interactions and run scam analysis on all units
    interactions_data = {}
    if INTERACTIONS_JSON.exists():
        try:
            interactions_data = json.loads(INTERACTIONS_JSON.read_text(encoding='utf-8')).get('units', {})
        except Exception:
            pass

    from scripts.check_scam import analyze_unit, compute_market_stats, compute_cross_refs
    market_stats = compute_market_stats(all_units)
    cross_refs = compute_cross_refs(all_units)
    scam_results = {}
    for u in all_units:
        uid = u.get('id', '')
        ix = interactions_data.get(uid, {})
        result = analyze_unit(u, ix, market_stats, cross_refs)
        scam_results[uid] = result
        u['scam_score'] = result['score']
        u['scam_level'] = result['level']

    # Generate per-unit detail pages for all units in the database
    all_units_map = {u.get('id', ''): u for u in all_units}
    for u in all_units:
        uid = u.get('id', '')
        unit_dir = PROJECT_ROOT / 'outputs' / 'apartments' / uid
        unit_dir.mkdir(parents=True, exist_ok=True)
        detail_path = unit_dir / 'index.html'
        detail_html = generate_unit_detail_html(
            u, lease_end, config.get('target_location'),
            scam_result=scam_results.get(uid),
            interactions_entry=interactions_data.get(uid, {}),
            all_units_map=all_units_map,
        )
        detail_path.write_text(detail_html, encoding='utf-8')

    # Search criteria, shown in the criteria bar and used by the client-side filter
    target = config.get('target_location') or {}
    target_name = target.get('name', 'target location')
    radius = config.get('search_radius_miles')
    min_price = config.get('min_price')
    max_price = config.get('max_price')
    min_beds = config.get('min_beds')
    max_beds = config.get('max_beds')

    criteria_parts = []
    if radius is not None:
        criteria_parts.append(f'within {radius} mi of {target_name}')
    if min_price is not None or max_price is not None:
        lo = f'${min_price}' if min_price is not None else '$0'
        hi = f'${max_price}' if max_price is not None else '+'
        criteria_parts.append(f'{lo}–{hi}/mo')
    if min_beds is not None:
        beds_part = f'{min_beds}+'
        if max_beds is not None and max_beds != min_beds:
            beds_part = f'{min_beds}–{max_beds}'
        criteria_parts.append(f'{beds_part} bd')
    criteria_text = ' · '.join(criteria_parts) if criteria_parts else 'No filters configured'

    js_config = {
        'target_location': target or None,
        'target_name': target_name,
        'search_radius_miles': radius,
        'min_price': min_price,
        'max_price': max_price,
        'min_beds': min_beds,
        'max_beds': max_beds,
        'min_baths': config.get('min_baths'),
        'min_sqft': config.get('min_sqft'),
        'min_quality': config.get('min_quality'),
        'current_lease_end': lease_end_str,
    }

    initial_data = {
        'units': [unit_to_js(u) for u in all_units],
        'total_units': units_data.get('total_units', len(all_units)),
        'last_updated': units_data.get('last_updated'),
    }

    config_json = json.dumps(js_config)
    initial_data_json = json.dumps(initial_data)

    # Load scrape activity log for the status modal
    scrape_log_path = PROJECT_ROOT / 'outputs' / 'scrape_log.json'
    scrape_log_data = {'runs': [], 'summary': {}}
    if scrape_log_path.exists():
        try:
            scrape_log_data = json.loads(scrape_log_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    scrape_log_data['total_units'] = len(all_units)
    all_sources = ['apartments_com', 'craigslist', 'realtor_com', 'zumper']
    for src in all_sources:
        if src not in scrape_log_data.get('summary', {}):
            scrape_log_data.setdefault('summary', {})[src] = {
                'total_runs': 0, 'total_added': 0, 'total_found': 0,
                'total_captchas': 0, 'total_image_scans': 0,
                'total_photos': 0, 'last_run': None, 'total_errors': 0,
            }
    scrape_log_json = json.dumps(scrape_log_data)

    # Full HTML
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Rental Finder</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.Default.css" />
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');

    :root {{
      --bg:        #0f1117;
      --surface:   #181c27;
      --border:    #252a38;
      --accent:    #4fd1c5;
      --accent2:   #f6ad55;
      --muted:     #4a5168;
      --text:      #e2e8f0;
      --subtext:   #8892a4;
      --green:     #68d391;
      --red:       #fc8181;
      --purple:    #b794f4;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    html, body {{
      height: 100%;
    }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'DM Sans', sans-serif;
      font-size: 15px;
      line-height: 1.6;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }}

    header {{
      border-bottom: 1px solid var(--border);
      padding: 14px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex: 0 0 auto;
    }}

    .page-title {{
      font-size: 15px;
      font-weight: 500;
      color: var(--subtext);
    }}

    .layout {{
      flex: 1 1 auto;
      display: flex;
      padding: 16px 20px;
      min-height: 0;
      overflow: hidden;
    }}

    .sidebar {{
      flex: 0 0 360px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      overflow-y: auto;
      padding-right: 14px;
      min-width: 240px;
      max-width: 70%;
    }}

    .resizer {{
      flex: 0 0 8px;
      cursor: col-resize;
      position: relative;
    }}

    .resizer::after {{
      content: '';
      position: absolute;
      top: 0;
      bottom: 0;
      left: 3px;
      width: 2px;
      border-radius: 1px;
      background: var(--border);
      transition: background 0.15s ease;
    }}

    .resizer:hover::after,
    .resizer.resizing::after {{
      background: var(--accent);
    }}

    .list-panel {{
      flex: 1 1 auto;
      min-width: 0;
      overflow: auto;
      padding-left: 6px;
    }}

    .stats {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px 16px;
      font-size: 13px;
      color: var(--subtext);
    }}

    .stat {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}

    .stat-value {{
      font-family: 'DM Mono', monospace;
      font-size: 18px;
      font-weight: 500;
      color: var(--accent);
    }}

    .stat-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }}

    .criteria-bar {{
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 8px;
      padding: 12px 16px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 13px;
    }}

    .criteria-text {{
      color: var(--subtext);
    }}

    .criteria-text strong {{
      color: var(--text);
    }}

    .edit-icon-btn {{
      background: transparent;
      border: 1px solid var(--border);
      color: var(--subtext);
      border-radius: 4px;
      padding: 1px 6px;
      font-size: 11px;
      line-height: 1.6;
      cursor: pointer;
      margin-left: 6px;
    }}

    .edit-icon-btn:hover {{
      border-color: var(--accent);
      color: var(--accent);
    }}

    .criteria-edit {{
      display: none;
      flex-direction: column;
      gap: 8px;
      width: 100%;
      margin-top: 4px;
      padding-top: 10px;
      border-top: 1px solid var(--border);
    }}

    .criteria-edit.open {{
      display: flex;
    }}

    .criteria-edit label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--subtext);
    }}

    .criteria-edit input,
    .criteria-edit select {{
      width: 100%;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 6px 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
    }}

    .checkbox-cell {{
      justify-content: center;
    }}

    .checkbox-label {{
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      text-transform: none;
      letter-spacing: normal;
      color: var(--text);
      cursor: pointer;
    }}

    .checkbox-label input[type="checkbox"] {{
      width: auto;
      accent-color: var(--accent);
      cursor: pointer;
    }}

    .criteria-edit-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px 10px;
    }}

    .criteria-edit-grid > div {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}

    .criteria-edit-grid .full {{
      grid-column: 1 / -1;
    }}

    .columns-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px 12px;
    }}

    .columns-grid label {{
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      text-transform: none;
      letter-spacing: normal;
      color: var(--text);
      cursor: pointer;
    }}

    .columns-grid input[type="checkbox"] {{
      width: auto;
      accent-color: var(--accent);
      cursor: pointer;
    }}

    .field-count {{
      font-size: 10px;
      color: var(--muted);
      font-family: 'DM Mono', monospace;
      margin-left: 2px;
    }}

    .work-locations-list {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 8px;
    }}

    .work-location-item {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 6px 8px;
    }}

    .work-location-name {{
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
    }}

    .work-location-address {{
      color: var(--subtext);
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .work-location-edit,
    .work-location-remove {{
      background: transparent;
      border: none;
      color: var(--subtext);
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
      padding: 0 2px;
    }}

    .work-location-edit:hover {{ color: var(--accent); }}
    .work-location-remove:hover {{ color: var(--red); }}

    .work-location-commute {{
      color: var(--subtext);
      font-size: 11px;
      white-space: nowrap;
    }}

    .work-loc-driving {{
      font-size: 11px;
      color: var(--subtext);
      display: block;
      line-height: 1.2;
    }}

    .work-loc-col {{
      width: 90px;
      text-align: right;
      font-size: 11px;
      color: var(--subtext);
      white-space: nowrap;
    }}

    /* Column visibility toggles - hides both header and body cells for a column */
    .units-table.hide-mine .mine-col {{ display: none; }}
    .units-table.hide-photo .photo-col,
    .units-table.hide-photo .unit-thumb-col {{ display: none; }}
    .units-table.hide-distance .distance-col,
    .units-table.hide-distance .unit-distance-col {{ display: none; }}
    .units-table.hide-price .price-col,
    .units-table.hide-price .unit-price-col {{ display: none; }}
    .units-table.hide-beds .beds-col {{ display: none; }}
    .units-table.hide-baths .baths-col {{ display: none; }}
    .units-table.hide-sqft .sqft-col {{ display: none; }}
    .units-table.hide-type .type-col,
    .units-table.hide-type .unit-type-col {{ display: none; }}
    .units-table.hide-amenities .amenity-col,
    .units-table.hide-amenities .unit-amenity-col {{ display: none; }}
    .units-table.hide-flooring .flooring-col {{ display: none; }}
    .units-table.hide-movein .movein-col {{ display: none; }}
    .units-table.hide-source .source-col,
    .units-table.hide-source .unit-source-col {{ display: none; }}
    .units-table.hide-quality .quality-col {{ display: none; }}
    .units-table.hide-risk .risk-col {{ display: none; }}
    .risk-col {{ font-size: 11px; white-space: nowrap; }}
    .risk-dot {{ font-weight: 600; }}
    .units-table.hide-contact .contact-col {{ display: none; }}
    .units-table.hide-details .details-col {{ display: none; }}
    .units-table.hide-kitchen .kitchen-col {{ display: none; }}
    .units-table.hide-outdoor .outdoor-col {{ display: none; }}
    .units-table.hide-size .size-col {{ display: none; }}
    .units-table.hide-score .score-col {{ display: none; }}

    .units-table.hide-commute-avg .commute-score-col {{ display: none; }}

    .score-col {{ white-space: nowrap; }}
    .score-bar {{
      display: inline-block;
      width: 40px;
      height: 8px;
      border-radius: 4px;
      background: var(--border);
      vertical-align: middle;
      margin-right: 5px;
      overflow: hidden;
    }}
    .score-bar-fill {{
      height: 100%;
      border-radius: 4px;
      transition: width 0.3s ease;
    }}
    .score-value {{
      font-weight: 600;
      font-size: 12px;
      font-family: 'DM Mono', monospace;
    }}

    .ranking-section-title {{
      font-size: 11px;
      font-weight: 600;
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin: 10px 0 6px 0;
      padding-bottom: 4px;
      border-bottom: 1px solid var(--border);
    }}
    .ranking-section-title:first-child {{ margin-top: 0; }}
    .ranking-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }}
    .ranking-row label {{
      flex: 0 0 120px;
      font-size: 12px;
      color: var(--subtext);
    }}
    .ranking-row input[type="number"],
    .ranking-row select {{
      flex: 1;
      min-width: 0;
    }}
    .ranking-row .rank-weight {{
      flex: 0 0 50px;
      text-align: center;
      font-size: 11px;
      color: var(--subtext);
    }}
    .ranking-row input[type="range"] {{
      flex: 0 0 60px;
      accent-color: var(--accent);
    }}
    .rank-weight-header {{
      display: flex;
      justify-content: flex-end;
      font-size: 10px;
      color: var(--muted);
      margin-bottom: 2px;
      padding-right: 2px;
    }}

    .criteria-edit-actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}

    .criteria-edit-status {{
      font-size: 11px;
      color: var(--subtext);
    }}

    .refresh-controls {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}

    .ctrl-btn {{
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 6px 12px;
      font-family: 'DM Sans', sans-serif;
      font-size: 12px;
      cursor: pointer;
      transition: border-color 0.2s ease;
    }}

    .ctrl-btn:hover {{ border-color: var(--accent); }}
    .ctrl-btn-icon {{ padding: 6px 10px; font-weight: 600; }}
    .ctrl-btn.primary {{ background: var(--accent); color: var(--bg); border-color: var(--accent); font-weight: 600; }}
    .ctrl-btn.primary:hover {{ opacity: 0.9; }}

    .howto-panel {{
      display: none;
      margin-top: 8px;
      width: 100%;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      font-size: 12px;
      color: var(--subtext);
    }}

    .howto-panel p {{ margin-bottom: 6px; }}

    .howto-panel code {{
      display: block;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 4px 8px;
      margin-bottom: 6px;
      font-family: 'DM Mono', monospace;
      font-size: 11px;
      color: var(--accent);
      white-space: nowrap;
      overflow-x: auto;
    }}

    #map {{
      width: 100%;
      aspect-ratio: 1 / 1;
      border-radius: 8px;
      border: 1px solid var(--border);
    }}

    .map-marker {{
      width: 14px;
      height: 14px;
      border-radius: 50%;
      border: 2px solid #1a1f2e;
      box-shadow: 0 1px 3px rgba(0,0,0,0.5);
    }}
    .map-marker.marker-apartment, .map-legend-dot.marker-apartment {{ background: var(--accent); }}
    .map-marker.marker-house, .map-legend-dot.marker-house {{ background: var(--accent2); }}
    .map-marker.marker-other, .map-legend-dot.marker-other {{ background: var(--muted); }}

    .map-marker-work {{
      width: 22px;
      height: 22px;
      border-radius: 5px;
      background: var(--purple);
      border: 2px solid #1a1f2e;
      box-shadow: 0 1px 3px rgba(0,0,0,0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      line-height: 1;
    }}
    .map-legend-dot.marker-work {{ background: var(--purple); border-radius: 3px; }}

    /* Highlight Leaflet MarkerCluster bubble when its contained marker is hovered */
    .marker-cluster.cluster-highlighted div {{
      background-color: rgba(79, 209, 197, 0.5) !important;
    }}
    .marker-cluster.cluster-highlighted span {{
      color: #fff !important;
      font-weight: 700;
    }}
    .marker-cluster.cluster-highlighted {{
      outline: 3px solid var(--accent);
      outline-offset: 2px;
    }}

    .map-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 6px;
      font-size: 12px;
      color: var(--subtext);
    }}
    .map-legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .map-legend-dot {{
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      border: 2px solid #1a1f2e;
    }}

    .photo-tooltip {{ padding: 0; border: none; background: transparent; box-shadow: none; }}
    .photo-tooltip::before {{ display: none; }}
    .popup-photo {{
      width: 180px; height: 130px; object-fit: cover; border-radius: 4px;
      display: block; margin: 6px 0; cursor: pointer;
    }}
    .popup-photo-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 2px;
      width: 180px;
      height: 130px;
      border-radius: 4px;
      overflow: hidden;
      margin: 6px 0;
    }}
    .popup-photo-grid img {{
      width: 100%; height: 100%; object-fit: cover; cursor: pointer; display: block;
    }}
    .popup-photo-grid img:hover {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    .popup-type, .tooltip-type {{
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      color: #555;
    }}
    .tooltip-type {{
      font-weight: 600;
      background: #fff;
      padding: 2px 6px;
      border-radius: 4px 4px 0 0;
      margin-bottom: -2px;
    }}
    .popup-address {{
      font-size: 12px;
      color: #555;
      margin: 2px 0 4px;
    }}
    .unit-tooltip-wrapper {{ background: #fff; border: 1px solid #ccc; border-radius: 4px; padding: 0; box-shadow: 0 1px 4px rgba(0,0,0,0.2); }}
    .unit-tooltip-wrapper::before {{ display: none; }}
    .unit-tooltip {{
      display: flex;
      align-items: flex-start;
      gap: 5px;
      padding: 5px 8px;
      max-width: 200px;
    }}
    .unit-tooltip .map-marker {{ margin-top: 2px; flex-shrink: 0; }}
    .unit-tooltip b {{ display: block; font-size: 12px; color: #1a1f2e; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 170px; }}
    .tooltip-header-address {{
      font-size: 11px;
      color: #666;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 170px;
    }}
    .popup-notes {{
      font-style: italic;
      color: #555;
      margin: 4px 0;
      max-width: 180px;
      white-space: pre-wrap;
    }}

    .filter-note {{
      background: rgba(79, 209, 197, 0.05);
      border-left: 3px solid var(--accent);
      padding: 12px 16px;
      font-size: 12px;
      color: var(--subtext);
    }}

    .empty-state {{
      text-align: center;
      padding: 80px 40px;
      color: var(--muted);
    }}

    .empty-state .icon {{ font-size: 48px; display: block; margin-bottom: 16px; }}
    .empty-state h2 {{ font-size: 18px; font-weight: 400; color: var(--subtext); margin-bottom: 8px; }}
    .empty-state p {{ font-size: 13px; line-height: 1.7; }}
    .empty-state code {{ background: var(--surface); padding: 4px 8px; border-radius: 3px; }}

    .units-table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--surface);
      border: 1px solid var(--border);
      font-size: 13px;
    }}

    .units-table thead {{
      background: var(--border);
      position: sticky;
      top: 0;
    }}

    .units-table th {{
      padding: 10px 6px;
      text-align: left;
      font-weight: 600;
      color: var(--text);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}

    .units-table th.sortable {{
      cursor: pointer;
      user-select: none;
    }}

    .units-table th.sortable:hover {{
      color: var(--accent);
    }}

    .units-table th.sort-asc::after {{ content: ' \\25b2'; font-size: 9px; }}
    .units-table th.sort-desc::after {{ content: ' \\25bc'; font-size: 9px; }}

    .units-table tbody tr {{
      border-bottom: 1px solid var(--border);
      transition: background 0.2s ease;
    }}

    .units-table tbody tr:hover {{
      background: rgba(79, 209, 197, 0.05);
    }}

    .units-table tbody tr:last-child {{
      border-bottom: none;
    }}

    .grp-order {{
      display: none;
      font-size: 9px;
      font-weight: 700;
      background: var(--accent);
      color: var(--bg);
      border-radius: 50%;
      width: 14px;
      height: 14px;
      line-height: 14px;
      text-align: center;
      margin-left: 3px;
    }}
    .grp-label input:checked ~ .grp-order {{ display: inline-block; }}

    /* Address group header row */
    .units-table tr.addr-group-hdr td {{
      background: rgba(79, 209, 197, 0.06);
      border-left: 3px solid var(--accent);
      padding: 7px 10px;
      font-weight: 600;
      color: var(--text);
      cursor: pointer;
      user-select: none;
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .units-table tr.addr-group-hdr:hover td {{ background: rgba(79, 209, 197, 0.12); }}
    .units-table tr.addr-group-hdr.depth-1 td {{ padding-left: 28px; font-size: 12px; font-weight: 500; background: rgba(79, 209, 197, 0.03); border-left-color: var(--muted); }}
    .units-table tr.addr-group-hdr.depth-2 td {{ padding-left: 46px; font-size: 12px; font-weight: 400; background: rgba(79, 209, 197, 0.02); border-left-color: var(--border); }}
    .units-table tr.addr-group-hdr.depth-3 td {{ padding-left: 64px; font-size: 11px; font-weight: 400; background: rgba(79, 209, 197, 0.01); border-left-color: var(--border); }}
    .addr-group-toggle {{
      display: inline-block;
      margin-right: 6px;
      font-size: 9px;
      transition: transform 0.15s;
      color: var(--accent);
    }}
    .addr-group-hdr.collapsed .addr-group-toggle {{ transform: rotate(-90deg); }}
    .addr-group-summary {{ color: var(--subtext); font-weight: 400; font-size: 12px; margin-left: 10px; }}

    /* Units sharing an address (same property, different floorplans) are
       grouped together in the list - tie them together visually */
    .units-table tbody tr.same-property {{
      border-left: 2px solid var(--accent);
    }}
    .units-table tbody tr.group-continues {{
      border-bottom: none;
    }}

    .units-table td {{
      padding: 10px 8px;
      color: var(--text);
      vertical-align: middle;
    }}

    .photo-col {{
      width: 80px;
      text-align: center;
    }}

    .mine-col {{
      width: 84px;
      text-align: center;
    }}

    .icon-btn {{
      background: transparent;
      border: 1px solid transparent;
      border-radius: 4px;
      font-size: 15px;
      line-height: 1;
      padding: 2px 4px;
      cursor: pointer;
      color: var(--subtext);
    }}

    .icon-btn:hover {{
      border-color: var(--border);
      color: var(--text);
    }}

    .fav-btn.active {{
      color: var(--accent2);
    }}

    .note-btn.active {{
      color: var(--accent);
    }}

    .timeline-btn.active {{
      color: var(--purple);
    }}

    .quality-col {{
      width: 64px;
      text-align: right;
      color: var(--accent2);
      letter-spacing: 1px;
      cursor: default;
    }}

    .contact-col {{
      font-size: 11px;
      white-space: nowrap;
    }}

    .contact-icons {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}

    .contact-icon-wrap {{
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 2px;
    }}

    .contact-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 26px;
      height: 26px;
      border-radius: 4px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--accent);
      font-size: 14px;
      text-decoration: none;
      cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
    }}

    .contact-icon:hover {{
      border-color: var(--accent);
      background: rgba(79, 209, 197, 0.1);
    }}

    .contact-tip {{
      position: absolute;
      bottom: calc(100% + 6px);
      left: 50%;
      transform: translateX(-50%);
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 3px 6px;
      white-space: nowrap;
      font-size: 11px;
      color: var(--text);
      box-shadow: 0 2px 6px rgba(0,0,0,0.15);
      display: none;
      align-items: center;
      gap: 5px;
      z-index: 100;
      pointer-events: auto;
    }}
    .contact-tip::after {{
      content: '';
      position: absolute;
      top: 100%;
      left: 50%;
      transform: translateX(-50%);
      border: 5px solid transparent;
      border-top-color: var(--border);
    }}
    .contact-icon-wrap:hover .contact-tip {{
      display: inline-flex;
    }}
    .contact-tip-copy {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      color: var(--subtext);
      padding: 1px;
      border-radius: 3px;
      transition: color 0.15s;
    }}
    .contact-tip-copy:hover {{
      color: var(--accent);
    }}
    .contact-tip-copy.copied {{
      color: var(--green);
    }}

    .contact-name-tag {{
      font-size: 10px;
      color: var(--subtext);
      max-width: 60px;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .contact-link {{
      color: var(--accent);
      text-decoration: none;
    }}

    .contact-link:hover {{ text-decoration: underline; }}

    .contact-name {{
      font-size: 10px;
      color: var(--subtext);
      margin-bottom: 2px;
    }}

    .scam-row td {{
      background: rgba(220, 60, 60, 0.08) !important;
      color: var(--subtext);
    }}

    .scam-row .unit-link {{ color: var(--subtext); }}

    .scam-btn.scam-active {{
      color: #e05252;
      opacity: 1;
    }}

    .distance-col {{
      width: 70px;
      text-align: right;
      color: var(--accent);
      font-weight: 500;
    }}

    .unit-distance-col {{
      text-align: right;
    }}

    .unit-thumb {{
      width: 70px;
      height: 70px;
      object-fit: cover;
      border-radius: 4px;
      display: inline-block;
      cursor: pointer;
      border: 1px solid var(--border);
    }}

    .unit-thumb:hover {{
      border-color: var(--accent);
    }}

    .unit-thumb-empty {{
      width: 70px;
      height: 70px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: var(--border);
      border-radius: 4px;
      color: var(--muted);
      border: 1px solid var(--border);
    }}

    .thumb-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 2px;
      width: 70px;
      height: 70px;
      border-radius: 4px;
      overflow: hidden;
      border: 1px solid var(--border);
    }}

    .thumb-grid img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      cursor: pointer;
      display: block;
    }}

    .thumb-grid img:hover {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}

    .address-col {{
      width: 25%;
      min-width: 200px;
    }}

    .unit-link {{
      color: var(--text);
      font-weight: 500;
    }}

    .dup-tag {{
      font-size: 11px;
      color: var(--subtext);
      font-style: italic;
      margin-top: 2px;
    }}

    .unit-id-line {{
      display: flex;
      align-items: center;
      gap: 6px;
      margin-top: 2px;
    }}

    .unit-id-tag {{
      font-size: 11px;
      color: var(--subtext);
      font-family: 'DM Mono', monospace;
    }}

    .linked-badge {{
      font-size: 10px;
      color: var(--accent);
      background: rgba(0,194,168,0.10);
      padding: 1px 6px;
      border-radius: 8px;
      margin-left: 4px;
      white-space: nowrap;
      cursor: pointer;
    }}
    .linked-badge:hover {{
      background: rgba(0,194,168,0.22);
    }}
    .linked-badge-dup {{
      color: var(--subtext);
      background: rgba(255,255,255,0.06);
    }}

    .link-icons {{
      margin-left: auto;
    }}

    .price-col {{
      width: 90px;
      text-align: right;
      font-family: 'DM Mono', monospace;
      font-weight: 600;
      color: var(--accent2);
    }}

    .unit-price-col {{
      text-align: right;
    }}

    .spec-col {{
      width: 70px;
      min-width: 55px;
      text-align: right;
      white-space: nowrap;
      color: var(--muted);
    }}

    .unit-spec-col {{
      text-align: right;
      white-space: nowrap;
    }}

    .type-col {{
      width: 90px;
      text-align: left;
      font-size: 12px;
    }}

    .unit-type-col {{
      text-align: left;
      font-size: 12px;
      color: var(--subtext);
    }}

    .age-badge {{
      display: inline-block;
      background: var(--red);
      color: var(--bg);
      border-radius: 3px;
      font-size: 10px;
      font-weight: 700;
      padding: 0 4px;
      margin-left: 2px;
      vertical-align: middle;
      cursor: default;
    }}

    .amenity-col {{
      width: 80px;
      text-align: center;
      font-size: 12px;
    }}

    .unit-amenity-col {{
      text-align: center;
    }}

    .amenity-more {{
      font-size: 10px;
      color: var(--subtext);
    }}

    .flooring-col {{
      width: 80px;
      text-align: left;
      font-size: 12px;
      color: var(--subtext);
    }}

    .movein-col {{
      width: 90px;
      text-align: left;
      font-size: 12px;
      color: var(--subtext);
    }}

    .source-col {{
      width: 90px;
      text-align: left;
      color: var(--muted);
      font-size: 12px;
    }}

    .unit-source-col {{
      text-align: left;
    }}

    .details-col {{
      width: 70px;
      text-align: center;
      font-size: 12px;
    }}

    .details-link {{
      color: var(--accent);
      text-decoration: none;
    }}

    .details-link:hover {{
      text-decoration: underline;
    }}

    .link-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 1px 5px;
      border-radius: 3px;
      font-size: 10px;
      text-decoration: none;
      cursor: pointer;
      transition: border-color 0.15s, color 0.15s, background 0.15s;
      white-space: nowrap;
    }}

    .link-icon.link-source {{
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--subtext);
    }}

    .link-icon.link-source:hover {{
      border-color: var(--accent);
      color: var(--accent);
      background: rgba(79, 209, 197, 0.1);
    }}

    .link-icon.link-details {{
      border: none;
      background: transparent;
      color: var(--accent);
      text-decoration: underline;
      text-underline-offset: 2px;
    }}

    .link-icon.link-details:hover {{
      color: var(--text);
    }}

    .tools-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      gap: 8px;
    }}

    .tools-group {{
      display: flex;
      flex-wrap: nowrap;
      align-items: flex-end;
      gap: 8px;
    }}

    .filter-group {{
      flex: 1 1 100px;
      min-width: 100px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 13px;
      color: var(--subtext);
    }}

    .filter-group select,
    .filter-group input {{
      width: 100%;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 6px 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
    }}

    .tool-btn {{
      flex: 1 1 100px;
      min-width: 100px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 7px 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
      cursor: pointer;
      transition: border-color 0.2s ease, color 0.2s ease;
    }}

    .tool-btn:hover {{
      border-color: var(--accent);
    }}

    .tool-btn.open {{
      border-color: var(--accent);
      color: var(--accent);
    }}

    .tool-badge {{
      background: var(--accent);
      color: var(--bg);
      border-radius: 10px;
      font-size: 10px;
      font-weight: 700;
      padding: 1px 6px;
      font-family: 'DM Mono', monospace;
    }}

    .tools-panel {{
      margin-top: 0;
      padding: 12px 16px;
      border-top: none;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
    }}

    .lightbox-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.85);
      align-items: center;
      justify-content: center;
      z-index: 1000;
      cursor: zoom-out;
    }}

    .lightbox-overlay img {{
      max-width: 90vw;
      max-height: 90vh;
      border-radius: 4px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
    }}

    .lightbox-nav {{
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      background: rgba(0, 0, 0, 0.5);
      border: none;
      color: #fff;
      font-size: 28px;
      width: 48px;
      height: 48px;
      border-radius: 50%;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1001;
    }}

    .lightbox-nav:hover {{
      background: rgba(0, 0, 0, 0.8);
    }}

    .lightbox-prev {{ left: 24px; }}
    .lightbox-next {{ right: 24px; }}

    .lightbox-counter {{
      position: absolute;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(0, 0, 0, 0.5);
      color: #fff;
      padding: 4px 14px;
      border-radius: 12px;
      font-family: 'DM Mono', monospace;
      font-size: 12px;
      z-index: 1001;
    }}

    .notes-modal-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      align-items: center;
      justify-content: center;
      z-index: 1100;
    }}

    .notes-modal {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      width: 90%;
      max-width: 420px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}

    .notes-modal h3 {{
      font-size: 14px;
      font-weight: 500;
      color: var(--text);
    }}

    .notes-modal textarea {{
      width: 100%;
      min-height: 120px;
      resize: vertical;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
    }}

    .notes-modal-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }}

    .timeline-modal {{
      max-width: 480px;
    }}

    .timeline-entries {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: 280px;
      overflow-y: auto;
    }}

    .timeline-entry {{
      display: flex;
      gap: 10px;
      align-items: flex-start;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 8px 10px;
      font-size: 13px;
    }}

    .timeline-entry-date {{
      font-family: 'DM Mono', monospace;
      font-size: 11px;
      color: var(--accent);
      white-space: nowrap;
      padding-top: 1px;
    }}

    .timeline-entry-text {{
      flex: 1;
      white-space: pre-wrap;
      word-break: break-word;
    }}

    .timeline-entry-remove {{
      background: transparent;
      border: none;
      color: var(--subtext);
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      padding: 0 2px;
    }}

    .timeline-entry-remove:hover {{ color: var(--red); }}

    .timeline-empty {{
      font-size: 12px;
      color: var(--subtext);
      font-style: italic;
    }}

    .timeline-add-row {{
      display: flex;
      gap: 8px;
    }}

    .timeline-add-row input[type="date"] {{
      flex: 0 0 130px;
    }}

    .timeline-add-row input[type="text"] {{
      flex: 1;
    }}

    .timeline-add-row input {{
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 8px 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
    }}

    .mobile-specs-row {{
      display: none;
    }}

    .mobile-sidebar-toggle {{
      display: none;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 14px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
      cursor: pointer;
      width: 100%;
      text-align: center;
      margin-bottom: 8px;
    }}
    .mobile-sidebar-toggle:hover {{ border-color: var(--accent); }}
    .mobile-sidebar-toggle .toggle-arrow {{
      display: inline-block;
      transition: transform 0.2s;
      margin-right: 6px;
      font-size: 10px;
    }}
    .mobile-sidebar-toggle.collapsed .toggle-arrow {{ transform: rotate(-90deg); }}

    @media (max-width: 860px) {{
      body {{
        height: auto;
        overflow: auto;
        font-size: 14px;
      }}

      header {{
        padding: 10px 14px;
        flex-wrap: wrap;
        gap: 8px;
      }}

      .layout {{
        flex-direction: column;
        overflow: visible;
        height: auto;
        padding: 10px 10px;
        gap: 10px;
      }}

      .mobile-sidebar-toggle {{
        display: block;
      }}

      .sidebar {{
        flex: 0 0 auto;
        width: 100%;
        max-width: none;
        min-width: 0;
        overflow-y: visible;
        padding-right: 0;
        gap: 10px;
      }}

      .sidebar.mobile-collapsed {{
        display: none;
      }}

      .resizer {{
        display: none;
      }}

      .list-panel {{
        flex: 1 1 auto;
        padding-left: 0;
        overflow: visible;
      }}

      #map {{
        aspect-ratio: 16 / 9;
        min-height: 200px;
      }}

      .stats {{
        grid-template-columns: 1fr 1fr;
        gap: 8px 12px;
      }}

      .stat-value {{ font-size: 16px; }}

      .tools-row {{
        gap: 6px;
      }}

      .tools-group {{
        flex-wrap: wrap;
        gap: 6px;
      }}

      .tool-btn {{
        flex: 1 1 70px;
        min-width: 70px;
        padding: 8px 8px;
        font-size: 12px;
      }}

      .filter-group {{
        min-width: 80px;
      }}

      .criteria-edit-grid {{
        grid-template-columns: 1fr;
      }}

      .columns-grid {{
        grid-template-columns: 1fr;
      }}

      .ranking-row {{
        flex-wrap: wrap;
        gap: 4px;
      }}

      .ranking-row label {{
        flex: 0 0 100%;
      }}

      /* --- Card layout for table rows on mobile --- */
      .units-table {{
        border: none;
        background: transparent;
      }}

      .units-table thead {{
        display: none;
      }}

      .units-table tbody {{
        display: flex;
        flex-direction: column;
        gap: 10px;
      }}

      .units-table tbody tr.unit-row {{
        display: grid;
        grid-template-columns: auto 1fr;
        grid-template-rows: auto;
        gap: 2px 10px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 12px;
        position: relative;
      }}

      .units-table tbody tr.unit-row > td {{
        display: block;
        border: none;
      }}

      .units-table tbody tr.unit-row:hover {{
        background: var(--surface);
      }}

      .units-table tbody tr.scam-row {{
        border-color: rgba(220, 60, 60, 0.3);
      }}

      .units-table tbody tr.same-property {{
        border-left: 3px solid var(--accent);
      }}

      .units-table tbody tr.group-continues {{
        border-bottom: 1px solid var(--border);
      }}

      .units-table tbody tr.addr-group-hdr {{
        display: block;
        background: rgba(79, 209, 197, 0.06);
        border: 1px solid var(--border);
        border-left: 3px solid var(--accent);
        border-radius: 6px;
        padding: 0;
      }}

      .units-table tbody tr.addr-group-hdr td {{
        padding: 10px 12px;
        font-size: 13px;
      }}

      /* Photo - spans full width at top */
      .units-table tbody tr.unit-row .unit-thumb-col {{
        grid-column: 1;
        grid-row: 1 / 4;
        padding: 0;
        display: flex;
        align-items: flex-start;
      }}

      .unit-thumb, .unit-thumb-empty, .thumb-grid {{
        width: 64px !important;
        height: 64px !important;
      }}

      /* Address - next to photo */
      .units-table tbody tr.unit-row .unit-address-col {{
        grid-column: 2;
        grid-row: 1;
        padding: 0;
        min-width: 0;
      }}

      .units-table tbody tr.unit-row .unit-address-col .unit-link {{
        font-size: 14px;
        line-height: 1.3;
        word-break: break-word;
      }}

      .unit-id-line {{
        flex-wrap: wrap;
        gap: 4px;
      }}

      /* Price + key specs next to photo */
      .units-table tbody tr.unit-row .unit-price-col {{
        grid-column: 2;
        grid-row: 2;
        padding: 2px 0;
        text-align: left;
        font-size: 16px;
      }}

      /* Type badge row */
      .units-table tbody tr.unit-row .unit-type-col {{
        grid-column: 2;
        grid-row: 3;
        padding: 0;
      }}

      /* Spec cells in a row below */
      .units-table tbody tr.unit-row .beds-col,
      .units-table tbody tr.unit-row .baths-col,
      .units-table tbody tr.unit-row .sqft-col {{
        padding: 0;
        text-align: left;
        font-size: 13px;
      }}

      .units-table tbody tr.unit-row .beds-col {{
        grid-column: 1;
        grid-row: 4;
      }}

      .units-table tbody tr.unit-row .baths-col {{
        grid-column: 1;
        grid-row: 4;
      }}

      .units-table tbody tr.unit-row .sqft-col {{
        grid-column: 2;
        grid-row: 4;
      }}

      /* Combine beds/baths/sqft into one visual row */
      .units-table tbody tr.unit-row .beds-col,
      .units-table tbody tr.unit-row .baths-col,
      .units-table tbody tr.unit-row .sqft-col {{
        display: inline;
      }}

      /* Mobile card: secondary info row */
      .units-table tbody tr.unit-row .unit-distance-col {{
        grid-column: 1 / -1;
        grid-row: 5;
        padding: 4px 0 0;
        text-align: left;
      }}

      .units-table tbody tr.unit-row .unit-distance-col::before {{
        content: 'Distance: ';
        color: var(--subtext);
        font-size: 11px;
      }}

      /* Mine (favorites/notes) - top-right corner */
      .units-table tbody tr.unit-row .mine-col {{
        position: absolute;
        top: 8px;
        right: 8px;
        width: auto;
        padding: 0;
        display: flex;
        gap: 2px;
      }}

      /* Hide less important columns on mobile cards */
      .units-table tbody tr.unit-row .movein-col,
      .units-table tbody tr.unit-row .unit-amenity-col,
      .units-table tbody tr.unit-row .flooring-col,
      .units-table tbody tr.unit-row .unit-source-col,
      .units-table tbody tr.unit-row .risk-col,
      .units-table tbody tr.unit-row .kitchen-col,
      .units-table tbody tr.unit-row .outdoor-col,
      .units-table tbody tr.unit-row .size-col,
      .units-table tbody tr.unit-row .contact-col,
      .units-table tbody tr.unit-row .work-loc-col,
      .units-table tbody tr.unit-row .commute-score-col {{
        display: none;
      }}

      /* Quality and score - show inline at bottom */
      .units-table tbody tr.unit-row .quality-col {{
        grid-column: 1;
        grid-row: 6;
        padding: 4px 0 0;
        text-align: left;
        width: auto;
      }}

      .units-table tbody tr.unit-row .score-col {{
        grid-column: 2;
        grid-row: 6;
        padding: 4px 0 0;
        text-align: left;
      }}

      /* Hide individual spec cols on mobile, show combined row */
      .units-table tbody tr.unit-row .beds-col,
      .units-table tbody tr.unit-row .baths-col,
      .units-table tbody tr.unit-row .sqft-col {{
        display: none !important;
      }}

      .mobile-specs-row {{
        grid-column: 1 / -1;
        grid-row: 4;
        display: block !important;
        padding: 4px 0 0 !important;
        font-size: 13px;
        color: var(--muted);
      }}

      /* Lightbox */
      .lightbox-nav {{ width: 38px; height: 38px; font-size: 22px; }}
      .lightbox-prev {{ left: 8px; }}
      .lightbox-next {{ right: 8px; }}

      /* Modals */
      .notes-modal, .scrape-modal {{
        width: 95%;
        max-height: 85vh;
      }}

      .scrape-modal {{
        padding: 14px 16px;
      }}

      .scrape-totals-bar {{
        flex-wrap: wrap;
      }}

      .scrape-summary-grid {{
        grid-template-columns: 1fr;
      }}

      .scrape-run-table {{
        font-size: 10px;
      }}

      .scrape-run-table th,
      .scrape-run-table td {{
        padding: 4px 4px;
      }}

      /* Work location items */
      .work-location-item {{
        flex-wrap: wrap;
      }}

      /* Filter note */
      .filter-note {{
        font-size: 11px;
        padding: 10px 12px;
      }}

      /* Empty state */
      .empty-state {{
        padding: 40px 20px;
      }}

      .empty-state h2 {{ font-size: 16px; }}
    }}

    /* Extra-small phones */
    @media (max-width: 400px) {{
      .layout {{ padding: 6px 6px; }}

      .units-table tbody tr.unit-row {{
        padding: 10px;
      }}

      .unit-thumb, .unit-thumb-empty, .thumb-grid {{
        width: 54px !important;
        height: 54px !important;
      }}

      .stat-value {{ font-size: 14px; }}

      .tool-btn {{
        min-width: 60px;
        font-size: 11px;
        padding: 6px 6px;
      }}
    }}

    .scrape-status-link {{
      font-size: 11px;
      color: var(--muted);
      text-decoration: none;
      font-family: 'DM Mono', monospace;
      letter-spacing: 0.05em;
      transition: color 0.15s;
    }}
    .scrape-status-link:hover {{
      color: var(--accent);
    }}
    .scrape-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      z-index: 1200;
      align-items: center;
      justify-content: center;
    }}
    .scrape-overlay.open {{
      display: flex;
    }}
    .scrape-modal {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      width: 90%;
      max-width: 720px;
      max-height: 80vh;
      overflow-y: auto;
      padding: 20px 24px;
    }}
    .scrape-modal h2 {{
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .scrape-modal h2 .close-x {{
      cursor: pointer;
      color: var(--subtext);
      font-size: 18px;
      line-height: 1;
      background: none;
      border: none;
      padding: 0 4px;
    }}
    .scrape-modal h2 .close-x:hover {{ color: var(--text); }}
    .scrape-section-title {{
      font-size: 11px;
      font-weight: 600;
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin: 16px 0 8px 0;
      padding-bottom: 4px;
      border-bottom: 1px solid var(--border);
    }}
    .scrape-section-title:first-of-type {{ margin-top: 0; }}
    .scrape-summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 10px;
    }}
    .scrape-totals-bar {{
      display: flex;
      gap: 6px;
      margin-bottom: 14px;
      padding: 12px 0;
      border-bottom: 1px solid var(--border);
    }}
    .scrape-total-item {{
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
    }}
    .scrape-total-value {{
      font-family: 'DM Mono', monospace;
      font-size: 20px;
      font-weight: 600;
      color: var(--accent);
    }}
    .scrape-total-label {{
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--subtext);
    }}
    .scrape-source-card {{
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 12px;
    }}
    .scrape-source-card.scrape-source-inactive {{
      opacity: 0.45;
    }}
    .scrape-source-name {{
      font-size: 12px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 6px;
      text-transform: capitalize;
    }}
    .scrape-source-stats {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px 12px;
      font-size: 11px;
    }}
    .scrape-source-stats dt {{
      color: var(--subtext);
    }}
    .scrape-source-stats dd {{
      color: var(--text);
      font-family: 'DM Mono', monospace;
      text-align: right;
    }}
    .scrape-run-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 11px;
    }}
    .scrape-run-table th {{
      text-align: left;
      padding: 6px 8px;
      color: var(--subtext);
      font-weight: 500;
      border-bottom: 1px solid var(--border);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .scrape-run-table td {{
      padding: 5px 8px;
      color: var(--text);
      border-bottom: 1px solid rgba(37, 42, 56, 0.5);
      font-family: 'DM Mono', monospace;
    }}
    .scrape-run-table tr:last-child td {{ border-bottom: none; }}
    .scrape-run-status {{
      display: inline-block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      margin-right: 4px;
    }}
    .scrape-run-status.completed {{ background: var(--green); }}
    .scrape-run-status.in_progress {{ background: var(--accent2); }}
    .scrape-run-status.failed {{ background: var(--red); }}
    .scrape-empty {{
      font-size: 12px;
      color: var(--subtext);
      font-style: italic;
      padding: 20px 0;
      text-align: center;
    }}
  </style>
</head>
<body>
  <header>
    <div class="page-title">UNITS FOUND</div>
    <a href="#" class="scrape-status-link" onclick="openScrapeStatus(); return false;" title="Scrape activity log">activity</a>
  </header>

  <div class="layout">
    <button class="mobile-sidebar-toggle" id="mobile-sidebar-toggle" onclick="toggleMobileSidebar()">
      <span class="toggle-arrow">▼</span> Filters &amp; Map
    </button>
    <aside class="sidebar" id="sidebar">
      <div class="tools-row">
        <div class="filter-group">
          <label for="search-contains">Contains</label>
          <input type="text" id="search-contains" placeholder="e.g. gated" oninput="applySearchFilters()">
        </div>
        <div class="filter-group">
          <label for="search-excludes">Doesn't contain</label>
          <input type="text" id="search-excludes" placeholder="e.g. 55+" oninput="applySearchFilters()">
        </div>
      </div>

      <div class="stats">
        <div class="stat">
          <div class="stat-value" id="stat-total">{total}</div>
          <div class="stat-label">Units Matching Criteria</div>
        </div>
        <div class="stat">
          <div class="stat-value" id="stat-scraped">{units_data.get('total_units', 0)}</div>
          <div class="stat-label">Total Retrieved</div>
        </div>
        <div class="stat">
          <div class="stat-value" id="stat-updated">{units_data.get('last_updated', 'N/A').split('T')[0]}</div>
          <div class="stat-label">Last Updated</div>
        </div>
        <div class="stat">
          <div class="stat-value" id="stat-lease-value">{lease_end_display}</div>
          <div class="stat-label" id="stat-lease-label">{lease_end_label}</div>
        </div>
      </div>

      <div id="map"></div>
      <div id="map-legend" class="map-legend"></div>

      <div class="tools-row">
        <div class="tools-group">
          <div class="filter-group">
            <label for="type-select">Housing type</label>
            <select id="type-select" onchange="applyTypeFilter()">
              <option value="">All types</option>
            </select>
          </div>
          <div class="filter-group">
            <label for="quality-select">Min quality</label>
            <select id="quality-select" onchange="applyQualityFilter()">
              <option value="">Any</option>
              <option value="2">&#9733;&#9733; (2+)</option>
              <option value="3">&#9733;&#9733;&#9733; (3+)</option>
              <option value="4">&#9733;&#9733;&#9733;&#9733; (4+)</option>
              <option value="5">&#9733;&#9733;&#9733;&#9733;&#9733; (5)</option>
            </select>
          </div>
          <div class="filter-group">
            <label for="favorites-select">Favorites</label>
            <select id="favorites-select" onchange="applyFavoritesFilter()">
              <option value="">All units</option>
              <option value="1">&#9733; Favorites only</option>
            </select>
          </div>
        </div>
        <div class="tools-group">
          <button id="list-filters-btn" class="tool-btn" title="Narrow the displayed list">Filters<span id="list-filters-badge" class="tool-badge" style="display:none;"></span></button>
          <button id="columns-btn" class="tool-btn" title="Choose which table columns are displayed">Columns<span id="columns-badge" class="tool-badge" style="display:none;"></span></button>
          <button id="work-locations-btn" class="tool-btn" title="Add work locations to show distance columns">Work<span id="work-locations-badge" class="tool-badge" style="display:none;"></span></button>
          <button id="ranking-btn" class="tool-btn" title="Set target values to rank units by overall fit">Rank<span id="ranking-badge" class="tool-badge" style="display:none;"></span></button>
        </div>
      </div>

      <div id="list-filters-panel" class="criteria-edit tools-panel">
        <div id="list-filters-grid" class="criteria-edit-grid">
          <div>
            <label for="lf-max-distance">Distance (mi) <span class="field-count" data-count="distance"></span></label>
            <input type="number" id="lf-max-distance" min="0" step="0.5" placeholder="Max" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-min-price">Price ($) <span class="field-count" data-count="price"></span></label>
            <div style="display:flex;gap:4px;">
              <input type="number" id="lf-min-price" min="0" step="50" placeholder="Min" oninput="applyListFilters()" style="flex:1;">
              <input type="number" id="lf-max-price" min="0" step="50" placeholder="Max" oninput="applyListFilters()" style="flex:1;">
            </div>
          </div>
          <div>
            <label for="lf-min-beds">Beds <span class="field-count" data-count="beds"></span></label>
            <input type="number" id="lf-min-beds" min="0" step="1" placeholder="Min" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-min-baths">Baths <span class="field-count" data-count="baths"></span></label>
            <input type="number" id="lf-min-baths" min="0" step="0.5" placeholder="Min" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-min-sqft">Sqft <span class="field-count" data-count="sqft"></span></label>
            <input type="number" id="lf-min-sqft" min="0" step="50" placeholder="Min" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-size-impression">Size feel <span class="field-count" data-count="size"></span></label>
            <select id="lf-size-impression" onchange="applyListFilters()">
              <option value="">Any</option>
              <option value="spacious">Spacious</option>
              <option value="average">Average</option>
              <option value="cramped">Cramped</option>
            </select>
          </div>
          <div>
            <label for="lf-available-by">Move-in <span class="field-count" data-count="movein"></span></label>
            <input type="date" id="lf-available-by" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-flooring">Flooring <span class="field-count" data-count="flooring"></span></label>
            <select id="lf-flooring" onchange="applyListFilters()">
              <option value="">Any</option>
              <option value="hardwood">Hardwood</option>
              <option value="tile">Tile</option>
              <option value="carpet">Carpet</option>
              <option value="vinyl">Vinyl</option>
              <option value="concrete">Concrete</option>
              <option value="mixed">Mixed</option>
              <option value="unknown">Unknown</option>
            </select>
          </div>
          <div>
            <label for="lf-kitchen-style">Kitchen <span class="field-count" data-count="kitchen"></span></label>
            <select id="lf-kitchen-style" onchange="applyListFilters()">
              <option value="">Any</option>
              <option value="modern">Modern</option>
              <option value="updated">Updated</option>
              <option value="dated">Dated</option>
            </select>
          </div>
          <div>
            <label for="lf-outdoor-space">Outdoor <span class="field-count" data-count="outdoor"></span></label>
            <select id="lf-outdoor-space" onchange="applyListFilters()">
              <option value="">Any</option>
              <option value="balcony">Balcony</option>
              <option value="patio">Patio</option>
              <option value="yard">Yard</option>
              <option value="none">None</option>
            </select>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-gated" onchange="applyListFilters()"> Gated <span class="field-count" data-count="gated"></span></label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-washer-dryer" onchange="applyListFilters()"> Washer/Dryer <span class="field-count" data-count="washerDryer"></span></label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-has-contact" onchange="applyListFilters()"> Has contact info <span class="field-count" data-count="contact"></span></label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-hide-age-restricted" onchange="applyListFilters()" checked> Hide age-restricted <span class="field-count" data-count="ageRestricted"></span></label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-hide-unknown-address" onchange="applyListFilters()" checked> Hide unknown address <span class="field-count" data-count="unknownAddress"></span></label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-hide-scams" onchange="applyListFilters()" checked> Hide scams <span class="field-count" data-count="scam"></span></label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-hide-duplicates" onchange="applyListFilters()" checked> Hide duplicates <span class="field-count" data-count="duplicates"></span></label>
          </div>
        </div>
        <div class="criteria-edit-actions">
          <button id="clear-list-filters-btn" class="ctrl-btn">Clear</button>
          <span id="list-filters-summary" class="criteria-edit-status"></span>
        </div>
      </div>

      <div id="columns-panel" class="criteria-edit tools-panel">
        <div style="margin-bottom:8px;">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12px;color:var(--subtext);">
            <span>Group by</span>
            <label class="checkbox-label grp-label" style="font-size:12px;"><input type="checkbox" data-grp="address" onchange="onGroupByToggle(this)"> Address<span class="grp-order"></span></label>
            <label class="checkbox-label grp-label" style="font-size:12px;"><input type="checkbox" data-grp="housing_type" onchange="onGroupByToggle(this)"> Type<span class="grp-order"></span></label>
            <label class="checkbox-label grp-label" style="font-size:12px;"><input type="checkbox" data-grp="source" onchange="onGroupByToggle(this)"> Source<span class="grp-order"></span></label>
            <label class="checkbox-label grp-label" style="font-size:12px;"><input type="checkbox" data-grp="flooring_type" onchange="onGroupByToggle(this)"> Flooring<span class="grp-order"></span></label>
            <button class="edit-icon-btn" onclick="clearGroupBy()" title="Clear all groupings" style="font-size:10px;padding:1px 5px;">&times;</button>
            <label class="checkbox-label" style="font-size:12px;margin-left:auto;">
              <input type="checkbox" id="groups-collapsed-cb" onchange="onGroupsCollapsedChange(this.checked)"> Collapsed
            </label>
          </div>
        </div>
        <div class="criteria-edit-grid columns-grid">
          <label><input type="checkbox" data-col="amenities" onchange="onColumnToggle(this)"> Amenities <span class="field-count" data-count="amenities"></span></label>
          <label><input type="checkbox" data-col="baths" onchange="onColumnToggle(this)"> Baths <span class="field-count" data-count="baths"></span></label>
          <label><input type="checkbox" data-col="beds" onchange="onColumnToggle(this)"> Beds <span class="field-count" data-count="beds"></span></label>
          <label><input type="checkbox" data-col="contact" onchange="onColumnToggle(this)"> Contact <span class="field-count" data-count="contact"></span></label>
          <label><input type="checkbox" data-col="distance" onchange="onColumnToggle(this)"> Distance <span class="field-count" data-count="distance"></span></label>
          <label><input type="checkbox" data-col="mine" onchange="onColumnToggle(this)"> Fav <span class="field-count" data-count="mine"></span></label>
          <label><input type="checkbox" data-col="flooring" onchange="onColumnToggle(this)"> Flooring <span class="field-count" data-count="flooring"></span></label>
          <label><input type="checkbox" data-col="kitchen" onchange="onColumnToggle(this)"> Kitchen <span class="field-count" data-count="kitchen"></span></label>
          <label><input type="checkbox" data-col="movein" onchange="onColumnToggle(this)"> Move-in <span class="field-count" data-count="movein"></span></label>
          <label><input type="checkbox" data-col="outdoor" onchange="onColumnToggle(this)"> Outdoor <span class="field-count" data-count="outdoor"></span></label>
          <label><input type="checkbox" data-col="photo" onchange="onColumnToggle(this)"> Photo <span class="field-count" data-count="photo"></span></label>
          <label><input type="checkbox" data-col="price" onchange="onColumnToggle(this)"> Price <span class="field-count" data-count="price"></span></label>
          <label><input type="checkbox" data-col="quality" onchange="onColumnToggle(this)"> Quality <span class="field-count" data-count="quality"></span></label>
          <label><input type="checkbox" data-col="risk" onchange="onColumnToggle(this)"> Risk <span class="field-count" data-count="risk"></span></label>
          <label><input type="checkbox" data-col="score" onchange="onColumnToggle(this)"> Rank <span class="field-count" data-count="score"></span></label>
          <label><input type="checkbox" data-col="size" onchange="onColumnToggle(this)"> Size feel <span class="field-count" data-count="size"></span></label>
          <label><input type="checkbox" data-col="source" onchange="onColumnToggle(this)"> Source <span class="field-count" data-count="source"></span></label>
          <label><input type="checkbox" data-col="sqft" onchange="onColumnToggle(this)"> Sqft <span class="field-count" data-count="sqft"></span></label>
          <label><input type="checkbox" data-col="type" onchange="onColumnToggle(this)"> Type <span class="field-count" data-count="type"></span></label>
        </div>
        <div class="criteria-edit-actions">
          <button id="reset-columns-btn" class="ctrl-btn">Show All</button>
        </div>
      </div>

      <div id="work-locations-panel" class="criteria-edit tools-panel">
        <div id="work-locations-list" class="work-locations-list"></div>
        <div class="criteria-edit-grid">
          <div>
            <label for="work-loc-name-input">Name</label>
            <input type="text" id="work-loc-name-input" placeholder="e.g. Cort Work">
          </div>
          <div>
            <label for="work-loc-address-input">Address</label>
            <input type="text" id="work-loc-address-input" placeholder="123 Main St, City, ST">
          </div>
          <div>
            <label for="work-loc-hours-start">Work starts</label>
            <input type="time" id="work-loc-hours-start" value="09:00">
          </div>
          <div>
            <label for="work-loc-hours-end">Work ends</label>
            <input type="time" id="work-loc-hours-end" value="17:00">
          </div>
          <div>
            <label for="work-loc-max-commute">Max commute (min)</label>
            <input type="number" id="work-loc-max-commute" min="0" step="5" placeholder="e.g. 30">
          </div>
        </div>
        <div class="criteria-edit-actions">
          <button id="add-work-location-btn" class="ctrl-btn">Add</button>
          <span id="work-location-status" class="criteria-edit-status"></span>
        </div>
      </div>

      <div id="ranking-panel" class="criteria-edit tools-panel">
        <div class="ranking-section-title">Targets</div>
        <div class="rank-weight-header">Value &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Importance</div>
        <div class="ranking-row">
          <label for="rank-target-price">Target price ($/mo)</label>
          <input type="number" id="rank-target-price" min="0" step="50" placeholder="e.g. 1200" oninput="applyScoringCriteria()">
          <input type="range" id="rank-w-price" min="0" max="10" value="5" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-price-val">5</span>
        </div>
        <div class="ranking-row">
          <label for="rank-max-commute">Max commute (mi)</label>
          <input type="number" id="rank-max-commute" min="0" step="1" placeholder="e.g. 15" oninput="applyScoringCriteria()">
          <input type="range" id="rank-w-commute" min="0" max="10" value="5" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-commute-val">5</span>
        </div>
        <div class="ranking-row">
          <label for="rank-target-beds">Bedrooms</label>
          <input type="number" id="rank-target-beds" min="0" step="1" placeholder="e.g. 2" oninput="applyScoringCriteria()">
          <input type="range" id="rank-w-beds" min="0" max="10" value="5" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-beds-val">5</span>
        </div>
        <div class="ranking-row">
          <label for="rank-target-baths">Bathrooms</label>
          <input type="number" id="rank-target-baths" min="0" step="0.5" placeholder="e.g. 1.5" oninput="applyScoringCriteria()">
          <input type="range" id="rank-w-baths" min="0" max="10" value="4" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-baths-val">4</span>
        </div>
        <div class="ranking-row">
          <label for="rank-target-sqft">Target sqft</label>
          <input type="number" id="rank-target-sqft" min="0" step="50" placeholder="e.g. 800" oninput="applyScoringCriteria()">
          <input type="range" id="rank-w-sqft" min="0" max="10" value="3" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-sqft-val">3</span>
        </div>
        <div class="ranking-row">
          <label for="rank-min-quality">Min quality</label>
          <select id="rank-min-quality" onchange="applyScoringCriteria()">
            <option value="">Any</option>
            <option value="2">2&#9733;</option>
            <option value="3">3&#9733;</option>
            <option value="4">4&#9733;</option>
            <option value="5">5&#9733;</option>
          </select>
          <input type="range" id="rank-w-quality" min="0" max="10" value="4" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-quality-val">4</span>
        </div>

        <div class="ranking-section-title">Preferences</div>
        <div class="ranking-row">
          <label for="rank-pref-flooring">Flooring</label>
          <select id="rank-pref-flooring" onchange="applyScoringCriteria()">
            <option value="">No preference</option>
            <option value="hardwood">Hardwood</option>
            <option value="tile">Tile</option>
            <option value="vinyl">Vinyl</option>
            <option value="carpet">Carpet</option>
          </select>
          <input type="range" id="rank-w-flooring" min="0" max="10" value="2" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-flooring-val">2</span>
        </div>
        <div class="ranking-row">
          <label for="rank-pref-kitchen">Kitchen</label>
          <select id="rank-pref-kitchen" onchange="applyScoringCriteria()">
            <option value="">No preference</option>
            <option value="modern">Modern</option>
            <option value="updated">Updated</option>
          </select>
          <input type="range" id="rank-w-kitchen" min="0" max="10" value="2" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-kitchen-val">2</span>
        </div>
        <div class="ranking-row">
          <label for="rank-pref-outdoor">Outdoor space</label>
          <select id="rank-pref-outdoor" onchange="applyScoringCriteria()">
            <option value="">No preference</option>
            <option value="yard">Yard</option>
            <option value="patio">Patio</option>
            <option value="balcony">Balcony</option>
          </select>
          <input type="range" id="rank-w-outdoor" min="0" max="10" value="2" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-outdoor-val">2</span>
        </div>

        <div class="ranking-section-title">Bonuses</div>
        <div class="ranking-row">
          <label>Low scam risk</label>
          <span style="flex:1"></span>
          <input type="range" id="rank-w-scam" min="0" max="10" value="3" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-scam-val">3</span>
        </div>
        <div class="ranking-row">
          <label>Washer/Dryer in unit</label>
          <span style="flex:1"></span>
          <input type="range" id="rank-w-wd" min="0" max="10" value="2" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-wd-val">2</span>
        </div>
        <div class="ranking-row">
          <label>Spacious feel</label>
          <span style="flex:1"></span>
          <input type="range" id="rank-w-spacious" min="0" max="10" value="1" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-spacious-val">1</span>
        </div>
        <div class="ranking-row">
          <label>Good natural light</label>
          <span style="flex:1"></span>
          <input type="range" id="rank-w-light" min="0" max="10" value="1" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-light-val">1</span>
        </div>
        <div class="ranking-row">
          <label>No age restriction (55+)</label>
          <span style="flex:1"></span>
          <input type="range" id="rank-w-noage" min="0" max="10" value="5" oninput="applyScoringCriteria()">
          <span class="rank-weight" id="rank-w-noage-val">5</span>
        </div>

        <div class="criteria-edit-actions" style="margin-top:10px;">
          <button id="clear-ranking-btn" class="ctrl-btn">Reset</button>
          <span id="ranking-summary" class="criteria-edit-status"></span>
        </div>
      </div>

      <div class="criteria-bar">
        <div class="criteria-text">
          <strong>Criteria:</strong> <span id="criteria-text-value">{criteria_text}</span>
          <button id="edit-criteria-btn" class="edit-icon-btn" title="Edit search criteria">&#9998; Edit</button>
        </div>
        <div id="criteria-edit" class="criteria-edit">
          <div class="criteria-edit-grid">
            <div class="full">
              <label for="address-input">Your address (distance &amp; map center)</label>
              <input type="text" id="address-input" placeholder="123 Main St, City, ST 12345">
            </div>
            <div class="full">
              <label for="lease-end-input">Lease end date</label>
              <input type="date" id="lease-end-input">
            </div>
            <div>
              <label for="radius-input">Radius (mi)</label>
              <input type="number" id="radius-input" min="0" step="0.5">
            </div>
            <div>
              <label for="min-sqft-input">Min sqft</label>
              <input type="number" id="min-sqft-input" min="0" step="50">
            </div>
            <div>
              <label for="min-price-input">Min price ($)</label>
              <input type="number" id="min-price-input" min="0" step="50">
            </div>
            <div>
              <label for="max-price-input">Max price ($)</label>
              <input type="number" id="max-price-input" min="0" step="50">
            </div>
            <div>
              <label for="min-beds-input">Min beds</label>
              <input type="number" id="min-beds-input" min="0" step="1">
            </div>
            <div>
              <label for="max-beds-input">Max beds</label>
              <input type="number" id="max-beds-input" min="0" step="1">
            </div>
            <div>
              <label for="min-baths-input">Min baths</label>
              <input type="number" id="min-baths-input" min="0" step="0.5">
            </div>
          </div>
          <div class="criteria-edit-actions">
            <button id="save-criteria-btn" class="ctrl-btn">Save</button>
            <button id="cancel-criteria-btn" class="ctrl-btn">Cancel</button>
            <button id="reset-criteria-btn" class="ctrl-btn" title="Clear saved overrides and reload config.json defaults">Reset</button>
            <span id="criteria-edit-status" class="criteria-edit-status"></span>
          </div>
        </div>
      </div>

      <div class="refresh-controls">
        <button id="reload-btn" class="ctrl-btn" title="Reload this page from disk">&#10227; Reload</button>
        <button id="howto-btn" class="ctrl-btn ctrl-btn-icon" title="How to update results">&#9432;</button>
      </div>
      <div id="howto-panel" class="howto-panel">
        <p>To pull fresh listings and rebuild this page, run in a terminal:</p>
        <code>python scripts/crawl_all.py</code>
        <code>python scripts/generate-map.py</code>
        <code>python scripts/generate-html.py</code>
        <p>Then click Reload above.</p>
      </div>
    </aside>

    <div class="resizer" id="resizer"></div>

    <div class="list-panel">
      <div id="linked-group-banner" style="display:none; align-items:center; gap:8px; padding:6px 12px; background:rgba(0,194,168,0.12); border-radius:6px; margin:4px 8px; font-size:13px;">
        <span></span>
        <button onclick="clearLinkedGroupFilter()" style="margin-left:auto; background:none; border:1px solid var(--border); color:var(--text); border-radius:4px; padding:2px 8px; cursor:pointer; font-size:12px;">Clear</button>
      </div>
      <table class="units-table">
        <thead>
          <tr>
            <th class="mine-col sortable" data-sort="favorite" onclick="applySort('favorite')"></th>
            <th class="photo-col">Photo</th>
            <th class="distance-col sortable" data-sort="distance_miles" onclick="applySort('distance_miles')">Distance</th>
            <th class="address-col sortable" data-sort="address" onclick="applySort('address')">Address</th>
            <th class="price-col sortable" data-sort="price" onclick="applySort('price')">Price/mo</th>
            <th class="spec-col beds-col sortable" data-sort="beds" onclick="applySort('beds')">Beds</th>
            <th class="spec-col baths-col sortable" data-sort="baths" onclick="applySort('baths')">Baths</th>
            <th class="spec-col sqft-col sortable" data-sort="sqft" onclick="applySort('sqft')">Sqft</th>
            <th class="type-col sortable" data-sort="housing_type" onclick="applySort('housing_type')">Type</th>
            <th class="movein-col sortable" data-sort="move_in_sort" onclick="applySort('move_in_sort')">Move-in</th>
            <th class="amenity-col">Amenities</th>
            <th class="flooring-col sortable" data-sort="flooring_type" onclick="applySort('flooring_type')">Flooring</th>
            <th class="source-col sortable" data-sort="source" onclick="applySort('source')">Source</th>
            <th class="quality-col sortable" data-sort="quality_rating" onclick="applySort('quality_rating')">Quality</th>
            <th class="risk-col sortable" data-sort="scam_score" onclick="applySort('scam_score')">Risk</th>
            <th class="kitchen-col sortable" data-sort="kitchen_style" onclick="applySort('kitchen_style')">Kitchen</th>
            <th class="outdoor-col sortable" data-sort="outdoor_space" onclick="applySort('outdoor_space')">Outdoor</th>
            <th class="size-col sortable" data-sort="size_impression" onclick="applySort('size_impression')">Size feel</th>
            <th class="score-col sortable" data-sort="overall_score" onclick="applySort('overall_score')">Rank</th>
            <th class="contact-col sortable" data-sort="contact_phone" onclick="applySort('contact_phone')">Contact</th>
            <th id="work-anchor-th" style="display:none"></th>
          </tr>
        </thead>
        <tbody id="units-tbody"></tbody>
      </table>
      <div id="empty-state" class="empty-state" style="display:none;"></div>
    </div>
  </div>

  <div id="lightbox-overlay" class="lightbox-overlay" onclick="closeLightbox(event)">
    <button class="lightbox-nav lightbox-prev" onclick="lightboxPrev(event)">&#8249;</button>
    <img id="lightbox-img" src="" alt="Full-size photo">
    <button class="lightbox-nav lightbox-next" onclick="lightboxNext(event)">&#8250;</button>
    <div class="lightbox-counter" id="lightbox-counter"></div>
  </div>

  <div id="notes-modal-overlay" class="notes-modal-overlay" onclick="closeNotesModal(event)">
    <div class="notes-modal">
      <h3>Your Notes</h3>
      <textarea id="notes-modal-textarea" placeholder="Add private notes about this unit..."></textarea>
      <div class="notes-modal-actions">
        <button class="ctrl-btn" onclick="closeNotesModal()">Cancel</button>
        <button class="ctrl-btn primary" onclick="saveNotes()">Save</button>
      </div>
    </div>
  </div>

  <div id="timeline-modal-overlay" class="notes-modal-overlay" onclick="closeTimelineModal(event)">
    <div class="notes-modal timeline-modal">
      <h3>Interaction Timeline</h3>
      <div id="timeline-entries" class="timeline-entries"></div>
      <div class="timeline-add-row">
        <input type="date" id="timeline-add-date">
        <input type="text" id="timeline-add-text" placeholder="e.g. Called landlord, toured unit...">
      </div>
      <div class="notes-modal-actions">
        <button class="ctrl-btn" onclick="closeTimelineModal()">Close</button>
        <button class="ctrl-btn primary" onclick="addTimelineEntry()">Add Entry</button>
      </div>
    </div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
  <script>
    const CONFIG = {config_json};
    const INITIAL_DATA = {initial_data_json};

    // ---- Load any saved criteria overrides (address, lease end, radius, price, beds, baths, sqft) ----
    const OVERRIDE_KEYS = ['target_location', 'target_name', 'current_lease_end', 'search_radius_miles',
      'min_price', 'max_price', 'min_beds', 'max_beds', 'min_baths', 'min_sqft'];

    function toggleMobileSidebar() {{
      const sidebar = document.getElementById('sidebar');
      const btn = document.getElementById('mobile-sidebar-toggle');
      sidebar.classList.toggle('mobile-collapsed');
      btn.classList.toggle('collapsed');
      const isCollapsed = sidebar.classList.contains('mobile-collapsed');
      btn.innerHTML = `<span class="toggle-arrow">▼</span> ${{isCollapsed ? 'Show' : 'Hide'}} Filters &amp; Map`;
      if (!isCollapsed && typeof map !== 'undefined' && map) {{
        setTimeout(() => map.invalidateSize(), 100);
      }}
    }}

    (function initMobileSidebar() {{
      if (window.innerWidth <= 860) {{
        const sidebar = document.getElementById('sidebar');
        const btn = document.getElementById('mobile-sidebar-toggle');
        if (sidebar && btn) {{
          sidebar.classList.add('mobile-collapsed');
          btn.classList.add('collapsed');
          btn.innerHTML = '<span class="toggle-arrow">▼</span> Show Filters &amp; Map';
        }}
      }}
    }})();

    function saveOverrides() {{
      const overrides = {{}};
      OVERRIDE_KEYS.forEach(k => {{ overrides[k] = CONFIG[k]; }});
      try {{ localStorage.setItem('configOverrides', JSON.stringify(overrides)); }} catch (e) {{}}
    }}

    try {{
      const saved = JSON.parse(localStorage.getItem('configOverrides') || 'null');
      if (saved) {{
        OVERRIDE_KEYS.forEach(k => {{
          if (Object.prototype.hasOwnProperty.call(saved, k)) CONFIG[k] = saved[k];
        }});
      }}
    }} catch (e) {{ /* localStorage unavailable */ }}

    // ---- Map setup ----
    let targetMarker = null;
    let radiusCircle = null;
    const defaultCenter = [27.9468822, -82.725352];
    const center = CONFIG.target_location ? [CONFIG.target_location.lat, CONFIG.target_location.lon] : defaultCenter;
    const map = L.map('map', {{ zoomSnap: 0.5, zoomDelta: 0.5 }}).setView(center, 13);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);
    // Default maxClusterRadius (80px) groups units that are several blocks
    // apart into one blob - shrink it so only markers that would visually
    // overlap (e.g. multiple floorplans at the same address) get clustered
    const markers = L.markerClusterGroup({{ maxClusterRadius: 20 }});
    map.addLayer(markers);
    const workLocationMarkers = L.layerGroup();
    map.addLayer(workLocationMarkers);
    // Maps unit id -> its Leaflet marker, so list rows can highlight the
    // matching dot on hover
    let unitMarkers = {{}};

    // Leaflet caches the container size at init time, before the flex layout
    // has settled into its final dimensions - without this, tiles outside
    // that initial (often too-small) viewport never render until something
    // (e.g. a zoom change) forces Leaflet to recompute its size.
    const mapEl = document.getElementById('map');
    if (window.ResizeObserver) {{
      new ResizeObserver(() => map.invalidateSize()).observe(mapEl);
    }}
    window.addEventListener('load', () => map.invalidateSize());
    requestAnimationFrame(() => map.invalidateSize());

    // (Re)draws the search-center marker and radius circle for CONFIG.target_location
    function updateMapCenter() {{
      const t = CONFIG.target_location;
      if (!t) return;
      map.setView([t.lat, t.lon], map.getZoom());
      if (targetMarker) map.removeLayer(targetMarker);
      targetMarker = L.marker([t.lat, t.lon]).addTo(map)
        .bindPopup(`<b>${{escapeHtml(t.name || 'Search center')}}</b><br>${{escapeHtml(t.address || '')}}`);
      if (radiusCircle) map.removeLayer(radiusCircle);
      if (CONFIG.search_radius_miles) {{
        radiusCircle = L.circle([t.lat, t.lon], {{
          radius: CONFIG.search_radius_miles * 1609.34,
          color: '#4fd1c5',
          weight: 1,
          fillOpacity: 0.05
        }}).addTo(map);
      }}
    }}
    updateMapCenter();

    // ---- Helpers ----
    function haversineMiles(lat1, lon1, lat2, lon2) {{
      const R = 3959;
      const toRad = d => d * Math.PI / 180;
      const dLat = toRad(lat2 - lat1);
      const dLon = toRad(lon2 - lon1);
      const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
      return R * 2 * Math.asin(Math.sqrt(a));
    }}

    // ---- Driving distance via OSRM ----
    let drivingDistCache = {{}};
    let drivingDistLoading = false;

    async function fetchDrivingDistances() {{
      if (drivingDistLoading || !workLocations.length) return;
      const validUnits = (lastData.units || []).filter(u => u.lat != null && u.lon != null);
      if (!validUnits.length) return;

      const workCoords = workLocations.filter(w => w.lat != null && w.lon != null);
      if (!workCoords.length) return;

      drivingDistLoading = true;
      const batchSize = Math.max(1, 95 - workCoords.length);

      for (let start = 0; start < validUnits.length; start += batchSize) {{
        const batch = validUnits.slice(start, start + batchSize);
        const allCoords = [...workCoords.map(w => `${{w.lon}},${{w.lat}}`), ...batch.map(u => `${{u.lon}},${{u.lat}}`)];
        const sources = workCoords.map((_, i) => i).join(';');
        const destStart = workCoords.length;
        const destinations = batch.map((_, i) => destStart + i).join(';');

        try {{
          const url = `https://router.project-osrm.org/table/v1/driving/${{allCoords.join(';')}}?sources=${{sources}}&destinations=${{destinations}}&annotations=distance,duration`;
          const resp = await fetch(url);
          const data = await resp.json();

          if (data.code === 'Ok') {{
            batch.forEach((unit, ui) => {{
              if (!drivingDistCache[unit.id]) drivingDistCache[unit.id] = {{}};
              workCoords.forEach((wLoc, wi) => {{
                const dist = data.distances[wi][ui];
                const dur = data.durations[wi][ui];
                if (dist != null && dur != null) {{
                  drivingDistCache[unit.id][wi] = {{
                    distance_mi: Math.round(dist / 1609.344 * 10) / 10,
                    duration_min: Math.round(dur / 60)
                  }};
                }}
              }});
            }});
          }}
        }} catch (e) {{
          console.warn('OSRM table API error:', e);
        }}

        if (start + batchSize < validUnits.length) {{
          await new Promise(r => setTimeout(r, 300));
        }}
      }}

      drivingDistLoading = false;
      renderAll(lastData);
    }}

    function photoPath(p) {{
      return p.replace(/^outputs\\//, '');
    }}

    function dashOr(v, def) {{
      def = (def === undefined) ? '\\u2014' : def;
      return (v === null || v === undefined) ? def : v;
    }}

    function formatNumber(n) {{
      if (n == null || n === '' || isNaN(n)) return n;
      return Number(n).toLocaleString('en-US');
    }}

    // Map marker color by housing type - keep in sync with the .map-marker.* CSS rules
    function markerClassForType(housingType) {{
      const type = (housingType || '').toLowerCase();
      if (type === 'apartment') return 'marker-apartment';
      if (type === 'house') return 'marker-house';
      return 'marker-other';
    }}

    function markerIcon(housingType) {{
      return L.divIcon({{
        className: `map-marker ${{markerClassForType(housingType)}}`,
        iconSize: [14, 14]
      }});
    }}

    function workLocationIcon() {{
      return L.divIcon({{
        className: 'map-marker-work',
        html: '\\ud83d\\udcbc',
        iconSize: [22, 22]
      }});
    }}

    function escapeHtml(s) {{
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}

    // ---- Personal favorites & notes (stored locally per-browser) ----
    function loadUnitOverrides() {{
      try {{ return JSON.parse(localStorage.getItem('unitOverrides') || '{{}}'); }} catch (e) {{ return {{}}; }}
    }}

    let unitOverrides = loadUnitOverrides();

    function saveUnitOverrides() {{
      try {{ localStorage.setItem('unitOverrides', JSON.stringify(unitOverrides)); }} catch (e) {{}}
    }}

    function isFavorite(id) {{
      return !!(unitOverrides[id] && unitOverrides[id].favorite);
    }}

    function isScam(id) {{
      return !!(unitOverrides[id] && unitOverrides[id].scam);
    }}

    function toggleScam(id) {{
      const entry = unitOverrides[id] || {{}};
      entry.scam = !entry.scam;
      unitOverrides[id] = entry;
      saveUnitOverrides();
      renderAll(lastData);
    }}

    function getUnitNotes(id) {{
      return (unitOverrides[id] && unitOverrides[id].notes) || '';
    }}

    function getUnitTimeline(id) {{
      return (unitOverrides[id] && unitOverrides[id].timeline) || [];
    }}

    function toggleFavorite(id) {{
      const entry = unitOverrides[id] || {{}};
      entry.favorite = !entry.favorite;
      unitOverrides[id] = entry;
      saveUnitOverrides();
      renderAll(lastData);
    }}

    let notesModalUnitId = null;

    function openNotesModal(id) {{
      notesModalUnitId = id;
      document.getElementById('notes-modal-textarea').value = getUnitNotes(id);
      document.getElementById('notes-modal-overlay').style.display = 'flex';
    }}

    function closeNotesModal(e) {{
      if (e && e.target.id !== 'notes-modal-overlay') return;
      document.getElementById('notes-modal-overlay').style.display = 'none';
      notesModalUnitId = null;
    }}

    function saveNotes() {{
      if (!notesModalUnitId) return;
      const entry = unitOverrides[notesModalUnitId] || {{}};
      entry.notes = document.getElementById('notes-modal-textarea').value;
      unitOverrides[notesModalUnitId] = entry;
      saveUnitOverrides();
      closeNotesModal();
      renderAll(lastData);
    }}

    let timelineModalUnitId = null;

    function formatTimelineDate(dateStr) {{
      const d = new Date(dateStr + 'T00:00:00');
      if (isNaN(d.getTime())) return dateStr;
      return d.toLocaleDateString(undefined, {{ year: 'numeric', month: 'short', day: 'numeric' }});
    }}

    function renderTimelineEntries() {{
      const list = document.getElementById('timeline-entries');
      const entries = getUnitTimeline(timelineModalUnitId);
      if (!entries.length) {{
        list.innerHTML = '<div class="timeline-empty">No interactions logged yet.</div>';
        return;
      }}
      const sorted = entries.map((e, i) => ({{ ...e, i }})).sort((a, b) => b.date.localeCompare(a.date));
      list.innerHTML = sorted.map(entry => `<div class="timeline-entry">` +
        `<div class="timeline-entry-date">${{formatTimelineDate(entry.date)}}</div>` +
        `<div class="timeline-entry-text">${{escapeHtml(entry.text)}}</div>` +
        `<button class="timeline-entry-remove" title="Remove" onclick="removeTimelineEntry(${{entry.i}})">\\u2715</button>` +
      `</div>`).join('');
    }}

    function openTimelineModal(id) {{
      timelineModalUnitId = id;
      document.getElementById('timeline-add-date').value = new Date().toISOString().slice(0, 10);
      document.getElementById('timeline-add-text').value = '';
      renderTimelineEntries();
      document.getElementById('timeline-modal-overlay').style.display = 'flex';
    }}

    function closeTimelineModal(e) {{
      if (e && e.target.id !== 'timeline-modal-overlay') return;
      document.getElementById('timeline-modal-overlay').style.display = 'none';
      timelineModalUnitId = null;
    }}

    function addTimelineEntry() {{
      if (!timelineModalUnitId) return;
      const date = document.getElementById('timeline-add-date').value;
      const text = document.getElementById('timeline-add-text').value.trim();
      if (!date || !text) return;
      const entry = unitOverrides[timelineModalUnitId] || {{}};
      const timeline = entry.timeline || [];
      timeline.push({{ date, text }});
      entry.timeline = timeline;
      unitOverrides[timelineModalUnitId] = entry;
      saveUnitOverrides();
      document.getElementById('timeline-add-text').value = '';
      renderTimelineEntries();
      renderAll(lastData);
    }}

    function removeTimelineEntry(index) {{
      if (!timelineModalUnitId) return;
      const entry = unitOverrides[timelineModalUnitId] || {{}};
      const timeline = entry.timeline || [];
      timeline.splice(index, 1);
      entry.timeline = timeline;
      unitOverrides[timelineModalUnitId] = entry;
      saveUnitOverrides();
      renderTimelineEntries();
      renderAll(lastData);
    }}

    const COPY_SVG = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5.5" y="5.5" width="9" height="9" rx="1.5"/><path d="M3 10.5H2.5A1.5 1.5 0 011 9V2.5A1.5 1.5 0 012.5 1H9a1.5 1.5 0 011.5 1.5V3"/></svg>';
    const CHECK_SVG = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8.5L6.5 12L13 4"/></svg>';

    function copyToClipboard(text, btnEl) {{
      navigator.clipboard.writeText(text).then(() => {{
        btnEl.classList.add('copied');
        btnEl.innerHTML = CHECK_SVG;
        setTimeout(() => {{ btnEl.classList.remove('copied'); btnEl.innerHTML = COPY_SVG; }}, 1200);
      }});
    }}

    function contactCellHtml(u) {{
      const phone = u.contact_phone;
      const email = u.contact_email;
      if (!phone && !email) return '<span title="No contact info">\\u2014</span>';
      let html = '<span class="contact-icons">';
      if (phone) {{
        const safePhone = escapeHtml(phone);
        const encodedPhone = encodeURIComponent(phone);
        html += `<span class="contact-icon-wrap">`;
        html += `<a href="tel:${{encodedPhone}}" class="contact-icon">\\u260E</a>`;
        html += `<span class="contact-tip">${{safePhone}} <span class="contact-tip-copy" onclick="event.stopPropagation();copyToClipboard('${{safePhone}}',this)">${{COPY_SVG}}</span></span>`;
        html += `</span>`;
        html += `<span class="contact-icon-wrap">`;
        html += `<a href="sms:${{encodedPhone}}" class="contact-icon">&#128172;</a>`;
        html += `<span class="contact-tip">Text ${{safePhone}} <span class="contact-tip-copy" onclick="event.stopPropagation();copyToClipboard('${{safePhone}}',this)">${{COPY_SVG}}</span></span>`;
        html += `</span>`;
      }}
      if (email) {{
        const safeEmail = escapeHtml(email);
        html += `<span class="contact-icon-wrap">`;
        html += `<a href="mailto:${{safeEmail}}" class="contact-icon">\\u2709</a>`;
        html += `<span class="contact-tip">${{safeEmail}} <span class="contact-tip-copy" onclick="event.stopPropagation();copyToClipboard('${{safeEmail}}',this)">${{COPY_SVG}}</span></span>`;
        html += `</span>`;
      }}
      html += '</span>';
      return html;
    }}

    // Renders a 1-5 quality rating from the AI photo scan as a number, or a dash if unrated
    function qualityStarsHtml(u) {{
      const rating = u.quality_rating;
      if (rating == null) return '<span title="Not yet scanned">\\u2014</span>';
      const notes = u.quality_notes ? escapeHtml(u.quality_notes) : `${{rating}}/5`;
      return `<span title="${{notes}}">${{rating.toFixed(1)}} \\u2605</span>`;
    }}

    function riskCellHtml(u) {{
      const score = u.scam_score;
      const level = u.scam_level;
      if (score == null) return '<span title="Not analyzed">\\u2014</span>';
      const colors = {{ low: '#48bb78', moderate: '#ecc94b', high: '#fc8181', very_high: '#e53e3e' }};
      const labels = {{ low: 'Low', moderate: 'Med', high: 'High', very_high: 'V.High' }};
      const color = colors[level] || '#8892a4';
      const label = labels[level] || level;
      return `<span class="risk-dot" style="color:${{color}}" title="Scam risk score: ${{score}}">`
        + `\\u25CF ${{label}}</span>`;
    }}

    // Subtle value-based tinting for numeric and categorical cells
    let _tintRanges = {{ priceMin: 0, priceMax: 1, distMin: 0, distMax: 1 }};

    function computeTintRanges(units) {{
      const prices = units.map(u => Number(u.price)).filter(p => p > 0);
      const dists = units.map(u => u.distance_miles).filter(d => d != null);
      _tintRanges = {{
        priceMin: prices.length ? Math.min(...prices) : 0,
        priceMax: prices.length ? Math.max(...prices) : 1,
        distMin: dists.length ? Math.min(...dists) : 0,
        distMax: dists.length ? Math.max(...dists) : 1,
      }};
    }}

    function numericTint(value, min, max, invert) {{
      if (value == null || min === max) return '';
      const t = Math.max(0, Math.min(1, (value - min) / (max - min)));
      const ratio = invert ? 1 - t : t;
      const r = Math.round(104 + (252 - 104) * (1 - ratio));
      const g = Math.round(211 + (129 - 211) * (1 - ratio));
      const b = Math.round(197 + (129 - 197) * (1 - ratio));
      return `color: rgb(${{r}}, ${{g}}, ${{b}})`;
    }}

    const FLOORING_COLORS = {{
      hardwood:  'color: #d4a76a',
      wood:      'color: #d4a76a',
      carpet:    'color: #b0a0c8',
      tile:      'color: #7ec8c8',
      laminate:  'color: #b8b070',
      vinyl:     'color: #90b0d0',
      concrete:  'color: #9a9a9a',
      mixed:     'color: #b0a890',
    }};

    const TYPE_COLORS = {{
      apartment: 'color: #9ab8b4',
      condo:     'color: #9ab4c8',
      house:     'color: #c8b498',
      townhouse: 'color: #b8aec8',
      duplex:    'color: #c8a0a0',
      studio:    'color: #98c0a8',
    }};

    // Renders the AI-detected primary flooring type, or a dash if unscanned/unknown
    function flooringHtml(u) {{
      const flooring = u.flooring_type;
      if (!flooring || flooring === 'unknown') return '<span title="Not detected">\\u2014</span>';
      return escapeHtml(flooring.charAt(0).toUpperCase() + flooring.slice(1));
    }}

    // Renders the move-in/availability date: "Now", a formatted date, or a dash if not listed
    function moveInHtml(u) {{
      const moveIn = u.move_in_date;
      if (!moveIn) return '<span title="Not listed">\\u2014</span>';
      if (moveIn === 'now') return 'Now';
      const d = new Date(moveIn + 'T00:00:00');
      if (isNaN(d.getTime())) return escapeHtml(moveIn);
      return d.toLocaleDateString('en-US', {{ month: 'short', day: 'numeric', year: 'numeric' }});
    }}

    // Units sharing an address (e.g. multiple floorplans/listings at the same
    // apartment complex) are otherwise indistinguishable in the table and map
    function findDuplicateAddresses(units) {{
      const counts = {{}};
      units.forEach(u => {{
        if (!u.address) return;
        counts[u.address] = (counts[u.address] || 0) + 1;
      }});
      return new Set(Object.keys(counts).filter(a => counts[a] > 1));
    }}

    // Reorders units so multiple listings at the same address (e.g. different
    // floorplans in one building) sit next to each other in the list,
    // regardless of the active sort - each group's position is determined by
    // where its first unit falls in the sorted order
    function groupByAddress(units) {{ return groupByField(units, 'address'); }}

    let currentGroupBy = (() => {{
      try {{
        const stored = localStorage.getItem('groupByFields');
        if (stored) return JSON.parse(stored);
      }} catch(e) {{}}
      return ['address'];
    }})();

    let groupsCollapsedByDefault = (() => {{
      try {{ return localStorage.getItem('groupsCollapsed') === 'true'; }} catch(e) {{ return false; }}
    }})();

    function onGroupByToggle(changedCb) {{
      if (changedCb && changedCb.checked) {{
        currentGroupBy.push(changedCb.dataset.grp);
      }} else if (changedCb) {{
        currentGroupBy = currentGroupBy.filter(f => f !== changedCb.dataset.grp);
      }}
      try {{ localStorage.setItem('groupByFields', JSON.stringify(currentGroupBy)); }} catch(e) {{}}
      updateGroupByOrderBadges();
      renderAll(lastData);
    }}

    function updateGroupByOrderBadges() {{
      document.querySelectorAll('[data-grp]').forEach(cb => {{
        const badge = cb.parentElement.querySelector('.grp-order');
        if (!badge) return;
        const idx = currentGroupBy.indexOf(cb.dataset.grp);
        if (idx >= 0 && currentGroupBy.length > 1) {{
          badge.textContent = idx + 1;
        }} else {{
          badge.textContent = '';
        }}
      }});
    }}

    function restoreGroupByCheckboxes() {{
      document.querySelectorAll('[data-grp]').forEach(cb => {{
        cb.checked = currentGroupBy.includes(cb.dataset.grp);
      }});
      updateGroupByOrderBadges();
    }}

    function clearGroupBy() {{
      currentGroupBy = [];
      try {{ localStorage.setItem('groupByFields', '[]'); }} catch(e) {{}}
      restoreGroupByCheckboxes();
      renderAll(lastData);
    }}

    function onGroupsCollapsedChange(checked) {{
      groupsCollapsedByDefault = checked;
      try {{ localStorage.setItem('groupsCollapsed', checked); }} catch(e) {{}}
      renderAll(lastData);
    }}

    function groupByFields(units, fields) {{
      if (!fields || fields.length === 0) return units;
      const groups = new Map();
      const order = [];
      units.forEach(u => {{
        const parts = fields.map(f => String(u[f] || ''));
        const key = parts.every(p => p === '') ? `__unit_${{u.id}}` : parts.join('\\x00');
        if (!groups.has(key)) {{ groups.set(key, []); order.push(key); }}
        groups.get(key).push(u);
      }});
      const out = [];
      order.forEach(key => out.push(...groups.get(key)));
      return out;
    }}

    function groupByField(units, field) {{
      if (Array.isArray(field)) return groupByFields(units, field);
      if (!field) return units;
      return groupByFields(units, [field]);
    }}

    // ---- Filtering (mirrors filter_units_by_distance in generate-html.py) ----
    let currentMinQuality = CONFIG.min_quality;
    let currentFavoritesOnly = false;
    let currentTypeFilter = '';
    let lastData = INITIAL_DATA;

    // List filters narrow what's displayed without changing the search
    // criteria (CONFIG / configOverrides), which represents what gets fetched
    let listFilters = {{
      maxDistance: null,
      minPrice: null,
      maxPrice: null,
      minBeds: null,
      minBaths: null,
      minSqft: null,
      flooring: null,
      availableBy: null,
      kitchenStyle: null,
      outdoorSpace: null,
      sizeImpression: null,
      hideScams: true,
      hasContact: false,
      washerDryer: false,
      gated: false,
      hideAgeRestricted: true,
      hideUnknownAddress: true,
      hideDuplicates: true,
      workMaxDistance: [],
      linkedGroupIds: null,
    }};

    // Free-text search (top-left of sidebar) - narrows the list/map by
    // substring match against a blob of each unit's text fields
    let searchFilters = {{
      contains: '',
      excludes: '',
    }};

    function searchableText(u) {{
      const parts = [u.title, u.address, u.notes, u.housing_type, u.source, u.flooring_type, u.move_in_date]
        .concat(u.amenities || [])
        .filter(Boolean);
      if (u.age_restriction) parts.push(`${{u.age_restriction}}+`);
      return parts.join(' ').toLowerCase();
    }}

    function applySearchFilters() {{
      searchFilters.contains = document.getElementById('search-contains').value.trim();
      searchFilters.excludes = document.getElementById('search-excludes').value.trim();
      saveListFilters();
      renderAll(lastData);
    }}

    function filterUnits(units) {{
      const target = CONFIG.target_location;
      const radius = CONFIG.search_radius_miles;
      const out = [];
      units.forEach(raw => {{
        const u = Object.assign({{}}, raw);
        u.favorite = isFavorite(u.id);
        u.scam = isScam(u.id);
        u.notes_text = getUnitNotes(u.id);
        u.timeline = getUnitTimeline(u.id);
        if (currentFavoritesOnly && !u.favorite) return;
        if (listFilters.hideScams && u.scam) return;

        let distance = null;
        if (target && u.lat != null && u.lon != null) {{
          distance = haversineMiles(target.lat, target.lon, u.lat, u.lon);
          u.distance_miles = Math.round(distance * 10) / 10;
        }}
        if (radius != null && distance != null && distance > radius) return;

        workLocations.forEach((loc, i) => {{
          u['work_dist_' + i] = (u.lat != null && u.lon != null)
            ? Math.round(haversineMiles(loc.lat, loc.lon, u.lat, u.lon) * 10) / 10
            : null;
          const cached = drivingDistCache[u.id] && drivingDistCache[u.id][i];
          u['work_drive_dist_' + i] = cached ? cached.distance_mi : null;
          u['work_drive_min_' + i] = cached ? cached.duration_min : null;
        }});
        // Commute score: average of all work-location driving distances (falls back to straight-line)
        if (workLocations.length && u.lat != null && u.lon != null) {{
          const dists = workLocations.map((loc, i) => {{
            const cached = drivingDistCache[u.id] && drivingDistCache[u.id][i];
            return cached ? cached.distance_mi : haversineMiles(loc.lat, loc.lon, u.lat, u.lon);
          }});
          u.commute_score = Math.round(dists.reduce((a, b) => a + b, 0) / dists.length * 10) / 10;
        }} else {{
          u.commute_score = null;
        }}

        const beds = Number(u.beds || 0);
        if (CONFIG.min_beds != null && beds < CONFIG.min_beds) return;

        const baths = Number(u.baths || 0);
        if (CONFIG.min_baths != null && baths < CONFIG.min_baths) return;

        if (CONFIG.min_sqft != null && u.sqft) {{
          if (Number(u.sqft || 0) < CONFIG.min_sqft) return;
        }}

        const price = Number(u.price || 0);
        if (CONFIG.min_price != null && price < CONFIG.min_price) return;
        if (CONFIG.max_price != null && price > CONFIG.max_price) return;

        if (currentMinQuality != null) {{
          if (u.quality_rating == null || u.quality_rating < currentMinQuality) return;
        }}

        // ---- List filters (narrow the displayed list only - doesn't affect criteria) ----
        if (listFilters.maxDistance != null) {{
          if (distance == null || distance > listFilters.maxDistance) return;
        }}
        if (listFilters.minPrice != null && price < listFilters.minPrice) return;
        if (listFilters.maxPrice != null && price > listFilters.maxPrice) return;
        if (listFilters.minBeds != null && beds < listFilters.minBeds) return;
        if (listFilters.minBaths != null && baths < listFilters.minBaths) return;
        if (listFilters.minSqft != null) {{
          if (!u.sqft || Number(u.sqft) < listFilters.minSqft) return;
        }}
        if (listFilters.flooring) {{
          if ((u.flooring_type || 'unknown') !== listFilters.flooring) return;
        }}
        if (listFilters.availableBy) {{
          if (!u.move_in_date) return;
          if (u.move_in_date !== 'now' && u.move_in_date > listFilters.availableBy) return;
        }}

        if (listFilters.kitchenStyle && (u.kitchen_style || 'unknown') !== listFilters.kitchenStyle) return;
        if (listFilters.outdoorSpace && (u.outdoor_space || 'unknown') !== listFilters.outdoorSpace) return;
        if (listFilters.sizeImpression && (u.size_impression || 'unknown') !== listFilters.sizeImpression) return;

        if (listFilters.hasContact && !u.contact_phone && !u.contact_email) return;
        if (listFilters.washerDryer && !u.has_washer_dryer) return;
        if (listFilters.gated && !u.is_gated) return;
        if (listFilters.hideAgeRestricted && u.age_restriction) return;
        if (listFilters.linkedGroupIds && !listFilters.linkedGroupIds.has(u.id)) return;
        if (!listFilters.linkedGroupIds && listFilters.hideDuplicates && u.linked_ids && u.linked_ids.length && u.linked_primary && u.linked_primary !== u.id) return;
        if (listFilters.hideUnknownAddress && !u.address) return;
        if (workLocations.some((loc, i) => {{
          const maxD = listFilters.workMaxDistance[i];
          if (maxD != null) {{
            const d = u['work_dist_' + i];
            if (d == null || d > maxD) return true;
          }}
          const maxT = (listFilters.workMaxCommute || [])[i];
          if (maxT != null) {{
            const t = u['work_drive_min_' + i];
            if (t == null || t > maxT) return true;
          }}
          return false;
        }})) return;

        // ---- Free-text search ----
        if (searchFilters.contains || searchFilters.excludes) {{
          const text = searchableText(u);
          if (searchFilters.contains) {{
            const terms = searchFilters.contains.toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
            if (terms.length && !terms.some(t => text.includes(t))) return;
          }}
          if (searchFilters.excludes) {{
            const terms = searchFilters.excludes.toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
            if (terms.some(t => text.includes(t))) return;
          }}
        }}

        u.overall_score = computeScore(u);
        out.push(u);
      }});
      return out;
    }}

    function applyQualityFilter() {{
      const val = document.getElementById('quality-select').value;
      currentMinQuality = val ? Number(val) : null;
      renderAll(lastData);
    }}

    function applyFavoritesFilter() {{
      currentFavoritesOnly = document.getElementById('favorites-select').value === '1';
      renderAll(lastData);
    }}

    function filterToLinkedGroup(ids) {{
      listFilters.linkedGroupIds = new Set(ids);
      const banner = document.getElementById('linked-group-banner');
      banner.style.display = 'flex';
      banner.querySelector('span').textContent = `Showing ${{ids.length}} linked listings`;
      renderAll(lastData);
    }}

    function clearLinkedGroupFilter() {{
      listFilters.linkedGroupIds = null;
      document.getElementById('linked-group-banner').style.display = 'none';
      renderAll(lastData);
    }}

    function applyListFilters() {{
      listFilters.maxDistance = numOrNull(document.getElementById('lf-max-distance').value);
      listFilters.minPrice = numOrNull(document.getElementById('lf-min-price').value);
      listFilters.maxPrice = numOrNull(document.getElementById('lf-max-price').value);
      listFilters.minBeds = numOrNull(document.getElementById('lf-min-beds').value);
      listFilters.minBaths = numOrNull(document.getElementById('lf-min-baths').value);
      listFilters.minSqft = numOrNull(document.getElementById('lf-min-sqft').value);
      listFilters.flooring = document.getElementById('lf-flooring').value || null;
      listFilters.availableBy = document.getElementById('lf-available-by').value || null;

      listFilters.kitchenStyle = document.getElementById('lf-kitchen-style').value || null;
      listFilters.outdoorSpace = document.getElementById('lf-outdoor-space').value || null;
      listFilters.sizeImpression = document.getElementById('lf-size-impression').value || null;

      listFilters.hideScams = document.getElementById('lf-hide-scams').checked;
      listFilters.hasContact = document.getElementById('lf-has-contact').checked;
      listFilters.washerDryer = document.getElementById('lf-washer-dryer').checked;
      listFilters.gated = document.getElementById('lf-gated').checked;
      listFilters.hideAgeRestricted = document.getElementById('lf-hide-age-restricted').checked;
      listFilters.hideUnknownAddress = document.getElementById('lf-hide-unknown-address').checked;
      listFilters.hideDuplicates = document.getElementById('lf-hide-duplicates').checked;
      listFilters.workMaxDistance = workLocations.map((loc, i) => numOrNull(document.getElementById(`lf-work-distance-${{i}}`).value));
      listFilters.workMaxCommute = workLocations.map((loc, i) => numOrNull(document.getElementById(`lf-work-commute-${{i}}`).value));
      saveListFilters();
      renderListFiltersSummary();
      renderAll(lastData);
    }}

    function clearListFilters() {{
      ['lf-max-distance', 'lf-min-price', 'lf-max-price', 'lf-min-beds', 'lf-min-baths', 'lf-min-sqft',
       'lf-flooring', 'lf-available-by', 'lf-kitchen-style',
       'lf-outdoor-space', 'lf-size-impression'].forEach(id => {{
        document.getElementById(id).value = '';
      }});
      ['lf-has-contact', 'lf-washer-dryer', 'lf-gated'].forEach(id => {{
        document.getElementById(id).checked = false;
      }});
      document.getElementById('lf-hide-scams').checked = true;
      document.getElementById('lf-hide-age-restricted').checked = true;
      document.getElementById('lf-hide-unknown-address').checked = true;
      document.getElementById('lf-hide-duplicates').checked = true;
      workLocations.forEach((loc, i) => {{
        document.getElementById(`lf-work-distance-${{i}}`).value = '';
        const ct = document.getElementById(`lf-work-commute-${{i}}`);
        if (ct) ct.value = '';
      }});
      applyListFilters();
    }}

    function renderListFiltersSummary() {{
      const parts = [];
      if (listFilters.maxDistance != null) parts.push(`\\u2264${{listFilters.maxDistance}} mi`);
      if (listFilters.minPrice != null || listFilters.maxPrice != null) {{
        const lo = (listFilters.minPrice != null) ? `$${{listFilters.minPrice}}` : '$0';
        const hi = (listFilters.maxPrice != null) ? `$${{listFilters.maxPrice}}` : '+';
        parts.push(`${{lo}}\\u2013${{hi}}/mo`);
      }}
      if (listFilters.minBeds != null) parts.push(`${{listFilters.minBeds}}+ bd`);
      if (listFilters.minBaths != null) parts.push(`${{listFilters.minBaths}}+ ba`);
      if (listFilters.minSqft != null) parts.push(`${{listFilters.minSqft}}+ sqft`);
      if (listFilters.flooring) parts.push(`${{listFilters.flooring.charAt(0).toUpperCase()}}${{listFilters.flooring.slice(1)}} flooring`);
      if (listFilters.availableBy) parts.push(`available by ${{listFilters.availableBy}}`);

      if (listFilters.kitchenStyle) parts.push(`kitchen:${{listFilters.kitchenStyle}}`);
      if (listFilters.outdoorSpace) parts.push(`outdoor:${{listFilters.outdoorSpace}}`);
      if (listFilters.sizeImpression) parts.push(`size:${{listFilters.sizeImpression}}`);

      if (!listFilters.hideScams) parts.push('showing scams');
      if (!listFilters.hideDuplicates) parts.push('showing duplicates');
      if (!listFilters.hideUnknownAddress) parts.push('showing unknown addr');
      if (listFilters.hasContact) parts.push('has contact');
      if (listFilters.washerDryer) parts.push('washer/dryer');
      if (listFilters.gated) parts.push('gated');
      workLocations.forEach((loc, i) => {{
        const maxD = listFilters.workMaxDistance[i];
        if (maxD != null) parts.push(`\\u2264${{maxD}} mi to ${{loc.name}}`);
      }});
      document.getElementById('list-filters-summary').textContent = parts.length ? parts.join(' \\u00b7 ') : 'None';
      const badge = document.getElementById('list-filters-badge');
      badge.style.display = parts.length ? '' : 'none';
      badge.textContent = parts.length;
      document.getElementById('list-filters-btn').title = parts.length
        ? `Filters: ${{parts.join(' \\u00b7 ')}}`
        : 'Narrow the displayed list';
    }}

    // ---- Column visibility (persisted across reloads) ----
    const ALL_COLUMNS = ['mine', 'photo', 'distance', 'price', 'beds', 'baths', 'sqft', 'type', 'movein', 'amenities', 'flooring', 'source', 'quality', 'risk', 'kitchen', 'outdoor', 'size', 'score', 'contact'];

    function loadColumnPrefs() {{
      try {{ return JSON.parse(localStorage.getItem('columnPrefs') || '{{}}'); }} catch (e) {{ return {{}}; }}
    }}

    function saveColumnPrefs() {{
      try {{ localStorage.setItem('columnPrefs', JSON.stringify(columnPrefs)); }} catch (e) {{}}
    }}

    // Vision columns start hidden — they populate only after a Gemini rescan
    // contact starts hidden — most units won't have this data
    const DEFAULT_HIDDEN_COLS = [];
    let columnPrefs = loadColumnPrefs();
    DEFAULT_HIDDEN_COLS.forEach(col => {{
      if (!(col in columnPrefs)) columnPrefs[col] = false;
    }});

    function isColumnVisible(col) {{
      return columnPrefs[col] !== false;
    }}

    function applyColumnPrefs() {{
      const table = document.querySelector('.units-table');
      ALL_COLUMNS.forEach(col => {{
        table.classList.toggle('hide-' + col, !isColumnVisible(col));
      }});
      table.classList.toggle('hide-commute-avg', !isColumnVisible('commute-avg'));
      applyWorkColumnVis();
      renderColumnsSummary();
    }}

    function applyWorkColumnVis() {{
      let css = '';
      workLocations.forEach((loc, i) => {{
        if (!isColumnVisible('work-dist-' + i)) {{
          css += `.units-table .work-dist-col-${{i}} {{ display: none !important; }} `;
        }}
      }});
      let el = document.getElementById('work-col-vis-style');
      if (!el) {{ el = document.createElement('style'); el.id = 'work-col-vis-style'; document.head.appendChild(el); }}
      el.textContent = css;
    }}

    function renderColumnsSummary() {{
      const hiddenStatic = ALL_COLUMNS.filter(col => !isColumnVisible(col));
      const hiddenWork = workLocations.filter((loc, i) => !isColumnVisible('work-dist-' + i));
      const hiddenCommute = (workLocations.length > 1 && !isColumnVisible('commute-avg')) ? 1 : 0;
      const total = hiddenStatic.length + hiddenWork.length + hiddenCommute;
      const badge = document.getElementById('columns-badge');
      badge.style.display = total ? '' : 'none';
      badge.textContent = total;
      document.getElementById('columns-btn').title = total
        ? `Columns: ${{total}} hidden`
        : 'Choose which table columns are displayed';
    }}

    function onColumnToggle(checkbox) {{
      columnPrefs[checkbox.dataset.col] = checkbox.checked;
      saveColumnPrefs();
      applyColumnPrefs();
    }}

    function resetColumnPrefs() {{
      columnPrefs = {{}};
      saveColumnPrefs();
      document.querySelectorAll('#columns-panel input[data-col]').forEach(cb => {{ cb.checked = true; }});
      applyColumnPrefs();
      applyWorkColumnVis();
    }}

    // ---- Work locations (named places to show a per-unit distance column for) ----
    function loadWorkLocations() {{
      try {{ return JSON.parse(localStorage.getItem('workLocations') || '[]'); }} catch (e) {{ return []; }}
    }}

    function saveWorkLocations() {{
      try {{ localStorage.setItem('workLocations', JSON.stringify(workLocations)); }} catch (e) {{}}
    }}

    let workLocations = loadWorkLocations();

    // ---- Scoring / Ranking criteria (persisted to localStorage) ----
    const SCORING_DEFAULTS = {{
      targetPrice: null, wPrice: 5,
      maxCommute: null, wCommute: 5,
      targetBeds: null, wBeds: 5,
      targetBaths: null, wBaths: 4,
      targetSqft: null, wSqft: 3,
      minQuality: null, wQuality: 4,
      prefFlooring: null, wFlooring: 2,
      prefKitchen: null, wKitchen: 2,
      prefOutdoor: null, wOutdoor: 2,
      wScam: 3, wWd: 2, wSpacious: 1, wLight: 1, wNoage: 5,
    }};

    function loadScoringCriteria() {{
      try {{ return Object.assign({{}}, SCORING_DEFAULTS, JSON.parse(localStorage.getItem('scoringCriteria') || '{{}}')); }}
      catch (e) {{ return Object.assign({{}}, SCORING_DEFAULTS); }}
    }}
    function saveScoringCriteria() {{
      try {{ localStorage.setItem('scoringCriteria', JSON.stringify(scoringCriteria)); }} catch (e) {{}}
    }}
    let scoringCriteria = loadScoringCriteria();

    function scoringActive() {{
      const s = scoringCriteria;
      return s.targetPrice != null || s.maxCommute != null || s.targetBeds != null || s.targetBaths != null
        || s.targetSqft != null || s.minQuality != null
        || s.prefFlooring || s.prefKitchen || s.prefOutdoor;
    }}

    function computeScore(u) {{
      const s = scoringCriteria;
      const dims = [];
      const price = Number(u.price || 0);

      if (s.targetPrice != null && s.wPrice > 0 && price > 0) {{
        const diff = Math.abs(price - s.targetPrice) / s.targetPrice;
        dims.push({{ score: Math.max(0, 1 - diff), weight: s.wPrice }});
      }}

      if (s.maxCommute != null && s.wCommute > 0) {{
        let dist = null;
        if (workLocations.length && u.commute_score != null) {{
          dist = u.commute_score;
        }} else if (u.distance_miles != null) {{
          dist = u.distance_miles;
        }}
        if (dist != null) {{
          dims.push({{ score: Math.max(0, 1 - dist / s.maxCommute), weight: s.wCommute }});
        }}
      }}

      if (s.targetBeds != null && s.wBeds > 0 && u.beds != null) {{
        const beds = Number(u.beds);
        const diff = Math.abs(beds - s.targetBeds) / Math.max(s.targetBeds, 1);
        dims.push({{ score: Math.max(0, 1 - diff), weight: s.wBeds }});
      }}

      if (s.targetBaths != null && s.wBaths > 0 && u.baths != null) {{
        const baths = Number(u.baths);
        const diff = Math.abs(baths - s.targetBaths) / Math.max(s.targetBaths, 1);
        dims.push({{ score: Math.max(0, 1 - diff), weight: s.wBaths }});
      }}

      if (s.targetSqft != null && s.wSqft > 0 && u.sqft) {{
        dims.push({{ score: Math.min(1, Number(u.sqft) / s.targetSqft), weight: s.wSqft }});
      }}

      if (s.minQuality != null && s.wQuality > 0) {{
        const q = u.quality_rating != null ? u.quality_rating : 2.5;
        dims.push({{ score: Math.min(1, q / 5), weight: s.wQuality }});
      }}

      if (s.prefFlooring && s.wFlooring > 0) {{
        dims.push({{ score: (u.flooring_type === s.prefFlooring) ? 1 : 0, weight: s.wFlooring }});
      }}
      if (s.prefKitchen && s.wKitchen > 0) {{
        dims.push({{ score: (u.kitchen_style === s.prefKitchen) ? 1 : 0, weight: s.wKitchen }});
      }}
      if (s.prefOutdoor && s.wOutdoor > 0) {{
        const has = u.outdoor_space && u.outdoor_space !== 'none';
        const exact = u.outdoor_space === s.prefOutdoor;
        dims.push({{ score: exact ? 1 : (has ? 0.5 : 0), weight: s.wOutdoor }});
      }}

      if (s.wScam > 0) {{
        const risk = Number(u.scam_score || 0);
        dims.push({{ score: Math.max(0, 1 - risk / 100), weight: s.wScam }});
      }}
      if (s.wWd > 0) {{
        dims.push({{ score: u.has_washer_dryer ? 1 : 0, weight: s.wWd }});
      }}
      if (s.wSpacious > 0) {{
        const sizeMap = {{ spacious: 1, average: 0.5, cramped: 0 }};
        dims.push({{ score: sizeMap[u.size_impression] != null ? sizeMap[u.size_impression] : 0.5, weight: s.wSpacious }});
      }}
      if (s.wLight > 0) {{
        const lightMap = {{ high: 1, medium: 0.5, low: 0 }};
        dims.push({{ score: lightMap[u.natural_light] != null ? lightMap[u.natural_light] : 0.5, weight: s.wLight }});
      }}
      if (s.wNoage > 0) {{
        dims.push({{ score: u.age_restriction ? 0 : 1, weight: s.wNoage }});
      }}

      if (dims.length === 0) return null;
      const totalWeight = dims.reduce((a, d) => a + d.weight, 0);
      if (totalWeight === 0) return null;
      const raw = dims.reduce((a, d) => a + d.score * d.weight, 0) / totalWeight;
      return Math.round(raw * 100);
    }}

    function applyScoringCriteria() {{
      scoringCriteria.targetPrice = numOrNull(document.getElementById('rank-target-price').value);
      scoringCriteria.maxCommute = numOrNull(document.getElementById('rank-max-commute').value);
      scoringCriteria.targetBeds = numOrNull(document.getElementById('rank-target-beds').value);
      scoringCriteria.targetBaths = numOrNull(document.getElementById('rank-target-baths').value);
      scoringCriteria.targetSqft = numOrNull(document.getElementById('rank-target-sqft').value);
      scoringCriteria.minQuality = numOrNull(document.getElementById('rank-min-quality').value);
      scoringCriteria.prefFlooring = document.getElementById('rank-pref-flooring').value || null;
      scoringCriteria.prefKitchen = document.getElementById('rank-pref-kitchen').value || null;
      scoringCriteria.prefOutdoor = document.getElementById('rank-pref-outdoor').value || null;
      ['price', 'commute', 'beds', 'baths', 'sqft', 'quality', 'flooring', 'kitchen', 'outdoor', 'scam', 'wd', 'spacious', 'light', 'noage'].forEach(k => {{
        const el = document.getElementById('rank-w-' + k);
        const val = Number(el.value);
        scoringCriteria['w' + k.charAt(0).toUpperCase() + k.slice(1)] = val;
        document.getElementById('rank-w-' + k + '-val').textContent = val;
      }});
      saveScoringCriteria();
      renderRankingSummary();
      renderAll(lastData);
    }}

    function clearScoringCriteria() {{
      scoringCriteria = Object.assign({{}}, SCORING_DEFAULTS);
      saveScoringCriteria();
      populateRankingPanel();
      renderRankingSummary();
      renderAll(lastData);
    }}

    function populateRankingPanel() {{
      const s = scoringCriteria;
      document.getElementById('rank-target-price').value = s.targetPrice != null ? s.targetPrice : '';
      document.getElementById('rank-max-commute').value = s.maxCommute != null ? s.maxCommute : '';
      document.getElementById('rank-target-beds').value = s.targetBeds != null ? s.targetBeds : '';
      document.getElementById('rank-target-baths').value = s.targetBaths != null ? s.targetBaths : '';
      document.getElementById('rank-target-sqft').value = s.targetSqft != null ? s.targetSqft : '';
      document.getElementById('rank-min-quality').value = s.minQuality != null ? String(s.minQuality) : '';
      document.getElementById('rank-pref-flooring').value = s.prefFlooring || '';
      document.getElementById('rank-pref-kitchen').value = s.prefKitchen || '';
      document.getElementById('rank-pref-outdoor').value = s.prefOutdoor || '';
      ['price', 'commute', 'beds', 'baths', 'sqft', 'quality', 'flooring', 'kitchen', 'outdoor', 'scam', 'wd', 'spacious', 'light', 'noage'].forEach(k => {{
        const key = 'w' + k.charAt(0).toUpperCase() + k.slice(1);
        document.getElementById('rank-w-' + k).value = s[key];
        document.getElementById('rank-w-' + k + '-val').textContent = s[key];
      }});
    }}

    function renderRankingSummary() {{
      const parts = [];
      const s = scoringCriteria;
      if (s.targetPrice != null) parts.push('$' + s.targetPrice + '/mo');
      if (s.maxCommute != null) parts.push(s.maxCommute + ' mi');
      if (s.targetBeds != null) parts.push(s.targetBeds + ' bd');
      if (s.targetBaths != null) parts.push(s.targetBaths + ' ba');
      if (s.targetSqft != null) parts.push(s.targetSqft + ' sqft');
      if (s.minQuality != null) parts.push(s.minQuality + '\\u2605');
      if (s.prefFlooring) parts.push(s.prefFlooring);
      if (s.prefKitchen) parts.push(s.prefKitchen + ' kitchen');
      if (s.prefOutdoor) parts.push(s.prefOutdoor);
      document.getElementById('ranking-summary').textContent = parts.length ? parts.join(' \\u00b7 ') : 'Set targets to rank units';
      const badge = document.getElementById('ranking-badge');
      badge.style.display = scoringActive() ? '' : 'none';
      badge.textContent = '\\u2713';
    }}

    function scoreColorHsl(score) {{
      const hue = Math.round(score * 1.2);
      return `hsl(${{hue}}, 70%, 50%)`;
    }}

    function scoreCellHtml(u) {{
      if (u.overall_score == null) return '\\u2014';
      const color = scoreColorHsl(u.overall_score);
      return `<span class="score-bar"><span class="score-bar-fill" style="width:${{u.overall_score}}%;background:${{color}}"></span></span><span class="score-value" style="color:${{color}}">${{u.overall_score}}</span>`;
    }}

    // ---- Save / load list filters to localStorage ----
    function saveListFilters() {{
      try {{ localStorage.setItem('listFilters', JSON.stringify(listFilters)); }} catch (e) {{}}
      try {{ localStorage.setItem('searchFilters', JSON.stringify(searchFilters)); }} catch (e) {{}}
    }}
    function loadSavedListFilters() {{
      try {{
        const saved = JSON.parse(localStorage.getItem('listFilters') || 'null');
        if (saved) Object.assign(listFilters, saved);
      }} catch (e) {{}}
      try {{
        const saved = JSON.parse(localStorage.getItem('searchFilters') || 'null');
        if (saved) Object.assign(searchFilters, saved);
      }} catch (e) {{}}
    }}
    function populateListFilterDom() {{
      document.getElementById('lf-max-distance').value = listFilters.maxDistance != null ? listFilters.maxDistance : '';
      document.getElementById('lf-min-price').value = listFilters.minPrice != null ? listFilters.minPrice : '';
      document.getElementById('lf-max-price').value = listFilters.maxPrice != null ? listFilters.maxPrice : '';
      document.getElementById('lf-min-beds').value = listFilters.minBeds != null ? listFilters.minBeds : '';
      document.getElementById('lf-min-baths').value = listFilters.minBaths != null ? listFilters.minBaths : '';
      document.getElementById('lf-min-sqft').value = listFilters.minSqft != null ? listFilters.minSqft : '';
      document.getElementById('lf-flooring').value = listFilters.flooring || '';
      document.getElementById('lf-available-by').value = listFilters.availableBy || '';
      document.getElementById('lf-kitchen-style').value = listFilters.kitchenStyle || '';
      document.getElementById('lf-outdoor-space').value = listFilters.outdoorSpace || '';
      document.getElementById('lf-size-impression').value = listFilters.sizeImpression || '';
      document.getElementById('lf-hide-scams').checked = listFilters.hideScams !== false;
      document.getElementById('lf-has-contact').checked = !!listFilters.hasContact;
      document.getElementById('lf-washer-dryer').checked = !!listFilters.washerDryer;
      document.getElementById('lf-gated').checked = !!listFilters.gated;
      document.getElementById('lf-hide-age-restricted').checked = listFilters.hideAgeRestricted !== false;
      document.getElementById('lf-hide-unknown-address').checked = listFilters.hideUnknownAddress !== false;
      document.getElementById('lf-hide-duplicates').checked = listFilters.hideDuplicates !== false;
      document.getElementById('search-contains').value = searchFilters.contains || '';
      document.getElementById('search-excludes').value = searchFilters.excludes || '';
    }}

    async function geocodeAddress(address) {{
      const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${{encodeURIComponent(address)}}`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const results = await resp.json();
      if (!results.length) throw new Error('Address not found');
      return {{ lat: parseFloat(results[0].lat), lon: parseFloat(results[0].lon) }};
    }}

    function renderWorkLocationsList() {{
      const list = document.getElementById('work-locations-list');
      list.innerHTML = workLocations.map((loc, i) => {{
        const hrs = (loc.hours_start || '09:00') + '\\u2013' + (loc.hours_end || '17:00');
        const maxC = loc.max_commute_min ? ` / max ${{loc.max_commute_min}}m` : '';
        const commuteTag = `<span class="work-location-commute">${{hrs}}${{maxC}}</span>`;
        return `<div class="work-location-item">
          <span class="work-location-name">${{escapeHtml(loc.name)}}</span>
          <span class="work-location-address">${{escapeHtml(loc.address)}}</span>
          ${{commuteTag}}
          <button class="work-location-edit" onclick="editWorkLocation(${{i}})" title="Edit">\\u270e</button>
          <button class="work-location-remove" onclick="removeWorkLocation(${{i}})" title="Remove">&times;</button>
        </div>`;
      }}).join('');

      const badge = document.getElementById('work-locations-badge');
      badge.style.display = workLocations.length ? '' : 'none';
      badge.textContent = workLocations.length;
      document.getElementById('work-locations-btn').title = workLocations.length
        ? `Work locations: ${{workLocations.map(l => l.name).join(', ')}}`
        : 'Add work locations to show distance columns';
    }}

    // Plots a marker for each saved work location on the map
    function renderWorkLocationMarkers() {{
      workLocationMarkers.clearLayers();
      workLocations.forEach(loc => {{
        if (loc.lat == null || loc.lon == null) return;
        L.marker([loc.lat, loc.lon], {{ icon: workLocationIcon() }})
          .bindPopup(`<b>\\ud83d\\udcbc ${{escapeHtml(loc.name)}}</b><br>${{escapeHtml(loc.address)}}`)
          .addTo(workLocationMarkers);
      }});
    }}

    // Rebuilds the per-work-location "Max distance to <name>" list-filter
    // inputs, keeping listFilters.workMaxDistance in sync by index
    function renderWorkDistanceFilters() {{
      const grid = document.getElementById('list-filters-grid');
      grid.querySelectorAll('.lf-work-distance').forEach(el => el.remove());
      grid.querySelectorAll('.lf-work-commute').forEach(el => el.remove());
      listFilters.workMaxDistance = workLocations.map(() => null);
      listFilters.workMaxCommute = workLocations.map((loc) => loc.max_commute_min || null);
      workLocations.forEach((loc, i) => {{
        const div = document.createElement('div');
        div.className = 'lf-work-distance';
        const label = document.createElement('label');
        label.setAttribute('for', `lf-work-distance-${{i}}`);
        label.textContent = `Max dist. to ${{loc.name}} (mi)`;
        const input = document.createElement('input');
        input.type = 'number';
        input.id = `lf-work-distance-${{i}}`;
        input.min = '0';
        input.step = '0.5';
        input.placeholder = 'Any';
        input.addEventListener('input', applyListFilters);
        div.appendChild(label);
        div.appendChild(input);
        grid.appendChild(div);

        const divT = document.createElement('div');
        divT.className = 'lf-work-commute';
        const labelT = document.createElement('label');
        labelT.setAttribute('for', `lf-work-commute-${{i}}`);
        labelT.textContent = `Max time to ${{loc.name}} (min)`;
        const inputT = document.createElement('input');
        inputT.type = 'number';
        inputT.id = `lf-work-commute-${{i}}`;
        inputT.min = '0';
        inputT.step = '5';
        inputT.placeholder = loc.max_commute_min ? String(loc.max_commute_min) : 'Any';
        if (loc.max_commute_min) inputT.value = loc.max_commute_min;
        inputT.addEventListener('input', applyListFilters);
        divT.appendChild(labelT);
        divT.appendChild(inputT);
        grid.appendChild(divT);
      }});
    }}

    function renderWorkLocationColumnCheckboxes() {{
      const panel = document.getElementById('columns-panel').querySelector('.criteria-edit-grid');
      panel.querySelectorAll('.work-col-label').forEach(el => el.remove());
      const mkCb = (col, label) => {{
        const lbl = document.createElement('label');
        lbl.className = 'work-col-label';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.col = col;
        cb.checked = isColumnVisible(col);
        cb.addEventListener('change', () => onColumnToggle(cb));
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(' ' + label));
        panel.appendChild(lbl);
      }};
      workLocations.forEach((loc, i) => mkCb('work-dist-' + i, loc.name));
      if (workLocations.length > 1) mkCb('commute-avg', 'Commute avg');
    }}

    function renderWorkLocationHeaders() {{
      document.querySelectorAll('.units-table .work-loc-th').forEach(th => th.remove());
      document.querySelectorAll('.units-table .commute-score-th').forEach(th => th.remove());
      const anchorTh = document.getElementById('work-anchor-th');
      workLocations.forEach((loc, i) => {{
        const th = document.createElement('th');
        th.className = `work-loc-col work-loc-dist-col work-dist-col-${{i}} work-loc-th sortable`;
        th.dataset.sort = `work_dist_${{i}}`;
        th.textContent = loc.name;
        th.addEventListener('click', () => applySort(`work_dist_${{i}}`));
        anchorTh.parentNode.insertBefore(th, anchorTh);
      }});
      if (workLocations.length > 1) {{
        const th = document.createElement('th');
        th.className = 'work-loc-col commute-score-col commute-score-th sortable';
        th.dataset.sort = 'commute_score';
        th.title = 'Average distance to all work locations';
        th.textContent = 'Commute avg';
        th.addEventListener('click', () => applySort('commute_score'));
        anchorTh.parentNode.insertBefore(th, anchorTh);
      }}
      renderWorkLocationColumnCheckboxes();
      updateSortIndicators();
    }}

    let editingWorkLocationIndex = -1;

    function editWorkLocation(index) {{
      const loc = workLocations[index];
      if (!loc) return;
      editingWorkLocationIndex = index;
      document.getElementById('work-loc-name-input').value = loc.name;
      document.getElementById('work-loc-address-input').value = loc.address;
      document.getElementById('work-loc-hours-start').value = loc.hours_start || '09:00';
      document.getElementById('work-loc-hours-end').value = loc.hours_end || '17:00';
      document.getElementById('work-loc-max-commute').value = loc.max_commute_min || '';
      const addBtn = document.getElementById('add-work-location-btn');
      addBtn.textContent = 'Save';
      let cancelBtn = document.getElementById('cancel-work-edit-btn');
      if (!cancelBtn) {{
        cancelBtn = document.createElement('button');
        cancelBtn.id = 'cancel-work-edit-btn';
        cancelBtn.className = 'ctrl-btn';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', cancelWorkLocationEdit);
        addBtn.parentNode.insertBefore(cancelBtn, addBtn.nextSibling);
      }}
      cancelBtn.style.display = '';
    }}

    function cancelWorkLocationEdit() {{
      editingWorkLocationIndex = -1;
      document.getElementById('work-loc-name-input').value = '';
      document.getElementById('work-loc-address-input').value = '';
      document.getElementById('work-loc-hours-start').value = '09:00';
      document.getElementById('work-loc-hours-end').value = '17:00';
      document.getElementById('work-loc-max-commute').value = '';
      document.getElementById('add-work-location-btn').textContent = 'Add';
      const cancelBtn = document.getElementById('cancel-work-edit-btn');
      if (cancelBtn) cancelBtn.style.display = 'none';
      document.getElementById('work-location-status').textContent = '';
    }}

    function removeWorkLocation(index) {{
      if (editingWorkLocationIndex === index) cancelWorkLocationEdit();
      else if (editingWorkLocationIndex > index) editingWorkLocationIndex--;
      workLocations.splice(index, 1);
      saveWorkLocations();
      drivingDistCache = {{}};
      renderWorkLocationsList();
      renderWorkLocationHeaders();
      renderWorkLocationMarkers();
      renderWorkDistanceFilters();
      renderListFiltersSummary();
      renderAll(lastData);
      if (workLocations.length) fetchDrivingDistances();
    }}

    // ---- Sorting ----
    let currentSort = {{ field: null, dir: 'asc' }};

    function sortUnits(units) {{
      const {{ field, dir }} = currentSort;
      if (!field) return units;

      return units.slice().sort((a, b) => {{
        let va = a[field];
        let vb = b[field];
        if (field === 'favorite') {{
          va = va ? 1 : 0;
          vb = vb ? 1 : 0;
        }}

        // Always push nulls/undefined/empty to the bottom regardless of sort direction
        const aNull = (va == null || va === '');
        const bNull = (vb == null || vb === '');
        if (aNull && bNull) return 0;
        if (aNull) return 1;
        if (bNull) return -1;

        // Prefer numeric comparison: works for int/float fields (quality_rating,
        // distance, price, beds, etc.) whether stored as numbers or numeric strings
        const vaN = Number(va);
        const vbN = Number(vb);
        if (!isNaN(vaN) && !isNaN(vbN)) {{
          return dir === 'asc' ? (vaN - vbN) : (vbN - vaN);
        }}

        // String comparison for text fields
        const vas = String(va).toLowerCase();
        const vbs = String(vb).toLowerCase();
        if (vas < vbs) return dir === 'asc' ? -1 : 1;
        if (vas > vbs) return dir === 'asc' ? 1 : -1;
        return 0;
      }});
    }}

    function applySort(field) {{
      if (currentSort.field === field) {{
        currentSort.dir = (currentSort.dir === 'asc') ? 'desc' : 'asc';
      }} else {{
        currentSort.field = field;
        currentSort.dir = 'asc';
      }}
      renderAll(lastData);
    }}

    function updateSortIndicators() {{
      document.querySelectorAll('.units-table th.sortable').forEach(th => {{
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sort === currentSort.field) {{
          th.classList.add(currentSort.dir === 'asc' ? 'sort-asc' : 'sort-desc');
        }}
      }});
    }}

    // ---- Rendering ----
    // Maps raw amenity strings (and the normalized washer/dryer + gated
    // flags) to a small set of icons - ordered roughly by how often renters
    // care about them, so the first couple shown are the most useful.
    const AMENITY_ICON_MAP = [
      {{ match: /w\\/d|washer|dryer|laundry|stackable/i, icon: '\\ud83e\\uddfa', label: 'Laundry / washer-dryer' }},
      {{ match: /gated/i, icon: '\\ud83d\\udd10', label: 'Gated' }},
      {{ match: /garage|carport|parking/i, icon: '\\ud83d\\ude97', label: 'Parking' }},
      {{ match: /cats? (are )?ok|dogs? (are )?ok|pet/i, icon: '\\ud83d\\udc3e', label: 'Pets OK' }},
      {{ match: /air condition|a\\/c/i, icon: '\\u2744\\ufe0f', label: 'A/C' }},
      {{ match: /\\bpool\\b/i, icon: '\\ud83c\\udfca', label: 'Pool' }},
      {{ match: /wheelchair|accessible/i, icon: '\\u267f', label: 'Wheelchair accessible' }},
      {{ match: /ev charging|electric vehicle/i, icon: '\\ud83d\\udd0c', label: 'EV charging' }},
      {{ match: /furnished/i, icon: '\\ud83d\\udecb\\ufe0f', label: 'Furnished' }},
    ];

    function amenitiesCellHtml(u) {{
      const amenities = u.amenities || [];
      const matched = [];
      AMENITY_ICON_MAP.forEach(({{ match, icon, label }}) => {{
        const hit = amenities.some(a => match.test(a)) ||
          (label === 'Gated' && u.is_gated) ||
          (label === 'Laundry / washer-dryer' && u.has_washer_dryer);
        if (hit) matched.push({{ icon, label }});
      }});

      const title = amenities.length ? amenities.join(', ') : (matched.length ? matched.map(m => m.label).join(', ') : 'No amenities listed');
      if (!matched.length) return `<span title="${{escapeHtml(title)}}">\\u2014</span>`;

      const shown = matched.slice(0, 2);
      const extra = matched.length - shown.length;
      let html = shown.map(m => m.icon).join(' ');
      if (extra > 0) html += ` <span class="amenity-more">+${{extra}}</span>`;
      return `<span title="${{escapeHtml(title)}}">${{html}}</span>`;
    }}

    function unitRowHtml(u, dupAddresses, continuesGroup, groupId) {{
      const price = dashOr(u.price, 'N/A');
      const beds = dashOr(u.beds);
      const baths = dashOr(u.baths);
      const sqft = dashOr(u.sqft);
      const address = u.address || 'Address unknown';
      const source = u.source || 'Unknown source';
      const sourceUrl = u.source_url || '#';
      const housingType = u.housing_type || 'Unknown';
      const ageBadge = u.age_restriction
        ? ` <span class="age-badge" title="Age-restricted: ${{u.age_restriction}}+ community">${{u.age_restriction}}+</span>`
        : '';

      // Multiple listings at the same address (e.g. different floorplans in
      // one apartment complex) are otherwise indistinguishable - tag with title
      let addressExtra = '';
      if (u.address && dupAddresses.has(u.address)) {{
        const fullTitle = u.title || '';
        const tag = fullTitle.length > 60 ? fullTitle.slice(0, 57) + '\\u2026' : fullTitle;
        addressExtra = `<div class="dup-tag" title="${{escapeHtml(fullTitle)}}">${{escapeHtml(tag)}}</div>`;
      }}

      // Always show the unit's id so otherwise-identical rows can be told apart
      const idTag = `<span class="unit-id-tag">${{escapeHtml(u.id || '')}}</span>`;

      const isPrimary = !u.linked_primary || u.linked_primary === u.id;
      const linkedGroup = u.linked_ids && u.linked_ids.length ? [u.id, ...u.linked_ids] : [];
      const linkedBadge = linkedGroup.length
        ? `<span class="linked-badge${{isPrimary ? '' : ' linked-badge-dup'}}" onclick="filterToLinkedGroup(${{JSON.stringify(linkedGroup).replace(/"/g, '&quot;')}})" title="Click to show all ${{linkedGroup.length}} linked listings">`
          + `\U0001f517 ${{linkedGroup.length}} listings`
          + (isPrimary ? '' : ' (dup)')
          + `</span>`
        : '';

      const photoPaths = (u.photos || []).map(photoPath);
      const photosAttr = JSON.stringify(photoPaths).replace(/'/g, '&#39;');
      let thumbHtml;
      if (photoPaths.length === 0) {{
        thumbHtml = '<div class="unit-thumb-empty">\\u2014</div>';
      }} else if (photoPaths.length === 1) {{
        thumbHtml = `<img src="${{photoPaths[0]}}" alt="Thumbnail" class="unit-thumb" data-photos='${{photosAttr}}' data-index="0" onclick="openGallery(this)">`;
      }} else {{
        const gridImgs = photoPaths.slice(0, 4).map((p, i) =>
          `<img src="${{p}}" alt="Thumbnail" data-photos='${{photosAttr}}' data-index="${{i}}" onclick="openGallery(this)">`
        ).join('');
        thumbHtml = `<div class="thumb-grid">${{gridImgs}}</div>`;
      }}

      const distTint = numericTint(u.distance_miles, _tintRanges.distMin, _tintRanges.distMax, true);
      const distanceHtml = (u.distance_miles != null) ? `${{u.distance_miles}} mi` : '\\u2014';
      const priceTint = numericTint(Number(u.price), _tintRanges.priceMin, _tintRanges.priceMax, true);
      const typeTint = TYPE_COLORS[(housingType || '').toLowerCase()] || '';
      const floorTint = FLOORING_COLORS[(u.flooring_type || '').toLowerCase()] || '';
      const detailsHref = `apartments/${{u.id}}/index.html`;

      const favActive = u.favorite ? 'active' : '';
      const favIcon = u.favorite ? '\\u2605' : '\\u2606';
      const favTitle = u.favorite ? 'Remove from favorites' : 'Add to favorites';
      const noteActive = u.notes_text ? 'active' : '';
      const noteTitle = u.notes_text ? 'Edit notes' : 'Add notes';
      const timelineActive = (u.timeline && u.timeline.length) ? 'active' : '';
      const timelineTitle = (u.timeline && u.timeline.length) ? 'View timeline' : 'Add timeline entry';
      const scamActive = u.scam ? 'active scam-active' : '';
      const scamTitle = u.scam ? 'Unmark as scam' : 'Flag as scam';
      const mineHtml = `<button class="icon-btn fav-btn ${{favActive}}" title="${{favTitle}}" onclick="toggleFavorite('${{u.id}}')">${{favIcon}}</button>` +
        `<button class="icon-btn note-btn ${{noteActive}}" title="${{noteTitle}}" onclick="openNotesModal('${{u.id}}')">\\u270e</button>` +
        `<button class="icon-btn timeline-btn ${{timelineActive}}" title="${{timelineTitle}}" onclick="openTimelineModal('${{u.id}}')">\\u{{1F551}}</button>` +
        `<button class="icon-btn scam-btn ${{scamActive}}" title="${{scamTitle}}" onclick="toggleScam('${{u.id}}')">\\u26A0</button>`;

      const workCells = workLocations.map((loc, i) => {{
        const d = u['work_dist_' + i];
        const dd = u['work_drive_dist_' + i];
        const dm = u['work_drive_min_' + i];
        let cell = '\\u2014';
        if (dd != null) {{
          cell = `${{dd}} mi<span class="work-loc-driving">\\uD83D\\uDE97 ${{dm}} min</span>`;
          if (loc.hours_start || loc.hours_end) {{
            const hrs = (loc.hours_start || '09:00') + '\\u2013' + (loc.hours_end || '17:00');
            cell += `<span class="work-loc-driving">${{hrs}}</span>`;
          }}
        }} else if (d != null) {{
          cell = `${{d}} mi`;
        }}
        return `<td class="work-loc-col work-loc-dist-col work-dist-col-${{i}}">${{cell}}</td>`;
      }}).join('');
      const commuteCell = (workLocations.length > 1)
        ? `<td class="work-loc-col commute-score-col">${{(u.commute_score != null) ? u.commute_score + ' mi' : '\\u2014'}}</td>`
        : '';

      let groupClass = '';
      if (u.address && dupAddresses.has(u.address)) {{
        groupClass = continuesGroup ? ' same-property group-continues' : ' same-property';
      }}
      const scamRowClass = u.scam ? ' scam-row' : '';

      return `<tr class="unit-row${{groupClass}}${{scamRowClass}}" data-price="${{price}}" data-beds="${{beds}}" data-baths="${{baths}}" onmouseenter="highlightMapMarker('${{u.id}}')" onmouseleave="clearMapMarkerHighlight()">
        <td class="mine-col">${{mineHtml}}</td>
        <td class="unit-thumb-col">${{thumbHtml}}</td>
        <td class="unit-distance-col"${{distTint ? ` style="${{distTint}}"` : ''}}>${{distanceHtml}}</td>
        <td class="unit-address-col"><span class="unit-link">${{address}}</span><div class="unit-id-line">${{idTag}}${{linkedBadge}}<span class="link-icons"><a href="${{sourceUrl}}" target="_blank" class="link-icon link-source">source</a><a href="${{detailsHref}}" target="_blank" class="link-icon link-details">details</a></span></div>${{addressExtra}}</td>
        <td class="unit-price-col"${{priceTint ? ` style="${{priceTint}}"` : ''}}>$${{formatNumber(price)}}</td>
        <td class="unit-spec-col beds-col" data-label="Beds">${{beds}} bd</td>
        <td class="unit-spec-col baths-col" data-label="Baths">${{baths}} ba</td>
        <td class="unit-spec-col sqft-col" data-label="Sqft">${{formatNumber(sqft)}}</td>
        <td class="mobile-specs-row">${{beds}} bd &middot; ${{baths}} ba &middot; ${{formatNumber(sqft)}} sqft</td>
        <td class="unit-type-col"${{typeTint ? ` style="${{typeTint}}"` : ''}}>${{housingType}}${{ageBadge}}</td>
        <td class="movein-col">${{moveInHtml(u)}}</td>
        <td class="unit-amenity-col">${{amenitiesCellHtml(u)}}</td>
        <td class="flooring-col"${{floorTint ? ` style="${{floorTint}}"` : ''}}>${{flooringHtml(u)}}</td>
        <td class="unit-source-col">${{source}}</td>
        <td class="quality-col">${{qualityStarsHtml(u)}}</td>
        <td class="risk-col">${{riskCellHtml(u)}}</td>
        <td class="kitchen-col">${{escapeHtml(u.kitchen_style || '\\u2014')}}</td>
        <td class="outdoor-col">${{escapeHtml(u.outdoor_space || '\\u2014')}}</td>
        <td class="size-col">${{escapeHtml(u.size_impression || '\\u2014')}}</td>
        <td class="score-col">${{scoreCellHtml(u)}}</td>
        <td class="contact-col">${{contactCellHtml(u)}}</td>

        ${{workCells}}${{commuteCell}}
      </tr>`;
    }}

    function renderTable(units, totalCount, dupAddresses) {{
      const tbody = document.getElementById('units-tbody');
      const table = document.querySelector('.units-table');
      const emptyState = document.getElementById('empty-state');

      if (units.length === 0) {{
        table.style.display = 'none';
        emptyState.style.display = 'block';
        if (totalCount === 0) {{
          emptyState.innerHTML = '<div class="icon">\\ud83d\\udd0d</div><h2>No Units Found Yet</h2>' +
            '<p>Run the crawler to fetch listings from your sources.<br><code>python scripts/crawl_all.py</code></p>';
        }} else {{
          emptyState.innerHTML = '<div class="icon">\\ud83d\\udd0d</div><h2>No Units Match Your Filters</h2>' +
            `<p>${{totalCount}} unit(s) are in the database, but all were excluded by the current<br>` +
            'filters in <code>config.json</code> (distance, beds, baths, sqft, or price).<br>' +
            'Try widening <code>search_radius_miles</code> or lowering <code>min_beds</code>.</p>';
        }}
        tbody.innerHTML = '';
        return;
      }}

      table.style.display = '';
      emptyState.style.display = 'none';

      computeTintRanges(units);

      const gbFields = Array.isArray(currentGroupBy) ? currentGroupBy : (currentGroupBy ? [currentGroupBy] : []);
      const rows = [];
      let gSeq = 0;
      const collapsedGroups = new Set();

      function groupSummary(grpUnits) {{
        const prices = grpUnits.map(u => u.price).filter(p => p != null && p !== '');
        const minP = prices.length ? Math.min(...prices.map(Number)) : null;
        const maxP = prices.length ? Math.max(...prices.map(Number)) : null;
        const priceStr = prices.length === 0 ? '' :
          minP === maxP ? `$${{formatNumber(minP)}}` : `$${{formatNumber(minP)}}\\u2013$${{formatNumber(maxP)}}`;
        const bedsSet = [...new Set(grpUnits.map(u => u.beds).filter(b => b != null))].map(Number).sort((a,b)=>a-b);
        const bedsStr = bedsSet.length ? (bedsSet.length === 1 ? `${{bedsSet[0]}} bd` : `${{bedsSet[0]}}\\u2013${{bedsSet[bedsSet.length-1]}} bd`) : '';
        return [priceStr, bedsStr, `${{grpUnits.length}} units`].filter(Boolean).join(' \\u00b7 ');
      }}

      function emitGroup(groupUnits, depth, ancestorGids) {{
        if (depth >= gbFields.length) {{
          groupUnits.forEach((u, i) => {{
            const grpsAttr = ancestorGids.length ? ` data-grps="${{ancestorGids.join(' ')}}"` : '';
            const hidden = ancestorGids.some(g => collapsedGroups.has(g));
            const hideStyle = hidden ? ' style="display:none"' : '';
            let html = unitRowHtml(u, dupAddresses, i < groupUnits.length - 1, ancestorGids[ancestorGids.length - 1] || null);
            html = html.replace('<tr ', `<tr${{grpsAttr}}${{hideStyle}} `);
            rows.push(html);
          }});
          return;
        }}
        const field = gbFields[depth];
        const subMap = new Map();
        const subOrder = [];
        groupUnits.forEach(u => {{
          const val = String(u[field] || '') || '';
          if (!subMap.has(val)) {{ subMap.set(val, []); subOrder.push(val); }}
          subMap.get(val).push(u);
        }});
        subOrder.forEach(val => {{
          const subUnits = subMap.get(val);
          if (subUnits.length > 1 && val !== '') {{
            const gid = `g${{gSeq++}}`;
            const label = escapeHtml(val);
            const isCollapsed = groupsCollapsedByDefault;
            if (isCollapsed) collapsedGroups.add(gid);
            const collapsedClass = isCollapsed ? ' collapsed' : '';
            const depthClass = depth > 0 ? ` depth-${{depth}}` : '';
            const grpsAttr = ancestorGids.length ? ` data-grps="${{ancestorGids.join(' ')}}"` : '';
            const hidden = ancestorGids.some(g => collapsedGroups.has(g));
            const hideStyle = hidden ? ' style="display:none"' : '';
            rows.push(`<tr class="addr-group-hdr${{depthClass}}${{collapsedClass}}"${{grpsAttr}}${{hideStyle}} data-gid="${{gid}}" onclick="toggleAddrGroup(this)"><td colspan="999"><span class="addr-group-toggle">\\u25bc</span>${{label}}<span class="addr-group-summary">${{groupSummary(subUnits)}}</span></td></tr>`);
            emitGroup(subUnits, depth + 1, [...ancestorGids, gid]);
          }} else {{
            emitGroup(subUnits, depth + 1, ancestorGids);
          }}
        }});
      }}

      emitGroup(units, 0, []);
      tbody.innerHTML = rows.join('');
    }}

    function toggleAddrGroup(hdr) {{
      const gid = hdr.dataset.gid;
      const isCollapsing = hdr.classList.toggle('collapsed');
      document.querySelectorAll(`tr[data-grps]`).forEach(r => {{
        const grps = r.dataset.grps.split(' ');
        if (!grps.includes(gid)) return;
        if (isCollapsing) {{
          r.style.display = 'none';
        }} else {{
          const anyAncestorCollapsed = grps.some(g => {{
            if (g === gid) return false;
            const hdrEl = document.querySelector(`tr[data-gid="${{g}}"]`);
            return hdrEl && hdrEl.classList.contains('collapsed');
          }});
          if (!anyAncestorCollapsed) r.style.display = '';
        }}
      }});
    }}

    function renderMap(units, dupAddresses) {{
      markers.clearLayers();
      unitMarkers = {{}};
      units.forEach(u => {{
        if (u.lat == null || u.lon == null) return;
        const m = L.marker([u.lat, u.lon], {{ icon: markerIcon(u.housing_type) }});
        const photoPaths = (u.photos || []).map(photoPath);
        const photo = photoPaths.length ? photoPaths[0] : null;
        const photosAttr = JSON.stringify(photoPaths).replace(/'/g, '&#39;');

        // Show up to 4 photos in a grid (like the list's thumbnail grid) so
        // clicking a marker gives a real look at the unit, not just one photo
        let photoHtml = '';
        if (photoPaths.length === 1) {{
          photoHtml = `<img class="popup-photo" src="${{photoPaths[0]}}" alt="" data-photos='${{photosAttr}}' data-index="0" onclick="openGallery(this)">`;
        }} else if (photoPaths.length > 1) {{
          const gridImgs = photoPaths.slice(0, 4).map((p, i) =>
            `<img src="${{p}}" alt="" data-photos='${{photosAttr}}' data-index="${{i}}" onclick="openGallery(this)">`
          ).join('');
          photoHtml = `<div class="popup-photo-grid">${{gridImgs}}</div>`;
        }}

        const href = `apartments/${{u.id}}/index.html`;
        const typeLabel = escapeHtml(u.housing_type || 'Unknown');
        const typeDot = `<span class="map-marker ${{markerClassForType(u.housing_type)}}"></span>`;
        const address = u.address || 'Address unknown';
        const addressLine = `<div class="popup-address">${{escapeHtml(address)}}</div>`;

        // Show specs in the popup so units sharing an address (same building,
        // different floorplans) can be told apart after clustering/spiderfying
        const specs = [];
        if (u.beds != null) specs.push(`${{u.beds}} bd`);
        if (u.baths != null) specs.push(`${{u.baths}} ba`);
        if (u.sqft != null) specs.push(`${{formatNumber(u.sqft)}} sqft`);
        const specsLine = specs.length ? `${{specs.join(' / ')}}<br>` : '';
        const qualityLine = (u.quality_rating != null) ? `${{qualityStarsHtml(u)}}<br>` : '';
        const favStar = u.favorite ? '\\u2605 ' : '';
        const notesLine = u.notes_text ? `<div class="popup-notes">${{escapeHtml(u.notes_text)}}</div>` : '';
        const typeLine = `<div class="popup-type">${{typeDot}}${{typeLabel}}</div>`;

        m.bindPopup(`<b>${{favStar}}${{escapeHtml(u.title || '')}}</b>${{addressLine}}${{typeLine}}${{photoHtml}}$${{formatNumber(u.price)}}/mo<br>${{specsLine}}${{qualityLine}}${{notesLine}}<a href="${{href}}" target="_blank">Details</a>`);

        // Hover tooltip: compact title + address (no photo — photo is in popup)
        m.bindTooltip(
          `<div class="unit-tooltip">${{typeDot}}<div><b>${{escapeHtml(u.title || '')}}</b><div class="tooltip-header-address">${{escapeHtml(address)}}</div></div></div>`,
          {{ direction: 'top', offset: [0, -10], className: 'unit-tooltip-wrapper' }}
        );
        markers.addLayer(m);
        unitMarkers[u.id] = m;
      }});
    }}

    // Highlight the marker for `id` and dim all others, so hovering a row in
    // the list shows which dot on the map it corresponds to. Does NOT open the
    // tooltip — that fires naturally on map-hover and closes on map-mouseout.
    var _highlightedClusterEl = null;
    var _highlightedTooltip = null;

    function highlightMapMarker(id) {{
      // Clear previous state
      if (_highlightedClusterEl) {{
        _highlightedClusterEl.classList.remove('cluster-highlighted');
        _highlightedClusterEl = null;
      }}
      if (_highlightedTooltip) {{
        _highlightedTooltip.remove();
        _highlightedTooltip = null;
      }}
      const target = unitMarkers[id];
      Object.entries(unitMarkers).forEach(([uid, m]) => {{
        m.closeTooltip();
        if (uid === id) {{
          m.setOpacity(1);
          m.setZIndexOffset(1000);
        }} else {{
          m.setOpacity(0.25);
          m.setZIndexOffset(0);
        }}
      }});
      if (target) {{
        const visible = markers.getVisibleParent(target);
        if (visible && visible !== target) {{
          // Marker is inside a cluster — highlight the bubble
          visible.setOpacity(1);
          visible.setZIndexOffset(1000);
          const el = visible.getElement ? visible.getElement() : null;
          if (el) {{
            el.classList.add('cluster-highlighted');
            _highlightedClusterEl = el;
          }}
          // Show the unit's tooltip content at the cluster's position
          const tooltipContent = target.getTooltip ? target.getTooltip()?.getContent() : null;
          if (tooltipContent) {{
            _highlightedTooltip = L.tooltip({{
              direction: 'top',
              offset: [0, -20],
              className: 'unit-tooltip-wrapper',
              permanent: false,
              sticky: false,
            }})
              .setLatLng(visible.getLatLng())
              .setContent(tooltipContent)
              .addTo(map);
          }}
        }} else if (visible === target) {{
          // Marker is visible directly — open its own tooltip
          target.openTooltip();
        }}
      }}
    }}

    function clearMapMarkerHighlight() {{
      if (_highlightedClusterEl) {{
        _highlightedClusterEl.classList.remove('cluster-highlighted');
        _highlightedClusterEl = null;
      }}
      if (_highlightedTooltip) {{
        _highlightedTooltip.remove();
        _highlightedTooltip = null;
      }}
      Object.values(unitMarkers).forEach(m => {{
        m.closeTooltip();
        m.setOpacity(1);
        m.setZIndexOffset(0);
      }});
    }}

    function renderTypeFilter(units) {{
      const select = document.getElementById('type-select');
      const types = Array.from(new Set(units.map(u => u.housing_type || 'Unknown'))).sort();
      const current = select.value;
      select.innerHTML = '<option value="">All types</option>' +
        types.map(t => `<option value="${{t}}">${{t}}</option>`).join('');
      if (types.includes(current)) select.value = current;
    }}

    function renderMapLegend(units) {{
      const legend = document.getElementById('map-legend');
      const types = Array.from(new Set(units.map(u => u.housing_type || 'Unknown'))).sort();
      let html = types.map(t =>
        `<span class="map-legend-item"><span class="map-legend-dot ${{markerClassForType(t)}}"></span>${{escapeHtml(t)}}</span>`
      ).join('');
      if (workLocations.length) {{
        html += `<span class="map-legend-item"><span class="map-legend-dot marker-work"></span>Work location</span>`;
      }}
      legend.innerHTML = html;
    }}

    function applyTypeFilter() {{
      currentTypeFilter = document.getElementById('type-select').value;
      renderAll(lastData);
    }}

    function renderStats(filteredCount, data) {{
      document.getElementById('stat-total').textContent = filteredCount;
      document.getElementById('stat-scraped').textContent = (data.total_units != null) ? data.total_units : data.units.length;
      document.getElementById('stat-updated').textContent = (data.last_updated || 'N/A').split('T')[0];
    }}

    function updateFieldCounts() {{
      const units = (lastData && lastData.units) || [];
      const counts = {{}};
      counts.amenities = units.filter(u => u.has_washer_dryer || u.is_gated || (u.amenities && u.amenities.length)).length;
      counts.baths = units.filter(u => u.baths != null).length;
      counts.beds = units.filter(u => u.beds != null).length;
      counts.contact = units.filter(u => u.contact_phone || u.contact_email).length;
      counts.details = units.length;
      counts.distance = units.filter(u => u.lat != null && u.lon != null).length;
      counts.flooring = units.filter(u => u.flooring_type).length;
      counts.kitchen = units.filter(u => u.kitchen_style).length;
      counts.mine = units.filter(u => isFavorite(u.id)).length;
      counts.movein = units.filter(u => u.move_in_date).length;
      counts.outdoor = units.filter(u => u.outdoor_space && u.outdoor_space !== 'none').length;
      counts.photo = units.filter(u => u.photos && u.photos.length).length;
      counts.price = units.filter(u => u.price != null).length;
      counts.quality = units.filter(u => u.quality_rating != null).length;
      counts.risk = units.filter(u => u.scam_score != null).length;
      counts.score = scoringActive() ? units.length : 0;
      counts.size = units.filter(u => u.size_impression).length;
      counts.source = units.filter(u => u.source).length;
      counts.sqft = units.filter(u => u.sqft != null).length;
      counts.type = units.filter(u => u.housing_type).length;
      counts.washerDryer = units.filter(u => u.has_washer_dryer).length;
      counts.gated = units.filter(u => u.is_gated).length;
      counts.ageRestricted = units.filter(u => u.age_restriction).length;
      counts.unknownAddress = units.filter(u => !u.address).length;
      counts.scam = units.filter(u => isScam(u.id)).length;
      counts.duplicates = units.filter(u => u.linked_ids && u.linked_ids.length && u.linked_primary && u.linked_primary !== u.id).length;
      document.querySelectorAll('[data-count]').forEach(span => {{
        const key = span.dataset.count;
        const count = counts[key];
        span.textContent = count != null ? '(' + count + ')' : '';
      }});
    }}

    function renderAll(data) {{
      lastData = data;
      let filtered = filterUnits(data.units || []);
      filtered = sortUnits(filtered);
      renderTypeFilter(filtered);
      if (currentTypeFilter) {{
        filtered = filtered.filter(u => (u.housing_type || 'Unknown') === currentTypeFilter);
      }}
      filtered = groupByField(filtered, currentGroupBy);
      const dupAddresses = findDuplicateAddresses(filtered);
      renderStats(filtered.length, data);
      renderMapLegend(filtered);
      renderTable(filtered, (data.units || []).length, dupAddresses);
      renderMap(filtered, dupAddresses);
      updateSortIndicators();
      updateFieldCounts();
    }}

    // ---- Refresh controls ----
    document.getElementById('reload-btn').addEventListener('click', () => location.reload());

    document.getElementById('howto-btn').addEventListener('click', () => {{
      const panel = document.getElementById('howto-panel');
      panel.style.display = (panel.style.display === 'block') ? 'none' : 'block';
    }});

    // ---- Resizable divider between the sidebar (map) and the list panel ----
    (function () {{
      const resizer = document.getElementById('resizer');
      const sidebar = document.querySelector('.sidebar');
      const layout = document.querySelector('.layout');

      const savedWidth = localStorage.getItem('sidebarWidth');
      if (savedWidth) sidebar.style.flexBasis = savedWidth + 'px';

      let dragging = false;

      resizer.addEventListener('mousedown', (e) => {{
        dragging = true;
        resizer.classList.add('resizing');
        document.body.style.cursor = 'col-resize';
        e.preventDefault();
      }});

      document.addEventListener('mousemove', (e) => {{
        if (!dragging) return;
        const rect = layout.getBoundingClientRect();
        let width = e.clientX - rect.left;
        width = Math.max(240, Math.min(width, rect.width - 300));
        sidebar.style.flexBasis = width + 'px';
        requestAnimationFrame(() => map.invalidateSize());
      }});

      document.addEventListener('mouseup', () => {{
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove('resizing');
        document.body.style.cursor = '';
        localStorage.setItem('sidebarWidth', parseInt(sidebar.style.flexBasis, 10));
        map.invalidateSize();
      }});
    }})();

    // ---- Editable "your address" + lease end date ----
    function renderCriteriaText() {{
      const parts = [];
      if (CONFIG.search_radius_miles != null) {{
        parts.push(`within ${{CONFIG.search_radius_miles}} mi of ${{CONFIG.target_name}}`);
      }}
      if (CONFIG.min_price != null || CONFIG.max_price != null) {{
        const lo = (CONFIG.min_price != null) ? `$${{CONFIG.min_price}}` : '$0';
        const hi = (CONFIG.max_price != null) ? `$${{CONFIG.max_price}}` : '+';
        parts.push(`${{lo}}\\u2013${{hi}}/mo`);
      }}
      if (CONFIG.min_beds != null) {{
        let bedsPart = `${{CONFIG.min_beds}}+`;
        if (CONFIG.max_beds != null && CONFIG.max_beds !== CONFIG.min_beds) {{
          bedsPart = `${{CONFIG.min_beds}}\\u2013${{CONFIG.max_beds}}`;
        }}
        parts.push(`${{bedsPart}} bd`);
      }}
      document.getElementById('criteria-text-value').textContent = parts.length ? parts.join(' \\u00b7 ') : 'No filters configured';
    }}

    function renderLeaseStat() {{
      const valueEl = document.getElementById('stat-lease-value');
      const labelEl = document.getElementById('stat-lease-label');
      if (!CONFIG.current_lease_end) {{
        valueEl.textContent = 'N/A';
        labelEl.textContent = 'Your Lease Ends';
        return;
      }}
      const end = new Date(CONFIG.current_lease_end + 'T00:00:00');
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const days = Math.round((end - today) / 86400000);
      valueEl.textContent = end.toLocaleDateString('en-US', {{ month: 'short', day: '2-digit', year: 'numeric' }});
      labelEl.textContent = `Your Lease Ends (${{days}}d)`;
    }}

    const editBtn = document.getElementById('edit-criteria-btn');
    const editPanel = document.getElementById('criteria-edit');
    const addressInput = document.getElementById('address-input');
    const leaseInput = document.getElementById('lease-end-input');
    const radiusInput = document.getElementById('radius-input');
    const minPriceInput = document.getElementById('min-price-input');
    const maxPriceInput = document.getElementById('max-price-input');
    const minBedsInput = document.getElementById('min-beds-input');
    const maxBedsInput = document.getElementById('max-beds-input');
    const minBathsInput = document.getElementById('min-baths-input');
    const minSqftInput = document.getElementById('min-sqft-input');
    const editStatus = document.getElementById('criteria-edit-status');

    function numOrNull(val) {{
      if (val === '' || val == null) return null;
      const n = Number(val);
      return Number.isNaN(n) ? null : n;
    }}

    function populateEditInputs() {{
      addressInput.value = (CONFIG.target_location && CONFIG.target_location.address) || '';
      leaseInput.value = CONFIG.current_lease_end || '';
      radiusInput.value = (CONFIG.search_radius_miles != null) ? CONFIG.search_radius_miles : '';
      minPriceInput.value = (CONFIG.min_price != null) ? CONFIG.min_price : '';
      maxPriceInput.value = (CONFIG.max_price != null) ? CONFIG.max_price : '';
      minBedsInput.value = (CONFIG.min_beds != null) ? CONFIG.min_beds : '';
      maxBedsInput.value = (CONFIG.max_beds != null) ? CONFIG.max_beds : '';
      minBathsInput.value = (CONFIG.min_baths != null) ? CONFIG.min_baths : '';
      minSqftInput.value = (CONFIG.min_sqft != null) ? CONFIG.min_sqft : '';
    }}
    populateEditInputs();

    editBtn.addEventListener('click', () => {{
      editPanel.classList.toggle('open');
    }});

    function toggleToolPanel(panelId, btnId) {{
      const panel = document.getElementById(panelId);
      const btn = document.getElementById(btnId);
      const willOpen = !panel.classList.contains('open');
      document.querySelectorAll('.tools-panel.open').forEach(p => p.classList.remove('open'));
      document.querySelectorAll('.tool-btn.open').forEach(b => b.classList.remove('open'));
      if (willOpen) {{
        panel.classList.add('open');
        btn.classList.add('open');
      }}
    }}

    document.getElementById('list-filters-btn').addEventListener('click', () => {{
      toggleToolPanel('list-filters-panel', 'list-filters-btn');
    }});

    document.getElementById('clear-list-filters-btn').addEventListener('click', () => {{
      clearListFilters();
    }});

    document.getElementById('columns-btn').addEventListener('click', () => {{
      toggleToolPanel('columns-panel', 'columns-btn');
    }});

    document.getElementById('reset-columns-btn').addEventListener('click', () => {{
      resetColumnPrefs();
    }});

    document.getElementById('work-locations-btn').addEventListener('click', () => {{
      toggleToolPanel('work-locations-panel', 'work-locations-btn');
    }});

    document.getElementById('ranking-btn').addEventListener('click', () => {{
      toggleToolPanel('ranking-panel', 'ranking-btn');
    }});

    document.getElementById('clear-ranking-btn').addEventListener('click', () => {{
      clearScoringCriteria();
    }});

    document.getElementById('add-work-location-btn').addEventListener('click', async () => {{
      const nameInput = document.getElementById('work-loc-name-input');
      const addressInput = document.getElementById('work-loc-address-input');
      const hoursStartInput = document.getElementById('work-loc-hours-start');
      const hoursEndInput = document.getElementById('work-loc-hours-end');
      const maxCommuteInput = document.getElementById('work-loc-max-commute');
      const status = document.getElementById('work-location-status');
      const addBtn = document.getElementById('add-work-location-btn');

      const name = nameInput.value.trim();
      const address = addressInput.value.trim();
      if (!name || !address) {{
        status.textContent = 'Enter a name and address.';
        return;
      }}

      const hoursStart = hoursStartInput.value || '09:00';
      const hoursEnd = hoursEndInput.value || '17:00';
      const maxCommute = maxCommuteInput.value ? parseInt(maxCommuteInput.value, 10) : null;

      addBtn.disabled = true;
      const isEdit = editingWorkLocationIndex >= 0;
      const existingLoc = isEdit ? workLocations[editingWorkLocationIndex] : null;
      const addressChanged = !isEdit || address !== existingLoc.address;

      status.textContent = addressChanged ? 'Looking up address\\u2026' : 'Saving\\u2026';
      try {{
        const coords = addressChanged ? await geocodeAddress(address) : {{ lat: existingLoc.lat, lon: existingLoc.lon }};
        const entry = {{ name, address, lat: coords.lat, lon: coords.lon, hours_start: hoursStart, hours_end: hoursEnd, max_commute_min: maxCommute }};
        if (isEdit) {{
          workLocations[editingWorkLocationIndex] = entry;
        }} else {{
          workLocations.push(entry);
        }}
        saveWorkLocations();
        if (addressChanged) drivingDistCache = {{}};
        renderWorkLocationsList();
        renderWorkLocationHeaders();
        renderWorkLocationMarkers();
        renderWorkDistanceFilters();
        renderListFiltersSummary();
        cancelWorkLocationEdit();
        const verb = isEdit ? 'Updated' : 'Added';
        if (addressChanged) {{
          status.textContent = `\\u2713 ${{verb}} \\u2014 fetching drive distances\\u2026`;
          renderAll(lastData);
          fetchDrivingDistances().then(() => {{
            status.textContent = `\\u2713 Drive distances loaded`;
            setTimeout(() => {{ status.textContent = ''; }}, 2000);
          }});
        }} else {{
          status.textContent = `\\u2713 ${{verb}}`;
          renderAll(lastData);
          setTimeout(() => {{ status.textContent = ''; }}, 2000);
        }}
      }} catch (e) {{
        console.error('Geocoding failed:', e);
        status.textContent = '\\u2717 ' + e.message;
      }}
      addBtn.disabled = false;
    }});

    document.getElementById('cancel-criteria-btn').addEventListener('click', () => {{
      populateEditInputs();
      editStatus.textContent = '';
      editPanel.classList.remove('open');
    }});

    document.getElementById('reset-criteria-btn').addEventListener('click', () => {{
      try {{ localStorage.removeItem('configOverrides'); }} catch (e) {{}}
      location.reload();
    }});

    document.getElementById('save-criteria-btn').addEventListener('click', async () => {{
      const saveBtn = document.getElementById('save-criteria-btn');
      const newAddress = addressInput.value.trim();

      CONFIG.current_lease_end = leaseInput.value || null;
      CONFIG.search_radius_miles = numOrNull(radiusInput.value);
      CONFIG.min_price = numOrNull(minPriceInput.value);
      CONFIG.max_price = numOrNull(maxPriceInput.value);
      CONFIG.min_beds = numOrNull(minBedsInput.value);
      CONFIG.max_beds = numOrNull(maxBedsInput.value);
      CONFIG.min_baths = numOrNull(minBathsInput.value);
      CONFIG.min_sqft = numOrNull(minSqftInput.value);
      renderLeaseStat();

      const currentAddress = (CONFIG.target_location && CONFIG.target_location.address) || '';
      let geocodeFailed = false;
      if (newAddress && newAddress !== currentAddress) {{
        saveBtn.disabled = true;
        editStatus.textContent = 'Looking up address\\u2026';
        try {{
          const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${{encodeURIComponent(newAddress)}}`;
          const resp = await fetch(url);
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const results = await resp.json();
          if (!results.length) throw new Error('Address not found');
          CONFIG.target_location = {{
            name: newAddress,
            address: newAddress,
            lat: parseFloat(results[0].lat),
            lon: parseFloat(results[0].lon)
          }};
          CONFIG.target_name = newAddress;
        }} catch (e) {{
          console.error('Geocoding failed:', e);
          editStatus.textContent = '\\u2717 ' + e.message;
          geocodeFailed = true;
        }}
        saveBtn.disabled = false;
      }}

      updateMapCenter();
      renderCriteriaText();
      saveOverrides();
      renderAll(lastData);

      if (!geocodeFailed) {{
        editStatus.textContent = '\\u2713 Saved';
        setTimeout(() => {{
          editStatus.textContent = '';
          editPanel.classList.remove('open');
        }}, 1200);
      }}
    }});

    // ---- Lightbox / gallery ----
    var currentPhotos = [];
    var currentIndex = 0;

    function openGallery(el) {{
      currentPhotos = JSON.parse(el.dataset.photos);
      currentIndex = parseInt(el.dataset.index, 10);
      showLightboxImage();
      document.getElementById('lightbox-overlay').style.display = 'flex';
    }}

    function showLightboxImage() {{
      document.getElementById('lightbox-img').src = currentPhotos[currentIndex];
      var multi = currentPhotos.length > 1;
      var counter = document.getElementById('lightbox-counter');
      counter.style.display = multi ? 'block' : 'none';
      counter.textContent = (currentIndex + 1) + ' / ' + currentPhotos.length;
      document.querySelectorAll('.lightbox-nav').forEach(function (btn) {{
        btn.style.display = multi ? 'flex' : 'none';
      }});
    }}

    function lightboxPrev(e) {{
      e.stopPropagation();
      currentIndex = (currentIndex - 1 + currentPhotos.length) % currentPhotos.length;
      showLightboxImage();
    }}

    function lightboxNext(e) {{
      e.stopPropagation();
      currentIndex = (currentIndex + 1) % currentPhotos.length;
      showLightboxImage();
    }}

    function closeLightbox(e) {{
      if (e && e.target.id !== 'lightbox-overlay') return;
      document.getElementById('lightbox-overlay').style.display = 'none';
    }}

    document.addEventListener('keydown', function (e) {{
      var overlay = document.getElementById('lightbox-overlay');
      if (overlay.style.display !== 'flex') return;
      if (e.key === 'Escape') closeLightbox();
      else if (e.key === 'ArrowLeft') lightboxPrev(e);
      else if (e.key === 'ArrowRight') lightboxNext(e);
    }});

    // ---- Initial render ----
    if (currentMinQuality != null) {{
      document.getElementById('quality-select').value = String(currentMinQuality);
    }}
    renderCriteriaText();
    renderLeaseStat();
    document.querySelectorAll('#columns-panel input[data-col]').forEach(cb => {{
      cb.checked = isColumnVisible(cb.dataset.col);
    }});
    applyColumnPrefs();
    restoreGroupByCheckboxes();
    document.getElementById('groups-collapsed-cb').checked = groupsCollapsedByDefault;
    renderWorkLocationsList();
    renderWorkLocationHeaders();
    renderWorkLocationMarkers();
    renderWorkDistanceFilters();
    loadSavedListFilters();
    populateListFilterDom();
    renderListFiltersSummary();
    populateRankingPanel();
    renderRankingSummary();
    renderAll(INITIAL_DATA);
    if (workLocations.length) fetchDrivingDistances();
  </script>

  <!-- Scrape Activity Modal -->
  <div class="scrape-overlay" id="scrape-overlay" onclick="if(event.target===this)closeScrapeStatus()">
    <div class="scrape-modal" id="scrape-modal"></div>
  </div>
  <script>
  var SCRAPE_LOG = {scrape_log_json};
  function openScrapeStatus() {{
    var el = document.getElementById('scrape-modal');
    var summary = SCRAPE_LOG.summary || {{}};
    var runs = SCRAPE_LOG.runs || [];
    var totalUnits = SCRAPE_LOG.total_units || 0;
    var html = '<h2>Scrape Activity <button class="close-x" onclick="closeScrapeStatus()">&times;</button></h2>';

    var grandTotalFound = 0, grandTotalAdded = 0, grandTotalPhotos = 0, grandTotalRuns = 0;
    var sources = Object.keys(summary);
    sources.forEach(function(src) {{
      var s = summary[src];
      grandTotalFound += s.total_found || 0;
      grandTotalAdded += s.total_added || 0;
      grandTotalPhotos += s.total_photos || 0;
      grandTotalRuns += s.total_runs || 0;
    }});

    html += '<div class="scrape-totals-bar">' +
      '<div class="scrape-total-item"><span class="scrape-total-value">' + totalUnits + '</span><span class="scrape-total-label">Units in DB</span></div>' +
      '<div class="scrape-total-item"><span class="scrape-total-value">' + grandTotalFound + '</span><span class="scrape-total-label">Found</span></div>' +
      '<div class="scrape-total-item"><span class="scrape-total-value">' + grandTotalAdded + '</span><span class="scrape-total-label">Added</span></div>' +
      '<div class="scrape-total-item"><span class="scrape-total-value">' + grandTotalPhotos + '</span><span class="scrape-total-label">Photos</span></div>' +
      '<div class="scrape-total-item"><span class="scrape-total-value">' + grandTotalRuns + '</span><span class="scrape-total-label">Runs</span></div>' +
      '</div>';

    if (sources.length) {{
      html += '<div class="scrape-section-title">Source Totals</div>';
      html += '<div class="scrape-summary-grid">';
      sources.sort().forEach(function(src) {{
        var s = summary[src];
        var lastRun = s.last_run ? s.last_run.substring(0, 16).replace('T', ' ') : 'never';
        var cardClass = s.total_runs > 0 ? 'scrape-source-card' : 'scrape-source-card scrape-source-inactive';
        html += '<div class="' + cardClass + '">' +
          '<div class="scrape-source-name">' + src.replace(/_/g, ' ') + '</div>' +
          '<dl class="scrape-source-stats">' +
            '<dt>Runs</dt><dd>' + s.total_runs + '</dd>' +
            '<dt>Found</dt><dd>' + s.total_found + '</dd>' +
            '<dt>Added</dt><dd>' + s.total_added + '</dd>' +
            '<dt>Photos</dt><dd>' + s.total_photos + '</dd>' +
            '<dt>Captchas</dt><dd>' + s.total_captchas + '</dd>' +
            '<dt>Scans</dt><dd>' + s.total_image_scans + '</dd>' +
            '<dt>Errors</dt><dd>' + s.total_errors + '</dd>' +
            '<dt>Last run</dt><dd>' + lastRun + '</dd>' +
          '</dl></div>';
      }});
      html += '</div>';
    }}

    var recent = runs.slice(-15).reverse();
    if (recent.length) {{
      html += '<div class="scrape-section-title">Recent Runs (' + recent.length + ')</div>';
      html += '<table class="scrape-run-table"><thead><tr>' +
        '<th>Time</th><th>Source</th><th>Status</th><th>Found</th><th>Added</th><th>Photos</th><th>Dur</th><th>Errors</th>' +
        '</tr></thead><tbody>';
      recent.forEach(function(r) {{
        var ts = (r.started || '').substring(0, 16).replace('T', ' ');
        var errs = (r.errors || []).length;
        html += '<tr>' +
          '<td>' + ts + '</td>' +
          '<td style="text-transform:capitalize">' + (r.source || '').replace(/_/g, ' ') + '</td>' +
          '<td><span class="scrape-run-status ' + (r.status || '') + '"></span>' + (r.status || '') + '</td>' +
          '<td>' + (r.listings_found || 0) + '</td>' +
          '<td>' + (r.listings_added || 0) + '</td>' +
          '<td>' + (r.photos_downloaded || 0) + '</td>' +
          '<td>' + (r.duration_seconds || 0) + 's</td>' +
          '<td>' + (errs || '—') + '</td>' +
          '</tr>';
      }});
      html += '</tbody></table>';
    }}

    if (!sources.length && !runs.length) {{
      html += '<div class="scrape-empty">No scraping activity recorded yet.</div>';
    }}

    el.innerHTML = html;
    document.getElementById('scrape-overlay').classList.add('open');
  }}
  function closeScrapeStatus() {{
    document.getElementById('scrape-overlay').classList.remove('open');
  }}
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape' && document.getElementById('scrape-overlay').classList.contains('open')) {{
      closeScrapeStatus();
    }}
  }});
  </script>
</body>
</html>
'''

    SUMMARY_HTML.write_text(html_content, encoding='utf-8')
    print(f"✓ Generated {SUMMARY_HTML}")
    print(f"  Units within {config.get('search_radius_miles', '?')} mi: {total}")
    print(f"  Total units in database: {units_data.get('total_units', 0)}")
    print(f"  Last updated: {units_data.get('last_updated', 'N/A')}")




_URL_RE = re.compile(r'(?:https?://|www\.)[^\s<>"\']+')
_BULLET_RE = re.compile(r'^\s*[-*•‣›▸→·]\s+|^\s*\d+[.)]\s+')
# Section header: short line ending in ':', e.g. "Features:", "Community Info:"
_HEADER_RE = re.compile(r'^([A-Za-z&][^:\n]{1,58}):\s*$')
# Key-value pair on a single line, e.g. "Lease Length: One Year"
_KV_RE = re.compile(r'^([A-Za-z&][^:\n]{1,50}):\s+(.+)$')
# Pipe-separated spec line, e.g. "2 Bed | 2 Bath | 800 sq ft | Balcony"
_PIPE_RE = re.compile(r'^[^|]+(?:\|[^|]+){2,}$')
# Noise lines to strip from descriptions
_NOISE_LINE_RES = [
    re.compile(r'^QR Code Link to This Post$', re.I),
    re.compile(r'^show contact info$', re.I),
    re.compile(r'^Equal Housing Opportunity$', re.I),
    re.compile(r'^[A-Z][A-Za-z0-9]{7,}$'),  # bare tracking codes like B2V2AyWCTBfZ
    re.compile(r'^OR\s+Text$', re.I),
    re.compile(r'^x\s*\d+$', re.I),  # extension fragments like "x 165"
    re.compile(r'^:$'),               # lone colon separator from portal attribute blocks
    re.compile(r'^Interested\?$', re.I),
    re.compile(r'^Fill out this form', re.I),
    re.compile(r'^Have questions', re.I),
    re.compile(r'^Contact the (landlord|owner|manager|property)', re.I),
    re.compile(r'^\d[\d,]*\.?\d*\s*/\s*Month', re.I),   # "1950.00 / Month"
    re.compile(r'^\d[\d,]*\.?\d*\s+Deposit$', re.I),     # "1950.00 Deposit"
]
_CALL_NOW_RE = re.compile(r'^Call\s+Now\b', re.I)
_CALL_BLOCK_END_RE = re.compile(r'to\s+text\s+with\s+us|text\s+us\.?$', re.I)
_LINK_PROMPT_RE = re.compile(r'follow|click here|more info|information|this link', re.I)

def _title_value(s):
    """Lightly normalize portal attribute values like OUTSIDE_ONLY → Outside Only."""
    if s == s.upper() and '_' in s:
        return s.replace('_', ' ').title()
    return s


def extract_description_extras(notes):
    """Strip boilerplate noise from description text and pull out embedded URLs.
    Returns (cleaned_text, links) where links = [(label, url), ...]."""
    if not notes:
        return notes, []

    # Pre-pass: merge portal-style "key\n: value" or "key\n:" split across two lines
    raw_lines = notes.splitlines()
    merged = []
    i = 0
    while i < len(raw_lines):
        cur = raw_lines[i]
        cur_s = cur.strip()
        if cur_s and i + 1 < len(raw_lines):
            nxt = raw_lines[i + 1].strip()
            if nxt.startswith(':') and not cur_s.endswith(':'):
                val = nxt[1:].strip()
                merged.append(cur_s + (':' if not val else f': {val}'))
                i += 2
                continue
        merged.append(cur)
        i += 1
    lines = merged
    out = []
    links = []
    in_call_block = False

    for line in lines:
        s = line.strip()
        if not s:
            out.append('')
            continue

        # "Call Now -" through "to text with us." is Craigslist's obfuscated phone UI
        if _CALL_NOW_RE.match(s):
            in_call_block = True
            continue
        if in_call_block:
            if _CALL_BLOCK_END_RE.search(s):
                in_call_block = False
            continue

        # Strip known noise lines
        if any(r.match(s) for r in _NOISE_LINE_RES):
            continue

        # Standalone URL line: either inline it or extract as a chip button
        if _URL_RE.match(s) and not _URL_RE.sub('', s).strip():
            raw_url = s.rstrip('.,;:)')
            url = raw_url if raw_url.startswith('http') else 'https://' + raw_url
            # Find the last non-blank preceding line
            prev_line = ''
            prev_idx = -1
            for j in range(len(out) - 1, -1, -1):
                if out[j].strip():
                    prev_line = out[j].strip()
                    prev_idx = j
                    break
            # If the previous line is a mid-sentence lead-in (no terminal punct, no colon),
            # append the URL inline so "visit us at\nhttps://..." stays readable in context
            if prev_line and prev_line[-1] not in '.:!?':
                out[prev_idx] = out[prev_idx].rstrip() + ' ' + url
                continue
            # Otherwise extract as a chip — infer label from context
            label = 'More Info'
            ps_low = prev_line.lower()
            if 'apply' in ps_low:
                label = 'Apply'
            elif 'tour' in ps_low or 'schedule' in ps_low:
                label = 'Schedule Tour'
            elif 'floor plan' in ps_low:
                label = 'Floor Plan'
            links.append((label, url))
            # Strip a preceding "Follow this link:" / "Want more info?" prompt
            for j in range(len(out) - 1, -1, -1):
                if out[j].strip() and _LINK_PROMPT_RE.search(out[j]):
                    out[j] = ''
                    break
                if out[j].strip():
                    break
            continue

        out.append(line)

    cleaned = re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip()
    return cleaned, links


def format_description(text):
    """Convert cleaned listing text to rich HTML: section headings, bullet lists, paragraphs."""
    if not text:
        return ''

    def linkify(s):
        parts = _URL_RE.split(s)
        urls = _URL_RE.findall(s)
        result = html_escape(parts[0])
        for url, tail in zip(urls, parts[1:]):
            raw = url.rstrip('.,;:!?)')
            href = raw if raw.startswith('http') else 'https://' + raw
            u_esc = html_escape(raw)
            result += f'<a href="{html_escape(href)}" target="_blank" rel="noopener">{u_esc}</a>'
            result += html_escape(tail)
        return result

    html_parts = []
    in_list = False
    in_kv = False       # inside a <dl> key-value block
    para_lines = []
    after_header = False

    def flush_para():
        nonlocal para_lines
        if para_lines:
            html_parts.append('<p>' + '<br>'.join(para_lines) + '</p>')
            para_lines = []

    def close_list():
        nonlocal in_list, after_header
        if in_list:
            html_parts.append('</ul>')
            in_list = False
        after_header = False

    def close_kv():
        nonlocal in_kv
        if in_kv:
            html_parts.append('</dl>')
            in_kv = False

    for line in text.splitlines():
        s = line.strip()
        if not s:
            if in_list:
                close_list()
            close_kv()
            flush_para()
            continue

        # Pipe-separated spec line → render as chip badges
        if _PIPE_RE.match(s):
            close_list()
            close_kv()
            flush_para()
            chips = [html_escape(c.strip()) for c in s.split('|') if c.strip()]
            html_parts.append(
                '<div class="spec-chips">' +
                ''.join(f'<span class="spec-chip">{c}</span>' for c in chips) +
                '</div>'
            )
            after_header = False
            continue

        # Section header: line ending with ':' and no mid-sentence punct before it
        m = _HEADER_RE.match(s)
        if m and not re.search(r'[.!?]', s[:-1]):
            close_list()
            close_kv()
            flush_para()
            html_parts.append(f'<h3 class="desc-heading">{html_escape(m.group(1).strip())}</h3>')
            after_header = True
            continue

        # Key-value pair: "Label: Value" on one line (not ending with ':')
        kv = _KV_RE.match(s)
        if kv and not re.search(r'[.!?]', kv.group(1)):
            close_list()
            flush_para()
            if not in_kv:
                html_parts.append('<dl class="desc-kv">')
                in_kv = True
            k = html_escape(kv.group(1).strip())
            v = html_escape(_title_value(kv.group(2).strip()))
            html_parts.append(f'<div class="desc-kv-row"><dt>{k}</dt><dd>{v}</dd></div>')
            after_header = False
            continue

        # Explicit bullet marker
        if _BULLET_RE.match(s):
            close_kv()
            flush_para()
            if not in_list:
                html_parts.append('<ul class="desc-list">')
                in_list = True
            item = _BULLET_RE.sub('', s).strip()
            html_parts.append(f'<li>{linkify(item)}</li>')
            after_header = False
            continue

        # Implicit list item after a section header
        is_sentence = s[-1] in '.!?' if s else False
        if after_header and len(s) <= 60 and not re.search(r'[.!?]', s[:-1]) and not is_sentence:
            close_kv()
            flush_para()
            if not in_list:
                html_parts.append('<ul class="desc-list">')
                in_list = True
            html_parts.append(f'<li>{linkify(s)}</li>')
            continue

        # Regular paragraph line
        close_list()
        close_kv()
        after_header = False
        para_lines.append(linkify(s))

    if in_list:
        html_parts.append('</ul>')
    if in_kv:
        html_parts.append('</dl>')
    flush_para()
    return ''.join(html_parts)


DETAIL_PAGE_CSS = '''
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');

    :root {
      --bg:        #0f1117;
      --surface:   #181c27;
      --border:    #252a38;
      --accent:    #4fd1c5;
      --accent2:   #f6ad55;
      --muted:     #4a5168;
      --text:      #e2e8f0;
      --subtext:   #8892a4;
      --red:       #fc8181;
      --purple:    #b794f4;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'DM Sans', sans-serif;
      font-size: 15px;
      line-height: 1.6;
      padding: 24px;
      max-width: 960px;
      margin: 0 auto;
    }

    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }

    .back-link {
      display: inline-block;
      margin-bottom: 16px;
      font-size: 13px;
      color: var(--subtext);
    }

    h1 {
      font-size: 22px;
      font-weight: 500;
      margin-bottom: 4px;
    }

    .address {
      color: var(--subtext);
      margin-bottom: 16px;
    }

    .detail-map {
      height: 280px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--surface);
    }

    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 24px;
    }

    .badge {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 6px 12px;
      font-size: 13px;
      color: var(--subtext);
    }

    .badge.price {
      color: var(--accent2);
      font-weight: 600;
      font-family: 'DM Mono', monospace;
    }

    .badge.quality {
      color: var(--accent2);
      letter-spacing: 1px;
    }

    .badge.age-restricted {
      background: var(--red);
      color: var(--bg);
      border-color: var(--red);
      font-weight: 600;
    }

    .info-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }

    .info-item {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
    }

    .info-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--subtext);
      margin-bottom: 4px;
    }

    .info-value {
      font-size: 15px;
    }

    .section {
      margin-bottom: 24px;
    }

    .section h2 {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--subtext);
      margin-bottom: 8px;
    }

    .photos {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 8px;
    }

    .photos img {
      width: 100%;
      height: 140px;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--border);
      display: block;
      cursor: pointer;
    }

    .your-notes {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
    }

    .fav-toggle-btn {
      background: var(--bg);
      color: var(--subtext);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 6px 12px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
      cursor: pointer;
      margin-bottom: 10px;
    }

    .fav-toggle-btn.active {
      color: var(--accent2);
      border-color: var(--accent2);
    }

    .your-notes textarea {
      display: block;
      width: 100%;
      min-height: 100px;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 14px;
      resize: vertical;
      margin-bottom: 10px;
    }

    .notes-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .notes-actions .save-btn {
      background: var(--accent);
      color: var(--bg);
      border: none;
      border-radius: 4px;
      padding: 6px 14px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
    }

    .notes-actions .save-btn:hover {
      opacity: 0.9;
    }

    .saved-msg {
      font-size: 12px;
      color: var(--accent);
    }

    .timeline-section {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
    }

    .timeline-entries {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 12px;
    }

    .timeline-entry {
      display: flex;
      gap: 10px;
      align-items: flex-start;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 13px;
    }

    .timeline-entry-date {
      font-family: 'DM Mono', monospace;
      font-size: 11px;
      color: var(--accent);
      white-space: nowrap;
      padding-top: 1px;
    }

    .timeline-entry-text {
      flex: 1;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .timeline-entry-remove {
      background: transparent;
      border: none;
      color: var(--subtext);
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      padding: 0 2px;
    }

    .timeline-entry-remove:hover { color: var(--red); }

    .timeline-empty {
      font-size: 12px;
      color: var(--subtext);
      font-style: italic;
      margin-bottom: 12px;
    }

    .timeline-add-row {
      display: flex;
      gap: 8px;
    }

    .timeline-add-row input[type="date"] {
      flex: 0 0 130px;
    }

    .timeline-add-row input[type="text"] {
      flex: 1;
      min-width: 120px;
    }

    .timeline-add-row select {
      flex: 0 0 auto;
    }

    .timeline-add-row input, .timeline-add-row select {
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 8px 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
    }

    .timeline-add-row button {
      background: var(--accent);
      color: var(--bg);
      border: none;
      border-radius: 4px;
      padding: 8px 14px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
    }

    .contact-row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
    }

    .contact-chip {
      display: flex;
      flex-direction: column;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 14px;
      min-width: 150px;
    }

    .contact-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--subtext);
      margin-bottom: 3px;
    }

    .contact-value {
      color: var(--accent);
      text-decoration: none;
      font-size: 14px;
    }

    .contact-value:hover { text-decoration: underline; }

    .compose-btn {
      background: var(--accent);
      color: var(--bg);
      border: none;
      border-radius: 6px;
      padding: 10px 16px;
      font-family: 'DM Sans', sans-serif;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
    }

    .compose-btn:hover { opacity: 0.9; }

    .contact-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.6);
      z-index: 1000;
      align-items: center;
      justify-content: center;
    }

    .contact-overlay.open { display: flex; }

    .contact-modal {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      width: min(560px, 92vw);
      max-height: 90vh;
      overflow-y: auto;
      position: relative;
    }

    .contact-modal h3 {
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 12px;
      color: var(--text);
    }

    .contact-modal-close {
      position: absolute;
      top: 14px;
      right: 16px;
      background: transparent;
      border: none;
      color: var(--subtext);
      font-size: 20px;
      cursor: pointer;
      line-height: 1;
    }

    .contact-modal-close:hover { color: var(--text); }

    .contact-modal textarea {
      width: 100%;
      min-height: 160px;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 12px;
      font-family: 'DM Sans', sans-serif;
      font-size: 14px;
      resize: vertical;
      margin-bottom: 14px;
      box-sizing: border-box;
    }

    .contact-modal-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .contact-modal-actions button {
      border: none;
      border-radius: 6px;
      padding: 8px 14px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }

    .btn-email { background: var(--accent); color: var(--bg); }
    .btn-copy-call { background: var(--surface); color: var(--text); border: 1px solid var(--border) !important; }
    .btn-log { background: transparent; color: var(--subtext); border: 1px solid var(--border) !important; }

    .description {
      font-size: 14px;
      color: var(--text);
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
      line-height: 1.65;
    }

    .description p { margin: 0 0 10px; }
    .description p:last-child { margin-bottom: 0; }
    .description a { color: var(--accent); word-break: break-all; }

    .desc-heading {
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
      margin: 14px 0 6px;
      padding-bottom: 4px;
      border-bottom: 1px solid var(--border);
    }
    .desc-heading:first-child { margin-top: 0; }

    .desc-list {
      margin: 0 0 10px 0;
      padding: 0 0 0 18px;
      columns: 160px 3;
      column-gap: 24px;
    }

    .desc-list li {
      margin-bottom: 3px;
      break-inside: avoid;
    }

    .spec-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 10px 0;
    }
    .spec-chip {
      background: rgba(79,209,197,0.1);
      border: 1px solid rgba(79,209,197,0.3);
      color: var(--text);
      border-radius: 4px;
      padding: 3px 9px;
      font-size: 12px;
      white-space: nowrap;
    }

    dl.desc-kv {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 0;
      margin: 8px 0 12px;
      font-size: 13px;
    }
    .desc-kv-row {
      display: contents;
    }
    .desc-kv-row dt {
      color: var(--subtext);
      font-weight: 500;
      padding: 3px 14px 3px 0;
      white-space: nowrap;
    }
    .desc-kv-row dd {
      color: var(--text);
      padding: 3px 0;
      margin: 0;
    }
    .desc-kv-row:not(:last-child) dt,
    .desc-kv-row:not(:last-child) dd {
      border-bottom: 1px solid rgba(255,255,255,0.05);
    }

    .desc-links {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .desc-link-btn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: var(--surface);
      border: 1px solid var(--accent);
      color: var(--accent);
      border-radius: 6px;
      padding: 5px 12px;
      font-size: 13px;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
    }
    .desc-link-btn:hover { background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }

    .scam-report {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
    }
    .scam-score-header {
      display: flex;
      align-items: baseline;
      gap: 10px;
      margin-bottom: 8px;
    }
    .scam-score-value {
      font-family: 'DM Mono', monospace;
      font-size: 28px;
      font-weight: 600;
      line-height: 1;
    }
    .scam-score-label {
      font-size: 13px;
      color: var(--subtext);
    }
    .scam-score-bar-bg {
      height: 6px;
      background: var(--bg);
      border-radius: 3px;
      margin-bottom: 14px;
      overflow: hidden;
    }
    .scam-score-bar {
      height: 100%;
      border-radius: 3px;
      transition: width 0.3s;
    }
    .scam-factors {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .scam-factor {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      font-size: 13px;
      padding: 4px 0 4px 10px;
      border-left: 3px solid var(--border);
    }
    .scam-factor-icon {
      flex-shrink: 0;
      font-weight: 700;
      width: 16px;
      text-align: center;
    }

    .people-section {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
    }
    .people-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
    .person-card {
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 13px;
    }
    .person-card .person-name { font-weight: 500; flex: 1; }
    .person-card .person-role {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--subtext);
      background: var(--surface);
      padding: 2px 8px;
      border-radius: 3px;
    }
    .person-card .person-contact { font-size: 12px; color: var(--accent); }
    .person-card .person-remove {
      background: transparent; border: none; color: var(--subtext);
      cursor: pointer; font-size: 14px; padding: 0 2px;
    }
    .person-card .person-remove:hover { color: var(--red); }
    .people-add-row {
      display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
    }
    .people-add-row input, .people-add-row select {
      background: var(--bg); color: var(--text); border: 1px solid var(--border);
      border-radius: 4px; padding: 7px 10px; font-family: 'DM Sans', sans-serif; font-size: 13px;
    }
    .people-add-row input[type="text"] { flex: 1; min-width: 100px; }
    .people-add-row input[type="tel"], .people-add-row input[type="email"] { flex: 1; min-width: 120px; }
    .people-add-row select { min-width: 90px; }
    .people-add-row button {
      background: var(--accent); color: var(--bg); border: none;
      border-radius: 4px; padding: 7px 14px; font-weight: 600; font-size: 13px; cursor: pointer;
    }
    .people-empty { font-size: 12px; color: var(--subtext); font-style: italic; margin-bottom: 12px; }

    .detail-lightbox {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.88);
      z-index: 9999;
      align-items: center;
      justify-content: center;
      flex-direction: column;
    }
    .detail-lightbox.open { display: flex; }
    .detail-lightbox img {
      max-width: 90vw;
      max-height: 82vh;
      object-fit: contain;
      border-radius: 6px;
    }
    .detail-lb-nav {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      background: rgba(0,0,0,.55);
      border: none;
      color: #fff;
      font-size: 32px;
      padding: 8px 16px;
      cursor: pointer;
      border-radius: 4px;
      z-index: 10000;
    }
    .detail-lb-prev { left: 16px; }
    .detail-lb-next { right: 16px; }
    .detail-lb-counter {
      position: absolute;
      bottom: 16px;
      left: 50%;
      transform: translateX(-50%);
      color: rgba(255,255,255,.75);
      font-size: 13px;
    }
    .detail-lb-close {
      position: absolute;
      top: 14px;
      right: 18px;
      background: none;
      border: none;
      color: #fff;
      font-size: 28px;
      cursor: pointer;
      z-index: 10000;
    }

    .contact-display { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }

    .contact-none {
      font-size: 13px;
      color: var(--subtext);
      font-style: italic;
    }

    .contact-edit-link {
      background: transparent;
      border: none;
      color: var(--accent);
      font-size: 13px;
      cursor: pointer;
      padding: 0;
      text-decoration: underline;
      font-family: 'DM Sans', sans-serif;
    }

    .contact-edit-form {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-top: 10px;
    }

    .contact-edit-form input {
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 7px 10px;
      font-family: 'DM Sans', sans-serif;
      font-size: 13px;
      flex: 1 1 180px;
    }

    .contact-edit-form .save-btn {
      background: var(--accent);
      color: var(--bg);
      border: none;
      border-radius: 4px;
      padding: 7px 14px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
      font-family: 'DM Sans', sans-serif;
    }

    .contact-edit-form .cancel-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--subtext);
      border-radius: 4px;
      padding: 7px 14px;
      font-size: 13px;
      cursor: pointer;
      font-family: 'DM Sans', sans-serif;
    }

    .linked-units-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .linked-unit-card {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 8px 12px;
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 8px;
      text-decoration: none;
      color: inherit;
      transition: background 0.15s;
    }
    .linked-unit-card:hover {
      background: rgba(0,194,168,0.08);
      border-color: var(--accent);
    }
    .linked-unit-id {
      font-family: 'DM Mono', monospace;
      font-size: 12px;
      color: var(--accent);
      min-width: 80px;
    }
    .linked-unit-title {
      flex: 1;
      font-size: 13px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .linked-unit-meta {
      font-size: 12px;
      color: var(--subtext);
      white-space: nowrap;
    }

    @media (max-width: 640px) {
      body {
        padding: 14px;
        font-size: 14px;
      }

      h1 {
        font-size: 18px;
        line-height: 1.3;
      }

      .badges {
        gap: 6px;
      }

      .badge {
        padding: 5px 10px;
        font-size: 12px;
      }

      .info-grid {
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }

      .info-item {
        padding: 10px 12px;
      }

      .info-label {
        font-size: 10px;
      }

      .info-value {
        font-size: 14px;
      }

      .photos {
        grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
        gap: 6px;
      }

      .photos img {
        height: 110px;
      }

      .detail-map {
        height: 220px;
      }

      .detail-lightbox img {
        max-width: 95vw;
        max-height: 85vh;
      }

      .detail-lb-nav {
        font-size: 24px;
        padding: 6px 12px;
      }

      .your-notes textarea {
        min-height: 80px;
        font-size: 14px;
      }

      .section h2 {
        font-size: 11px;
      }

      .contact-modal-content {
        width: 95%;
        max-height: 85vh;
      }

      .linked-units-grid {
        grid-template-columns: 1fr;
      }

      .commute-table th,
      .commute-table td {
        padding: 6px 8px;
        font-size: 12px;
      }

      .description {
        padding: 12px;
        font-size: 13px;
      }
    }
'''


def _build_linked_section(unit, all_units_map):
    """Build an HTML section showing other listings linked to this unit."""
    linked_ids = unit.get('linked_ids', [])
    if not linked_ids or not all_units_map:
        return ''
    is_primary = unit.get('linked_primary') == unit.get('id')
    header_note = ' (this is the primary listing)' if is_primary else ''
    rows = []
    for lid in linked_ids:
        lu = all_units_map.get(lid)
        if not lu:
            continue
        title_text = html_escape(lu.get('title') or lu.get('address') or lid)
        if len(title_text) > 60:
            title_text = title_text[:57] + '&hellip;'
        price_text = f'${lu["price"]:,}' if lu.get('price') else '&mdash;'
        source_text = html_escape(lu.get('source') or '?')
        is_this_primary = lu.get('id') == unit.get('linked_primary')
        primary_tag = ' <span style="color:var(--accent);font-size:11px;">(primary)</span>' if is_this_primary else ''
        rows.append(
            f'<a href="../{html_escape(lid)}/index.html" class="linked-unit-card">'
            f'<span class="linked-unit-id">{html_escape(lid)}{primary_tag}</span>'
            f'<span class="linked-unit-title">{title_text}</span>'
            f'<span class="linked-unit-meta">{price_text} &middot; {source_text}</span>'
            f'</a>'
        )
    return f'''
  <div class="section">
    <h2>\U0001f517 Linked Listings ({len(linked_ids) + 1} total){header_note}</h2>
    <p style="color:var(--subtext);font-size:13px;margin:0 0 8px;">These appear to be the same rental listed multiple times.</p>
    <div class="linked-units-list">{''.join(rows)}</div>
  </div>'''

def generate_unit_detail_html(unit, lease_end=None, target_location=None,
                              scam_result=None, interactions_entry=None,
                              all_units_map=None):
    """Render a dark-themed page showing everything we've scraped for one unit."""
    title = unit.get('title') or unit.get('address') or unit.get('id')
    address = unit.get('address') or 'Address unknown'
    target_lat = target_location['lat'] if target_location else None
    target_lon = target_location['lon'] if target_location else None
    target_name = target_location.get('name') if target_location else None
    price = unit.get('price')
    beds = unit.get('beds')
    baths = unit.get('baths')
    sqft = unit.get('sqft')
    housing_type = unit.get('housing_type') or 'Unknown'
    source = unit.get('source') or 'Unknown source'
    source_url = unit.get('source_url')
    distance = unit.get('distance_miles')
    quality_rating = unit.get('quality_rating')
    quality_notes = unit.get('quality_notes')
    flooring_type = unit.get('flooring_type')
    move_in_date = unit.get('move_in_date')
    age_restriction = unit.get('age_restriction')
    date_added = (unit.get('date_added') or '').split('T')[0] or '—'
    lat = unit.get('lat')
    lon = unit.get('lon')

    def dash_or(v, suffix=''):
        return '—' if v is None or v == '' else f'{v}{suffix}'

    badges = []
    if price is not None:
        badges.append(f'<div class="badge price">${price:,}/mo</div>')
    badges.append(f'<div class="badge">{dash_or(beds)} bd</div>')
    badges.append(f'<div class="badge">{dash_or(baths)} ba</div>')
    sqft_display = '—' if sqft is None else f'{sqft:,}'
    badges.append(f'<div class="badge">{sqft_display} sqft</div>')
    badges.append(f'<div class="badge">{html_escape(housing_type)}</div>')
    if age_restriction:
        badges.append(f'<div class="badge age-restricted">{age_restriction}+ Community</div>')
    if distance is not None:
        badges.append(f'<div class="badge">{distance} mi away</div>')
    if quality_rating:
        rating_text = f'{quality_rating:.1f} ★'
        title_attr = f' title="{html_escape(quality_notes)}"' if quality_notes else ''
        badges.append(f'<div class="badge quality"{title_attr}>{rating_text} quality</div>')
    if flooring_type and flooring_type != 'unknown':
        badges.append(f'<div class="badge">{html_escape(flooring_type.capitalize())} flooring</div>')
    _VISION_LABELS = {
        'kitchen_style': {'modern': '🍳 Modern kitchen', 'updated': '🍳 Updated kitchen', 'dated': '🍳 Dated kitchen'},
        'outdoor_space': {'balcony': '🪴 Balcony', 'patio': '🪴 Patio', 'yard': '🪴 Yard'},
        'size_impression': {'spacious': '📐 Spacious', 'cramped': '📐 Cramped'},
    }
    for attr, labels in _VISION_LABELS.items():
        val = unit.get(attr)
        if val and val in labels:
            badges.append(f'<div class="badge">{html_escape(labels[val])}</div>')
    if move_in_date:
        if move_in_date == 'now':
            badges.append('<div class="badge">Move-in: Now</div>')
        else:
            try:
                d = date.fromisoformat(move_in_date)
                badges.append(f'<div class="badge">Move-in: {d.strftime("%b")} {d.day}, {d.year}</div>')
            except ValueError:
                badges.append(f'<div class="badge">Move-in: {html_escape(move_in_date)}</div>')
    if unit.get('has_washer_dryer'):
        badges.append('<div class="badge">\U0001f9fa Washer/Dryer</div>')
    if unit.get('is_gated'):
        badges.append('<div class="badge">\U0001f510 Gated</div>')
    for amenity in unit.get('amenities') or []:
        badges.append(f'<div class="badge">{html_escape(str(amenity))}</div>')

    if source_url:
        source_value = f'<a href="{html_escape(source_url)}" target="_blank">{html_escape(source)} &#8599;</a>'
    else:
        source_value = html_escape(source)

    info_items = [
        f'<div class="info-item"><div class="info-label">Source</div><div class="info-value">{source_value}</div></div>',
        f'<div class="info-item"><div class="info-label">Date Added</div><div class="info-value">{date_added}</div></div>',
        f'<div class="info-item"><div class="info-label">Unit ID</div><div class="info-value">{html_escape(unit.get("id") or "")}</div></div>',
    ]
    contact_phone = unit.get('contact_phone')
    contact_email = unit.get('contact_email')
    contact_name = unit.get('contact_name')
    if lease_end:
        days_left = (lease_end - datetime.now().date()).days
        info_items.append(
            '<div class="info-item"><div class="info-label">Your Lease Ends</div>'
            f'<div class="info-value">{lease_end.strftime("%b %d, %Y")} ({days_left}d)</div></div>'
        )

    # Pre-filled contact message
    lease_end_for_msg = lease_end.strftime('%b %d, %Y') if lease_end else ''
    price_for_msg = f'${price:,}/month ' if price else ''
    contact_message = (
        f'Hi, I\'m interested in your listing "{title}" at {address}.\n\n'
        f'Is it still available? '
        + (f'Our lease ends {lease_end_for_msg} and ' if lease_end_for_msg else '')
        + f'we\'re looking for a {price_for_msg}apartment and would love to schedule a viewing.\n\nPlease let us know if available and what the next steps are.\n\nThank you!'
    )

    # Extract links + clean description before rendering
    notes = unit.get('notes')
    cleaned_notes, desc_links = extract_description_extras(notes)

    # Contact section — phone/email chips rendered by JS; desc_links injected statically
    desc_links_html = ''
    if desc_links:
        items = ''.join(
            f'<a class="desc-link-btn" href="{html_escape(url)}" target="_blank" rel="noopener">'
            f'&#8599; {html_escape(label)}</a>'
            for label, url in desc_links
        )
        desc_links_html = f'<div class="desc-links">{items}</div>'

    contact_section_html = f'''
  <div class="section contact-section">
    <h2>Contact</h2>
    <div id="contact-display" class="contact-display"></div>{desc_links_html}
    <div id="contact-edit-form" class="contact-edit-form" style="display:none">
      <input type="tel" id="contact-edit-phone" placeholder="Phone number">
      <input type="email" id="contact-edit-email" placeholder="Email address">
      <button class="save-btn" onclick="saveContactInfo()">Save</button>
      <button class="cancel-btn" onclick="toggleContactEdit(false)">Cancel</button>
    </div>
  </div>'''

    description_html = ''
    if cleaned_notes:
        description_html = f'''
  <div class="section">
    <h2>Description (from listing)</h2>
    <div class="description">{format_description(cleaned_notes)}</div>
  </div>'''

    photos_html = ''
    photos = unit.get('photos') or []
    if photos:
        rel_paths = [p.replace('outputs/', '') if 'outputs/' in p else p for p in photos]
        photos_json = json.dumps(['../../' + p for p in rel_paths])
        imgs = ''.join(
            f'<img src="../../{p}" alt="Photo" loading="lazy" '
            f'data-photos=\'{photos_json}\' data-index="{i}" onclick="openDetailGallery(this)">'
            for i, p in enumerate(rel_paths)
        )
        photos_html = f'''
  <div class="section">
    <h2>Photos ({len(photos)})</h2>
    <div class="photos">{imgs}</div>
  </div>'''

    # Build scam risk report HTML
    scam_report_html = ''
    if scam_result:
        level = scam_result.get('level', 'low')
        score = scam_result.get('score', 0)
        level_label = scam_result.get('level_label', '')
        level_colors = {
            'low': '#48bb78', 'moderate': '#ecc94b',
            'high': '#fc8181', 'very_high': '#e53e3e',
        }
        bar_color = level_colors.get(level, '#8892a4')
        bar_width = min(score, 100)

        factors_html = ''
        for f in scam_result.get('factors', []):
            sev = f.get('severity', 'low')
            sev_icons = {'high': '‼', 'medium': '⚠', 'low': '•', 'positive': '✓'}
            sev_colors = {'high': '#fc8181', 'medium': '#ecc94b', 'low': '#8892a4', 'positive': '#48bb78'}
            icon = sev_icons.get(sev, '•')
            color = sev_colors.get(sev, '#8892a4')
            factors_html += (
                f'<div class="scam-factor" style="border-left-color: {color}">'
                f'<span class="scam-factor-icon" style="color: {color}">{icon}</span>'
                f'<span>{html_escape(f.get("text", ""))}</span></div>'
            )

        scam_report_html = f'''
  <div class="section">
    <h2>Scam Risk Analysis</h2>
    <div class="scam-report">
      <div class="scam-score-header">
        <span class="scam-score-value" style="color: {bar_color}">{score}</span>
        <span class="scam-score-label">{html_escape(level_label)}</span>
      </div>
      <div class="scam-score-bar-bg"><div class="scam-score-bar" style="width: {bar_width}%; background: {bar_color}"></div></div>
      <div class="scam-factors">{factors_html}</div>
    </div>
  </div>'''

    # Build People section HTML
    people_section_html = '''
  <div class="section">
    <h2>People</h2>
    <div class="people-section">
      <div id="people-list"></div>
      <div class="people-add-row">
        <input type="text" id="person-name" placeholder="Name">
        <input type="tel" id="person-phone" placeholder="Phone">
        <input type="email" id="person-email" placeholder="Email">
        <select id="person-role">
          <option value="">Role…</option>
          <option value="owner">Owner</option>
          <option value="manager">Manager</option>
          <option value="agent">Agent</option>
          <option value="tenant">Tenant</option>
          <option value="other">Other</option>
        </select>
        <button onclick="addPerson()">Add</button>
      </div>
    </div>
  </div>'''

    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html_escape(unit.get('id') or '')} — {html_escape(title)}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>{DETAIL_PAGE_CSS}</style>
</head>
<body>
  <a class="back-link" href="../../units-summary.html" onclick="if (window.opener || window.history.length > 1) {{ window.close(); return false; }}">&larr; Back to list</a>
  <h1>{html_escape(title)}</h1>
  <div class="address">{html_escape(address)}</div>
  <div class="badges">{''.join(badges)}</div>
  <div class="info-grid">{''.join(info_items)}</div>{contact_section_html}{scam_report_html}{people_section_html}{_build_linked_section(unit, all_units_map)}
  <div class="section">
    <h2>Location</h2>
    <div id="detail-map" class="detail-map"></div>
  </div>{description_html}{photos_html}
  <div class="section">
    <h2>Commute</h2>
    <div id="commute-list" class="info-grid"></div>
  </div>
  <div class="section">
    <h2>Your Notes</h2>
    <div class="your-notes">
      <button id="fav-toggle-btn" class="fav-toggle-btn" onclick="toggleDetailFavorite()">&#9734; Add to favorites</button>
      <textarea id="detail-notes-textarea" placeholder="Add private notes about this unit..."></textarea>
      <div class="notes-actions">
        <button class="save-btn" onclick="saveDetailNotes()">Save Notes</button>
        <span id="notes-saved-msg" class="saved-msg"></span>
      </div>
    </div>
  </div>
  <div class="section">
    <h2>Interaction Timeline</h2>
    <div class="timeline-section">
      <div id="detail-timeline-entries" class="timeline-entries"></div>
      <div class="timeline-add-row">
        <input type="date" id="detail-timeline-date">
        <select id="detail-timeline-type"><option value="note">Note</option><option value="call">Call</option><option value="text">Text</option><option value="email">Email</option><option value="visit">Visit</option><option value="app">App msg</option></select>
        <select id="detail-timeline-direction"><option value="out">Out</option><option value="in">In</option></select>
        <select id="detail-timeline-person"><option value="">Person&hellip;</option></select>
        <input type="text" id="detail-timeline-text" placeholder="What happened...">
        <button onclick="addDetailTimelineEntry()">Add</button>
      </div>
    </div>
  </div>
  <div id="detail-lightbox" class="detail-lightbox" onclick="closeDetailLightbox(event)">
    <button class="detail-lb-close" onclick="closeDetailLightbox()">&times;</button>
    <button class="detail-lb-nav detail-lb-prev" onclick="detailLbPrev(event)">&#8249;</button>
    <img id="detail-lb-img" src="" alt="Full-size photo">
    <button class="detail-lb-nav detail-lb-next" onclick="detailLbNext(event)">&#8250;</button>
    <div class="detail-lb-counter" id="detail-lb-counter"></div>
  </div>
  <div id="contact-overlay" class="contact-overlay" onclick="closeContactOverlay(event)">
    <div class="contact-modal">
      <button class="contact-modal-close" onclick="document.getElementById('contact-overlay').classList.remove('open')">&times;</button>
      <h3>Contact about this property</h3>
      <textarea id="contact-textarea"></textarea>
      <div class="contact-modal-actions">
        <button class="btn-email" id="btn-email" onclick="sendContactEmail()">Send Email</button>
        <button class="btn-copy-call" id="btn-copy-call" onclick="copyAndCall()">Copy &amp; Call</button>
        <button class="btn-log" onclick="logContactOnly()">Log only</button>
      </div>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const UNIT_ID = {json.dumps(unit.get('id') or '')};
    const UNIT_LAT = {json.dumps(lat)};
    const UNIT_LON = {json.dumps(lon)};
    const UNIT_ADDRESS = {json.dumps(address)};
    const TARGET_LAT = {json.dumps(target_lat)};
    const TARGET_LON = {json.dumps(target_lon)};
    const TARGET_NAME = {json.dumps(target_name)};
    const CONTACT_PHONE = {json.dumps(contact_phone)};
    const CONTACT_EMAIL = {json.dumps(contact_email)};
    const CONTACT_NAME = {json.dumps(contact_name)};
    const CONTACT_MESSAGE = {json.dumps(contact_message)};
    const EMBEDDED_CONTACTS = {json.dumps((interactions_entry or {}).get('contacts', []))};
    const EMBEDDED_INTERACTIONS = {json.dumps((interactions_entry or {}).get('interactions', []))};

    function escapeHtml(s) {{
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}

    function haversineMiles(lat1, lon1, lat2, lon2) {{
      const R = 3959;
      const toRad = d => d * Math.PI / 180;
      const dLat = toRad(lat2 - lat1);
      const dLon = toRad(lon2 - lon1);
      const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
      return R * 2 * Math.asin(Math.sqrt(a));
    }}

    function renderCommute() {{
      const list = document.getElementById('commute-list');
      let workLocations = [];
      try {{ workLocations = JSON.parse(localStorage.getItem('workLocations') || '[]'); }} catch (e) {{}}

      if (!workLocations.length) {{
        list.innerHTML = '<div class="info-item" style="grid-column: 1 / -1;"><div class="info-value">Add work locations from the list page to see commute distances here.</div></div>';
        return;
      }}
      if (UNIT_LAT == null || UNIT_LON == null) {{
        list.innerHTML = '<div class="info-item" style="grid-column: 1 / -1;"><div class="info-value">This unit\\'s location is unknown, so commute distance can\\'t be calculated.</div></div>';
        return;
      }}

      list.innerHTML = workLocations.map(loc => {{
        const d = Math.round(haversineMiles(UNIT_LAT, UNIT_LON, loc.lat, loc.lon) * 10) / 10;
        let commuteExtra = '';
        if (loc.commute_to_min || loc.commute_from_min) {{
          const parts = [];
          if (loc.commute_to_min) parts.push(`\\u2192 work: ${{loc.commute_to_min}} min`);
          if (loc.commute_from_min) parts.push(`\\u2190 home: ${{loc.commute_from_min}} min`);
          commuteExtra = `<div style="font-size:12px;color:var(--subtext);">${{parts.join(' &nbsp;|&nbsp; ')}}</div>`;
        }}
        return `<div class="info-item" id="commute-item-${{encodeURIComponent(loc.name)}}">` +
          `<div class="info-label">${{escapeHtml(loc.name)}}</div>` +
          `<div class="info-value">${{d}} mi (straight)<span class="drive-route-placeholder" style="display:block;font-size:12px;color:var(--subtext);">Loading drive\\u2026</span>${{commuteExtra}}</div>` +
          `</div>`;
      }}).join('');

      workLocations.forEach(async (loc, i) => {{
        if (loc.lat == null || loc.lon == null) return;
        try {{
          const url = `https://router.project-osrm.org/route/v1/driving/${{UNIT_LON}},${{UNIT_LAT}};${{loc.lon}},${{loc.lat}}?overview=false`;
          const resp = await fetch(url);
          const data = await resp.json();
          if (data.code === 'Ok' && data.routes.length) {{
            const driveMi = Math.round(data.routes[0].distance / 1609.344 * 10) / 10;
            const driveMin = Math.round(data.routes[0].duration / 60);
            const el = document.getElementById('commute-item-' + encodeURIComponent(loc.name));
            if (el) {{
              const placeholder = el.querySelector('.drive-route-placeholder');
              if (placeholder) placeholder.innerHTML = `\\uD83D\\uDE97 ${{driveMi}} mi drive &middot; ~${{driveMin}} min`;
            }}
          }}
        }} catch (e) {{
          const el = document.getElementById('commute-item-' + encodeURIComponent(loc.name));
          if (el) {{
            const placeholder = el.querySelector('.drive-route-placeholder');
            if (placeholder) placeholder.textContent = '';
          }}
        }}
      }});
    }}

    function loadDetailOverrides() {{
      try {{ return JSON.parse(localStorage.getItem('unitOverrides') || '{{}}'); }} catch (e) {{ return {{}}; }}
    }}

    function saveDetailOverrides(overrides) {{
      try {{ localStorage.setItem('unitOverrides', JSON.stringify(overrides)); }} catch (e) {{}}
    }}

    function updateFavToggleBtn(overrides) {{
      const btn = document.getElementById('fav-toggle-btn');
      const isFav = !!(overrides[UNIT_ID] && overrides[UNIT_ID].favorite);
      btn.textContent = isFav ? '\\u2605 Favorited' : '\\u2606 Add to favorites';
      btn.classList.toggle('active', isFav);
    }}

    function toggleDetailFavorite() {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      entry.favorite = !entry.favorite;
      overrides[UNIT_ID] = entry;
      saveDetailOverrides(overrides);
      updateFavToggleBtn(overrides);
    }}

    function saveDetailNotes() {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      entry.notes = document.getElementById('detail-notes-textarea').value;
      overrides[UNIT_ID] = entry;
      saveDetailOverrides(overrides);
      const msg = document.getElementById('notes-saved-msg');
      msg.textContent = '\\u2713 Saved';
      setTimeout(() => {{ msg.textContent = ''; }}, 1500);
    }}

    function formatTimelineDate(dateStr) {{
      const d = new Date(dateStr + 'T00:00:00');
      if (isNaN(d.getTime())) return dateStr;
      return d.toLocaleDateString(undefined, {{ year: 'numeric', month: 'short', day: 'numeric' }});
    }}

    function renderDetailTimeline() {{
      const overrides = loadDetailOverrides();
      const entries = (overrides[UNIT_ID] && overrides[UNIT_ID].timeline) || [];
      const list = document.getElementById('detail-timeline-entries');
      if (!entries.length) {{
        list.innerHTML = '<div class="timeline-empty">No interactions logged yet.</div>';
        return;
      }}
      const sorted = entries.map((e, i) => ({{ ...e, i }})).sort((a, b) => b.date.localeCompare(a.date));
      const typeIcons = {{ call: '\\uD83D\\uDCDE', text: '\\uD83D\\uDCAC', email: '\\u2709', visit: '\\uD83D\\uDEB6', app: '\\uD83D\\uDCF1', note: '\\uD83D\\uDCDD' }};
      list.innerHTML = sorted.map(entry => {{
        const icon = typeIcons[entry.type] || typeIcons.note;
        const dir = entry.direction === 'in' ? '\\u2B05' : (entry.direction === 'out' ? '\\u27A1' : '');
        const personTag = entry.person ? `<span style="color:var(--accent);font-size:11px;margin-right:4px">${{escapeHtml(entry.person)}}</span>` : '';
        return `<div class="timeline-entry">`
          + `<div class="timeline-entry-date">${{formatTimelineDate(entry.date)}} ${{icon}}${{dir}}</div>`
          + `<div class="timeline-entry-text">${{personTag}}${{escapeHtml(entry.text)}}</div>`
          + `<button class="timeline-entry-remove" title="Remove" onclick="removeDetailTimelineEntry(${{entry.i}})">\\u2715</button>`
          + `</div>`;
      }}).join('');
    }}

    function addDetailTimelineEntry() {{
      const date = document.getElementById('detail-timeline-date').value;
      const text = document.getElementById('detail-timeline-text').value.trim();
      const type = document.getElementById('detail-timeline-type').value;
      const direction = document.getElementById('detail-timeline-direction').value;
      const person = document.getElementById('detail-timeline-person').value;
      if (!date || !text) return;
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      const timeline = entry.timeline || [];
      timeline.push({{ date, text, type, direction, person }});
      entry.timeline = timeline;
      overrides[UNIT_ID] = entry;
      saveDetailOverrides(overrides);
      document.getElementById('detail-timeline-text').value = '';
      renderDetailTimeline();
    }}

    function removeDetailTimelineEntry(index) {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      const timeline = entry.timeline || [];
      timeline.splice(index, 1);
      entry.timeline = timeline;
      overrides[UNIT_ID] = entry;
      saveDetailOverrides(overrides);
      renderDetailTimeline();
    }}

    function initDetailMap() {{
      const mapEl = document.getElementById('detail-map');
      if (UNIT_LAT == null || UNIT_LON == null) {{
        mapEl.outerHTML = '<div class="timeline-empty">This unit\\'s location is unknown, so it can\\'t be shown on a map.</div>';
        return;
      }}
      const map = L.map('detail-map', {{ scrollWheelZoom: false }});
      L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19
      }}).addTo(map);

      const bounds = [[UNIT_LAT, UNIT_LON]];
      L.marker([UNIT_LAT, UNIT_LON]).addTo(map)
        .bindPopup(`<b>This unit</b><br>${{escapeHtml(UNIT_ADDRESS)}}`)
        .openPopup();

      if (TARGET_LAT != null && TARGET_LON != null) {{
        bounds.push([TARGET_LAT, TARGET_LON]);
        L.marker([TARGET_LAT, TARGET_LON]).addTo(map)
          .bindPopup(`<b>${{escapeHtml(TARGET_NAME || 'Search center')}}</b>`);
        L.polyline([[UNIT_LAT, UNIT_LON], [TARGET_LAT, TARGET_LON]], {{ color: '#63b3ed', weight: 2, dashArray: '4 6' }}).addTo(map);
      }}

      // Work locations from localStorage
      let workLocations = [];
      try {{ workLocations = JSON.parse(localStorage.getItem('workLocations') || '[]'); }} catch (e) {{}}
      const workIcon = L.divIcon({{
        html: '&#128188;',
        className: 'work-marker-icon',
        iconSize: [24, 24],
        iconAnchor: [12, 12]
      }});
      workLocations.forEach(async (loc, i) => {{
        if (loc.lat == null || loc.lon == null) return;
        const d = Math.round(haversineMiles(UNIT_LAT, UNIT_LON, loc.lat, loc.lon) * 10) / 10;
        bounds.push([loc.lat, loc.lon]);
        let popupHtml = `<b>${{escapeHtml(loc.name)}}</b><br>${{d}} mi straight-line`;
        const marker = L.marker([loc.lat, loc.lon], {{ icon: workIcon }}).addTo(map)
          .bindPopup(popupHtml);
        L.polyline([[UNIT_LAT, UNIT_LON], [loc.lat, loc.lon]], {{ color: '#b794f4', weight: 2, dashArray: '4 6' }}).addTo(map);
        try {{
          const url = `https://router.project-osrm.org/route/v1/driving/${{UNIT_LON}},${{UNIT_LAT}};${{loc.lon}},${{loc.lat}}?overview=false`;
          const resp = await fetch(url);
          const data = await resp.json();
          if (data.code === 'Ok' && data.routes.length) {{
            const driveMi = Math.round(data.routes[0].distance / 1609.344 * 10) / 10;
            const driveMin = Math.round(data.routes[0].duration / 60);
            popupHtml += `<br>\\uD83D\\uDE97 ${{driveMi}} mi drive &middot; ~${{driveMin}} min`;
            if (loc.commute_to_min || loc.commute_from_min) {{
              const parts = [];
              if (loc.commute_to_min) parts.push(`\\u2192 ${{loc.commute_to_min}}m`);
              if (loc.commute_from_min) parts.push(`\\u2190 ${{loc.commute_from_min}}m`);
              popupHtml += `<br>${{parts.join(' / ')}}`;
            }}
            marker.setPopupContent(popupHtml);
          }}
        }} catch (e) {{}}
      }});

      if (bounds.length > 1) {{
        map.fitBounds(bounds, {{ padding: [30, 30] }});
      }} else {{
        map.setView(bounds[0], 13);
      }}
      setTimeout(() => map.invalidateSize(), 100);
    }}

    function getEffectiveContact() {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      return {{
        phone: (entry.contact_phone !== undefined ? entry.contact_phone : CONTACT_PHONE) || null,
        email: (entry.contact_email !== undefined ? entry.contact_email : CONTACT_EMAIL) || null,
      }};
    }}

    function renderContactSection() {{
      const {{ phone, email }} = getEffectiveContact();
      const display = document.getElementById('contact-display');
      if (!display) return;
      let html = '';
      if (CONTACT_NAME) {{
        html += `<div class="contact-chip"><span class="contact-label">Name</span><span class="contact-value">${{escapeHtml(CONTACT_NAME)}}</span></div>`;
      }}
      if (phone) {{
        const ph = phone.replace(/[^0-9+]/g, '');
        html += `<div class="contact-chip"><span class="contact-label">Phone</span><a href="tel:${{ph}}" class="contact-value">${{escapeHtml(phone)}}</a></div>`;
      }}
      if (email) {{
        html += `<div class="contact-chip"><span class="contact-label">Email</span><a href="mailto:${{escapeHtml(email)}}" class="contact-value">${{escapeHtml(email)}}</a></div>`;
      }}
      if (phone || email) {{
        html += `<button class="compose-btn" onclick="openContactModal()">&#9993; Compose Message</button>`;
        html += `<button class="contact-edit-link" onclick="toggleContactEdit(true)">Edit</button>`;
      }} else {{
        html += `<span class="contact-none">No contact info found on listing.</span> `;
        html += `<button class="contact-edit-link" onclick="toggleContactEdit(true)">Add manually</button>`;
      }}
      display.innerHTML = html;
    }}

    function toggleContactEdit(show) {{
      const form = document.getElementById('contact-edit-form');
      const display = document.getElementById('contact-display');
      if (show) {{
        const {{ phone, email }} = getEffectiveContact();
        document.getElementById('contact-edit-phone').value = phone || '';
        document.getElementById('contact-edit-email').value = email || '';
        form.style.display = '';
        display.style.display = 'none';
      }} else {{
        form.style.display = 'none';
        display.style.display = '';
      }}
    }}

    function saveContactInfo() {{
      const phone = document.getElementById('contact-edit-phone').value.trim() || null;
      const email = document.getElementById('contact-edit-email').value.trim() || null;
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      entry.contact_phone = phone;
      entry.contact_email = email;
      overrides[UNIT_ID] = entry;
      saveDetailOverrides(overrides);
      toggleContactEdit(false);
      renderContactSection();
    }}

    // ---- People (contacts associated with this unit) ----
    function getPeople() {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      const stored = entry.people || [];
      if (stored.length === 0 && EMBEDDED_CONTACTS.length > 0) return [...EMBEDDED_CONTACTS];
      return stored;
    }}

    function savePeople(people) {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      entry.people = people;
      overrides[UNIT_ID] = entry;
      saveDetailOverrides(overrides);
    }}

    function renderPeople() {{
      const list = document.getElementById('people-list');
      if (!list) return;
      const people = getPeople();
      if (!people.length) {{
        list.innerHTML = '<div class="people-empty">No contacts added yet.</div>';
        return;
      }}
      list.innerHTML = people.map((p, i) => {{
        const roleBadge = p.role ? `<span class="person-role">${{escapeHtml(p.role)}}</span>` : '';
        const contactParts = [];
        if (p.phone) contactParts.push(`<a href="tel:${{encodeURIComponent(p.phone)}}" class="person-contact">${{escapeHtml(p.phone)}}</a>`);
        if (p.email) contactParts.push(`<a href="mailto:${{encodeURIComponent(p.email)}}" class="person-contact">${{escapeHtml(p.email)}}</a>`);
        return `<div class="person-card">`
          + `<span class="person-name">${{escapeHtml(p.name || 'Unknown')}}</span>`
          + roleBadge
          + contactParts.join(' ')
          + `<button class="person-remove" title="Remove" onclick="removePerson(${{i}})">\\u2715</button>`
          + `</div>`;
      }}).join('');
      updatePersonSelect();
    }}

    function addPerson() {{
      const name = document.getElementById('person-name').value.trim();
      const phone = document.getElementById('person-phone').value.trim();
      const email = document.getElementById('person-email').value.trim();
      const role = document.getElementById('person-role').value;
      if (!name && !phone && !email) return;
      const people = getPeople();
      people.push({{ name: name || '', phone: phone || '', email: email || '', role: role || '' }});
      savePeople(people);
      document.getElementById('person-name').value = '';
      document.getElementById('person-phone').value = '';
      document.getElementById('person-email').value = '';
      document.getElementById('person-role').value = '';
      renderPeople();
    }}

    function removePerson(index) {{
      const people = getPeople();
      people.splice(index, 1);
      savePeople(people);
      renderPeople();
    }}

    function updatePersonSelect() {{
      const sel = document.getElementById('detail-timeline-person');
      if (!sel) return;
      const people = getPeople();
      const val = sel.value;
      sel.innerHTML = '<option value="">Person…</option>' +
        people.filter(p => p.name).map(p =>
          `<option value="${{escapeHtml(p.name)}}">${{escapeHtml(p.name)}}</option>`
        ).join('');
      sel.value = val;
    }}

    function openContactModal() {{
      const {{ phone, email }} = getEffectiveContact();
      document.getElementById('contact-textarea').value = CONTACT_MESSAGE;
      const emailBtn = document.getElementById('btn-email');
      const callBtn = document.getElementById('btn-copy-call');
      if (emailBtn) emailBtn.style.display = email ? '' : 'none';
      if (callBtn) callBtn.style.display = phone ? '' : 'none';
      document.getElementById('contact-overlay').classList.add('open');
    }}

    function closeContactOverlay(event) {{
      if (!event || event.target === document.getElementById('contact-overlay')) {{
        document.getElementById('contact-overlay').classList.remove('open');
      }}
    }}

    function logContactToTimeline(method, preview) {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      const timeline = entry.timeline || [];
      const today = new Date().toISOString().slice(0, 10);
      const short = preview.length > 80 ? preview.slice(0, 80) + '...' : preview;
      timeline.push({{ date: today, text: method + ': ' + short }});
      entry.timeline = timeline;
      overrides[UNIT_ID] = entry;
      saveDetailOverrides(overrides);
      renderDetailTimeline();
    }}

    function sendContactEmail() {{
      const {{ email }} = getEffectiveContact();
      const msg = document.getElementById('contact-textarea').value;
      const subject = encodeURIComponent('Inquiry about your listing');
      const body = encodeURIComponent(msg);
      window.open('mailto:' + (email || '') + '?subject=' + subject + '&body=' + body, '_blank');
      logContactToTimeline('Emailed', msg);
      document.getElementById('contact-overlay').classList.remove('open');
    }}

    function copyAndCall() {{
      const {{ phone }} = getEffectiveContact();
      const msg = document.getElementById('contact-textarea').value;
      try {{ navigator.clipboard.writeText(msg); }} catch (e) {{}}
      logContactToTimeline('Called (message copied)', msg);
      if (phone) window.location.href = 'tel:' + phone;
      document.getElementById('contact-overlay').classList.remove('open');
    }}

    function logContactOnly() {{
      const msg = document.getElementById('contact-textarea').value;
      logContactToTimeline('Contacted', msg);
      document.getElementById('contact-overlay').classList.remove('open');
    }}

    // ---- Detail-page lightbox ----
    var _dlbPhotos = [];
    var _dlbIndex = 0;
    function openDetailGallery(el) {{
      _dlbPhotos = JSON.parse(el.dataset.photos || '[]');
      _dlbIndex = parseInt(el.dataset.index || '0', 10);
      _showDetailLb();
      document.getElementById('detail-lightbox').classList.add('open');
    }}
    function _showDetailLb() {{
      document.getElementById('detail-lb-img').src = _dlbPhotos[_dlbIndex] || '';
      var ctr = document.getElementById('detail-lb-counter');
      ctr.textContent = _dlbPhotos.length > 1 ? (_dlbIndex + 1) + ' / ' + _dlbPhotos.length : '';
      document.querySelectorAll('.detail-lb-nav').forEach(function(b) {{
        b.style.display = _dlbPhotos.length > 1 ? '' : 'none';
      }});
    }}
    function detailLbPrev(e) {{ e && e.stopPropagation(); _dlbIndex = (_dlbIndex - 1 + _dlbPhotos.length) % _dlbPhotos.length; _showDetailLb(); }}
    function detailLbNext(e) {{ e && e.stopPropagation(); _dlbIndex = (_dlbIndex + 1) % _dlbPhotos.length; _showDetailLb(); }}
    function closeDetailLightbox(e) {{
      if (e && e.target.id !== 'detail-lightbox') return;
      document.getElementById('detail-lightbox').classList.remove('open');
    }}
    document.addEventListener('keydown', function(e) {{
      if (!document.getElementById('detail-lightbox').classList.contains('open')) return;
      if (e.key === 'Escape') document.getElementById('detail-lightbox').classList.remove('open');
      else if (e.key === 'ArrowLeft') detailLbPrev(null);
      else if (e.key === 'ArrowRight') detailLbNext(null);
    }});

    (function() {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      document.getElementById('detail-notes-textarea').value = entry.notes || '';
      updateFavToggleBtn(overrides);
      renderCommute();
      renderContactSection();
      renderPeople();
      document.getElementById('detail-timeline-date').value = new Date().toISOString().slice(0, 10);
      renderDetailTimeline();
      initDetailMap();
    }})();
  </script>
</body>
</html>'''

if __name__ == "__main__":
  generate_html()
