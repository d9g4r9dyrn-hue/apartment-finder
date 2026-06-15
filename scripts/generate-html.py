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

# Ensure emoji/unicode in print() output doesn't crash on Windows consoles (cp1252)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent
UNITS_JSON = PROJECT_ROOT / "outputs" / "units.json"
SUMMARY_HTML = PROJECT_ROOT / "outputs" / "units-summary.html"
CONFIG_JSON = PROJECT_ROOT / "config.json"

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
        'move_in_date': u.get('move_in_date'),
        'move_in_sort': move_in_sort_key(u.get('move_in_date')),
        'notes': u.get('notes'),
    }

def generate_html():
    """Generate the complete units summary + map page"""
    units_data = load_units()
    config = load_config()
    all_units = units_data.get('units', [])

    # Apply distance/price/beds filtering (console stats only - the page itself
    # re-applies these filters client-side so the Re-fetch button can refresh
    # without rerunning this script)
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

    # Generate per-unit detail pages for all units in the database
    for u in all_units:
        unit_dir = PROJECT_ROOT / 'outputs' / 'apartments' / u['id']
        unit_dir.mkdir(parents=True, exist_ok=True)
        detail_path = unit_dir / 'index.html'
        detail_html = generate_unit_detail_html(u, lease_end, config.get('target_location'))
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

    # Full HTML
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Units Found — Rental Finder</title>
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

    .work-location-remove {{
      background: transparent;
      border: none;
      color: var(--subtext);
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
      padding: 0 2px;
    }}

    .work-location-remove:hover {{ color: var(--red); }}

    .work-loc-col {{
      width: 90px;
      text-align: right;
      font-size: 12px;
      color: var(--subtext);
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
    .units-table.hide-details .details-col {{ display: none; }}

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
      padding: 12px 8px;
      text-align: left;
      font-weight: 600;
      color: var(--text);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid var(--border);
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
      color: var(--accent);
      text-decoration: none;
      font-weight: 500;
    }}

    .unit-link:hover {{
      text-decoration: underline;
    }}

    .dup-tag {{
      font-size: 11px;
      color: var(--subtext);
      font-style: italic;
      margin-top: 2px;
    }}

    .unit-id-tag {{
      font-size: 11px;
      color: var(--subtext);
      font-family: 'DM Mono', monospace;
      margin-top: 2px;
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
      text-align: right;
      color: var(--muted);
    }}

    .unit-spec-col {{
      text-align: right;
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

    .tools-row {{
      display: flex;
      flex-wrap: wrap;
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

    /* Narrow / tall (mobile) viewports: stack the sidebar above the list
       instead of side-by-side, and let the page scroll vertically */
    @media (max-width: 860px) {{
      body {{
        height: auto;
        overflow: auto;
      }}

      .layout {{
        flex-direction: column;
        overflow: visible;
        height: auto;
      }}

      .sidebar {{
        flex: 0 0 auto;
        width: 100%;
        max-width: none;
        min-width: 0;
        overflow-y: visible;
        padding-right: 0;
      }}

      .resizer {{
        display: none;
      }}

      .list-panel {{
        flex: 1 1 auto;
        padding-left: 0;
      }}

      #map {{
        aspect-ratio: 16 / 9;
      }}
    }}

  </style>
</head>
<body>
  <header>
    <div class="page-title">UNITS FOUND</div>
  </header>

  <div class="layout">
    <aside class="sidebar">
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
          <div class="stat-label">Total Scraped</div>
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

      <div class="criteria-bar">
        <div class="criteria-text">
          <strong>Criteria:</strong> <span id="criteria-text-value">{criteria_text}</span>
          <button id="edit-criteria-btn" class="edit-icon-btn" title="Edit search criteria">&#9998; Edit</button>
        </div>
        <div id="criteria-edit" class="criteria-edit">
          <div class="criteria-edit-grid">
            <div class="full">
              <label for="address-input">Your address (distance & map center)</label>
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
        <div class="refresh-controls">
          <button id="reload-btn" class="ctrl-btn" title="Reload this page from disk">&#10227; Reload</button>
          <button id="refetch-btn" class="ctrl-btn" title="Re-fetch units.json and refresh without reloading">&#10227; Re-fetch</button>
          <button id="howto-btn" class="ctrl-btn ctrl-btn-icon" title="How to update results">&#9432;</button>
        </div>
        <div id="howto-panel" class="howto-panel">
          <p>To pull fresh listings and rebuild this page, run in a terminal:</p>
          <code>python scripts/crawl_all.py</code>
          <code>python scripts/generate-map.py</code>
          <code>python scripts/generate-html.py</code>
          <p>Then click Reload above. (Re-fetch only works if this page is served over http://, not opened as a file.)</p>
        </div>
      </div>

      <div id="filter-note" class="filter-note" style="display:none;"></div>

      <div id="map"></div>
      <div id="map-legend" class="map-legend"></div>

      <div class="tools-row">
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
        <button id="list-filters-btn" class="tool-btn" title="Narrow the displayed list without changing your search criteria">List filters<span id="list-filters-badge" class="tool-badge" style="display:none;"></span></button>
        <button id="columns-btn" class="tool-btn" title="Choose which table columns are displayed">Columns<span id="columns-badge" class="tool-badge" style="display:none;"></span></button>
        <button id="work-locations-btn" class="tool-btn" title="Add work locations to show distance columns">Work locations<span id="work-locations-badge" class="tool-badge" style="display:none;"></span></button>
      </div>

      <div id="list-filters-panel" class="criteria-edit tools-panel">
        <div id="list-filters-grid" class="criteria-edit-grid">
          <div>
            <label for="lf-max-distance">Max distance (mi)</label>
            <input type="number" id="lf-max-distance" min="0" step="0.5" placeholder="Any" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-min-sqft">Min sqft</label>
            <input type="number" id="lf-min-sqft" min="0" step="50" placeholder="Any" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-min-price">Min price ($)</label>
            <input type="number" id="lf-min-price" min="0" step="50" placeholder="Any" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-max-price">Max price ($)</label>
            <input type="number" id="lf-max-price" min="0" step="50" placeholder="Any" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-min-beds">Min beds</label>
            <input type="number" id="lf-min-beds" min="0" step="1" placeholder="Any" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-min-baths">Min baths</label>
            <input type="number" id="lf-min-baths" min="0" step="0.5" placeholder="Any" oninput="applyListFilters()">
          </div>
          <div>
            <label for="lf-flooring">Flooring</label>
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
            <label for="lf-available-by">Available by</label>
            <input type="date" id="lf-available-by" oninput="applyListFilters()">
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-washer-dryer" onchange="applyListFilters()"> Washer/Dryer</label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-gated" onchange="applyListFilters()"> Gated</label>
          </div>
          <div class="checkbox-cell">
            <label class="checkbox-label"><input type="checkbox" id="lf-hide-age-restricted" onchange="applyListFilters()"> Hide age-restricted (55+)</label>
          </div>
        </div>
        <div class="criteria-edit-actions">
          <button id="clear-list-filters-btn" class="ctrl-btn">Clear</button>
          <span id="list-filters-summary" class="criteria-edit-status"></span>
        </div>
      </div>

      <div id="columns-panel" class="criteria-edit tools-panel">
        <div class="criteria-edit-grid columns-grid">
          <label><input type="checkbox" data-col="mine" onchange="onColumnToggle(this)"> Mine</label>
          <label><input type="checkbox" data-col="photo" onchange="onColumnToggle(this)"> Photo</label>
          <label><input type="checkbox" data-col="distance" onchange="onColumnToggle(this)"> Distance</label>
          <label><input type="checkbox" data-col="price" onchange="onColumnToggle(this)"> Price</label>
          <label><input type="checkbox" data-col="beds" onchange="onColumnToggle(this)"> Beds</label>
          <label><input type="checkbox" data-col="baths" onchange="onColumnToggle(this)"> Baths</label>
          <label><input type="checkbox" data-col="sqft" onchange="onColumnToggle(this)"> Sqft</label>
          <label><input type="checkbox" data-col="type" onchange="onColumnToggle(this)"> Type</label>
          <label><input type="checkbox" data-col="movein" onchange="onColumnToggle(this)"> Move-in</label>
          <label><input type="checkbox" data-col="amenities" onchange="onColumnToggle(this)"> Amenities</label>
          <label><input type="checkbox" data-col="flooring" onchange="onColumnToggle(this)"> Flooring</label>
          <label><input type="checkbox" data-col="source" onchange="onColumnToggle(this)"> Source</label>
          <label><input type="checkbox" data-col="quality" onchange="onColumnToggle(this)"> Quality</label>
          <label><input type="checkbox" data-col="details" onchange="onColumnToggle(this)"> Details</label>
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
        </div>
        <div class="criteria-edit-actions">
          <button id="add-work-location-btn" class="ctrl-btn">Add</button>
          <span id="work-location-status" class="criteria-edit-status"></span>
        </div>
      </div>
    </aside>

    <div class="resizer" id="resizer"></div>

    <div class="list-panel">
      <table class="units-table">
        <thead>
          <tr>
            <th class="mine-col sortable" data-sort="favorite" onclick="applySort('favorite')">Mine</th>
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
            <th id="details-th" class="details-col">Details</th>
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
    const map = L.map('map').setView(center, 13);
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

    // Renders a 1-5 quality rating from the AI photo scan as a number, or a dash if unrated
    function qualityStarsHtml(u) {{
      const rating = u.quality_rating;
      if (rating == null) return '<span title="Not yet scanned">\\u2014</span>';
      const notes = u.quality_notes ? escapeHtml(u.quality_notes) : `${{rating}}/5`;
      return `<span title="${{notes}}">${{rating.toFixed(1)}} \\u2605</span>`;
    }}

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
    function groupByAddress(units) {{
      const groups = new Map();
      const order = [];
      units.forEach(u => {{
        const key = u.address || `__unit_${{u.id}}`;
        if (!groups.has(key)) {{
          groups.set(key, []);
          order.push(key);
        }}
        groups.get(key).push(u);
      }});
      const out = [];
      order.forEach(key => out.push(...groups.get(key)));
      return out;
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
      washerDryer: false,
      gated: false,
      hideAgeRestricted: false,
      workMaxDistance: [],
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
      renderAll(lastData);
    }}

    function filterUnits(units) {{
      const target = CONFIG.target_location;
      const radius = CONFIG.search_radius_miles;
      const out = [];
      units.forEach(raw => {{
        const u = Object.assign({{}}, raw);
        u.favorite = isFavorite(u.id);
        u.notes_text = getUnitNotes(u.id);
        u.timeline = getUnitTimeline(u.id);
        if (currentFavoritesOnly && !u.favorite) return;

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
        }});

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
        if (listFilters.washerDryer && !u.has_washer_dryer) return;
        if (listFilters.gated && !u.is_gated) return;
        if (listFilters.hideAgeRestricted && u.age_restriction) return;
        if (workLocations.some((loc, i) => {{
          const maxD = listFilters.workMaxDistance[i];
          if (maxD == null) return false;
          const d = u['work_dist_' + i];
          return (d == null || d > maxD);
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

    function applyListFilters() {{
      listFilters.maxDistance = numOrNull(document.getElementById('lf-max-distance').value);
      listFilters.minPrice = numOrNull(document.getElementById('lf-min-price').value);
      listFilters.maxPrice = numOrNull(document.getElementById('lf-max-price').value);
      listFilters.minBeds = numOrNull(document.getElementById('lf-min-beds').value);
      listFilters.minBaths = numOrNull(document.getElementById('lf-min-baths').value);
      listFilters.minSqft = numOrNull(document.getElementById('lf-min-sqft').value);
      listFilters.flooring = document.getElementById('lf-flooring').value || null;
      listFilters.availableBy = document.getElementById('lf-available-by').value || null;
      listFilters.washerDryer = document.getElementById('lf-washer-dryer').checked;
      listFilters.gated = document.getElementById('lf-gated').checked;
      listFilters.hideAgeRestricted = document.getElementById('lf-hide-age-restricted').checked;
      listFilters.workMaxDistance = workLocations.map((loc, i) => numOrNull(document.getElementById(`lf-work-distance-${{i}}`).value));
      renderListFiltersSummary();
      renderAll(lastData);
    }}

    function clearListFilters() {{
      ['lf-max-distance', 'lf-min-price', 'lf-max-price', 'lf-min-beds', 'lf-min-baths', 'lf-min-sqft', 'lf-flooring', 'lf-available-by'].forEach(id => {{
        document.getElementById(id).value = '';
      }});
      ['lf-washer-dryer', 'lf-gated', 'lf-hide-age-restricted'].forEach(id => {{
        document.getElementById(id).checked = false;
      }});
      workLocations.forEach((loc, i) => {{
        document.getElementById(`lf-work-distance-${{i}}`).value = '';
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
      if (listFilters.washerDryer) parts.push('washer/dryer');
      if (listFilters.gated) parts.push('gated');
      if (listFilters.hideAgeRestricted) parts.push('hide 55+');
      workLocations.forEach((loc, i) => {{
        const maxD = listFilters.workMaxDistance[i];
        if (maxD != null) parts.push(`\\u2264${{maxD}} mi to ${{loc.name}}`);
      }});
      document.getElementById('list-filters-summary').textContent = parts.length ? parts.join(' \\u00b7 ') : 'None';
      const badge = document.getElementById('list-filters-badge');
      badge.style.display = parts.length ? '' : 'none';
      badge.textContent = parts.length;
      document.getElementById('list-filters-btn').title = parts.length
        ? `List filters: ${{parts.join(' \\u00b7 ')}}`
        : 'Narrow the displayed list without changing your search criteria';
    }}

    // ---- Column visibility (persisted across reloads) ----
    const ALL_COLUMNS = ['mine', 'photo', 'distance', 'price', 'beds', 'baths', 'sqft', 'type', 'movein', 'amenities', 'flooring', 'source', 'quality', 'details'];

    function loadColumnPrefs() {{
      try {{ return JSON.parse(localStorage.getItem('columnPrefs') || '{{}}'); }} catch (e) {{ return {{}}; }}
    }}

    function saveColumnPrefs() {{
      try {{ localStorage.setItem('columnPrefs', JSON.stringify(columnPrefs)); }} catch (e) {{}}
    }}

    let columnPrefs = loadColumnPrefs();

    function isColumnVisible(col) {{
      return columnPrefs[col] !== false;
    }}

    function applyColumnPrefs() {{
      const table = document.querySelector('.units-table');
      ALL_COLUMNS.forEach(col => {{
        table.classList.toggle('hide-' + col, !isColumnVisible(col));
      }});
      renderColumnsSummary();
    }}

    function renderColumnsSummary() {{
      const hidden = ALL_COLUMNS.filter(col => !isColumnVisible(col));
      const badge = document.getElementById('columns-badge');
      badge.style.display = hidden.length ? '' : 'none';
      badge.textContent = hidden.length;
      document.getElementById('columns-btn').title = hidden.length
        ? `Columns: ${{hidden.length}} hidden`
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
      document.querySelectorAll('#columns-panel input[type="checkbox"]').forEach(cb => {{ cb.checked = true; }});
      applyColumnPrefs();
    }}

    // ---- Work locations (named places to show a per-unit distance column for) ----
    function loadWorkLocations() {{
      try {{ return JSON.parse(localStorage.getItem('workLocations') || '[]'); }} catch (e) {{ return []; }}
    }}

    function saveWorkLocations() {{
      try {{ localStorage.setItem('workLocations', JSON.stringify(workLocations)); }} catch (e) {{}}
    }}

    let workLocations = loadWorkLocations();

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
      list.innerHTML = workLocations.map((loc, i) => `
        <div class="work-location-item">
          <span class="work-location-name">${{escapeHtml(loc.name)}}</span>
          <span class="work-location-address">${{escapeHtml(loc.address)}}</span>
          <button class="work-location-remove" onclick="removeWorkLocation(${{i}})" title="Remove">&times;</button>
        </div>
      `).join('');

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
      listFilters.workMaxDistance = workLocations.map(() => null);
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
      }});
    }}

    // Rebuilds the dynamic per-work-location <th> columns, inserted just before "Details"
    function renderWorkLocationHeaders() {{
      document.querySelectorAll('.units-table .work-loc-th').forEach(th => th.remove());
      const detailsTh = document.getElementById('details-th');
      workLocations.forEach((loc, i) => {{
        const th = document.createElement('th');
        th.className = 'work-loc-col work-loc-th sortable';
        th.dataset.sort = `work_dist_${{i}}`;
        th.textContent = loc.name;
        th.addEventListener('click', () => applySort(`work_dist_${{i}}`));
        detailsTh.parentNode.insertBefore(th, detailsTh);
      }});
      updateSortIndicators();
    }}

    function removeWorkLocation(index) {{
      workLocations.splice(index, 1);
      saveWorkLocations();
      renderWorkLocationsList();
      renderWorkLocationHeaders();
      renderWorkLocationMarkers();
      renderWorkDistanceFilters();
      renderListFiltersSummary();
      renderAll(lastData);
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

        const aNull = (va == null || va === '');
        const bNull = (vb == null || vb === '');
        if (aNull && bNull) return 0;
        if (aNull) return 1;
        if (bNull) return -1;

        if (typeof va === 'string' || typeof vb === 'string') {{
          va = String(va).toLowerCase();
          vb = String(vb).toLowerCase();
          if (va < vb) return dir === 'asc' ? -1 : 1;
          if (va > vb) return dir === 'asc' ? 1 : -1;
          return 0;
        }}

        return dir === 'asc' ? (va - vb) : (vb - va);
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

    function unitRowHtml(u, dupAddresses, continuesGroup) {{
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
      const idTag = `<div class="unit-id-tag">${{escapeHtml(u.id || '')}}</div>`;

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

      const distanceHtml = (u.distance_miles != null) ? `${{u.distance_miles}} mi` : '\\u2014';
      const detailsHref = `apartments/${{u.id}}/index.html`;

      const favActive = u.favorite ? 'active' : '';
      const favIcon = u.favorite ? '\\u2605' : '\\u2606';
      const favTitle = u.favorite ? 'Remove from favorites' : 'Add to favorites';
      const noteActive = u.notes_text ? 'active' : '';
      const noteTitle = u.notes_text ? 'Edit notes' : 'Add notes';
      const timelineActive = (u.timeline && u.timeline.length) ? 'active' : '';
      const timelineTitle = (u.timeline && u.timeline.length) ? 'View timeline' : 'Add timeline entry';
      const mineHtml = `<button class="icon-btn fav-btn ${{favActive}}" title="${{favTitle}}" onclick="toggleFavorite('${{u.id}}')">${{favIcon}}</button>` +
        `<button class="icon-btn note-btn ${{noteActive}}" title="${{noteTitle}}" onclick="openNotesModal('${{u.id}}')">\\u270e</button>` +
        `<button class="icon-btn timeline-btn ${{timelineActive}}" title="${{timelineTitle}}" onclick="openTimelineModal('${{u.id}}')">\\u{{1F551}}</button>`;

      const workCells = workLocations.map((loc, i) => {{
        const d = u['work_dist_' + i];
        return `<td class="work-loc-col">${{(d != null) ? d + ' mi' : '\\u2014'}}</td>`;
      }}).join('');

      let groupClass = '';
      if (u.address && dupAddresses.has(u.address)) {{
        groupClass = continuesGroup ? ' same-property group-continues' : ' same-property';
      }}

      return `<tr class="unit-row${{groupClass}}" data-price="${{price}}" data-beds="${{beds}}" data-baths="${{baths}}" onmouseenter="highlightMapMarker('${{u.id}}')" onmouseleave="clearMapMarkerHighlight()">
        <td class="mine-col">${{mineHtml}}</td>
        <td class="unit-thumb-col">${{thumbHtml}}</td>
        <td class="unit-distance-col">${{distanceHtml}}</td>
        <td class="unit-address-col"><a href="${{sourceUrl}}" target="_blank" class="unit-link">${{address}}</a>${{idTag}}${{addressExtra}}</td>
        <td class="unit-price-col">$${{formatNumber(price)}}</td>
        <td class="unit-spec-col beds-col">${{beds}} bd</td>
        <td class="unit-spec-col baths-col">${{baths}} ba</td>
        <td class="unit-spec-col sqft-col">${{formatNumber(sqft)}}</td>
        <td class="unit-type-col">${{housingType}}${{ageBadge}}</td>
        <td class="movein-col">${{moveInHtml(u)}}</td>
        <td class="unit-amenity-col">${{amenitiesCellHtml(u)}}</td>
        <td class="flooring-col">${{flooringHtml(u)}}</td>
        <td class="unit-source-col">${{source}}</td>
        <td class="quality-col">${{qualityStarsHtml(u)}}</td>
        ${{workCells}}
        <td class="details-col"><a href="${{detailsHref}}" target="_blank" class="details-link">Details</a></td>
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
      tbody.innerHTML = units.map((u, i) => {{
        const next = units[i + 1];
        const continuesGroup = !!(u.address && next && next.address === u.address);
        return unitRowHtml(u, dupAddresses, continuesGroup);
      }}).join('');
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
    function highlightMapMarker(id) {{
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
    }}

    function clearMapMarkerHighlight() {{
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

    function renderFilterNote(filteredCount, totalCount) {{
      const note = document.getElementById('filter-note');
      const excluded = totalCount - filteredCount;
      if (excluded > 0) {{
        note.textContent = `\\u2139\\ufe0f Showing ${{filteredCount}} of ${{totalCount}} units matching criteria (${{CONFIG.target_name}}). ${{excluded}} unit(s) filtered out.`;
        note.style.display = 'block';
      }} else {{
        note.style.display = 'none';
      }}
    }}

    function renderAll(data) {{
      lastData = data;
      let filtered = filterUnits(data.units || []);
      filtered = sortUnits(filtered);
      renderTypeFilter(filtered);
      if (currentTypeFilter) {{
        filtered = filtered.filter(u => (u.housing_type || 'Unknown') === currentTypeFilter);
      }}
      filtered = groupByAddress(filtered);
      const dupAddresses = findDuplicateAddresses(filtered);
      renderStats(filtered.length, data);
      renderFilterNote(filtered.length, (data.units || []).length);
      renderMapLegend(filtered);
      renderTable(filtered, (data.units || []).length, dupAddresses);
      renderMap(filtered, dupAddresses);
      updateSortIndicators();
    }}

    // ---- Refresh controls ----
    document.getElementById('reload-btn').addEventListener('click', () => location.reload());

    document.getElementById('howto-btn').addEventListener('click', () => {{
      const panel = document.getElementById('howto-panel');
      panel.style.display = (panel.style.display === 'block') ? 'none' : 'block';
    }});

    document.getElementById('refetch-btn').addEventListener('click', async () => {{
      const btn = document.getElementById('refetch-btn');
      const original = btn.textContent;
      btn.textContent = '\\u23f3 Fetching\\u2026';
      btn.disabled = true;
      try {{
        const resp = await fetch('units.json', {{ cache: 'no-store' }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        renderAll(data);
        btn.textContent = '\\u2713 Updated';
      }} catch (e) {{
        console.error('Re-fetch failed:', e);
        btn.textContent = '\\u2717 Failed (see \\u24d8)';
      }} finally {{
        setTimeout(() => {{ btn.textContent = original; btn.disabled = false; }}, 2000);
      }}
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

    document.getElementById('add-work-location-btn').addEventListener('click', async () => {{
      const nameInput = document.getElementById('work-loc-name-input');
      const addressInput = document.getElementById('work-loc-address-input');
      const status = document.getElementById('work-location-status');
      const addBtn = document.getElementById('add-work-location-btn');

      const name = nameInput.value.trim();
      const address = addressInput.value.trim();
      if (!name || !address) {{
        status.textContent = 'Enter a name and address.';
        return;
      }}

      addBtn.disabled = true;
      status.textContent = 'Looking up address\\u2026';
      try {{
        const coords = await geocodeAddress(address);
        workLocations.push({{ name, address, lat: coords.lat, lon: coords.lon }});
        saveWorkLocations();
        renderWorkLocationsList();
        renderWorkLocationHeaders();
        renderWorkLocationMarkers();
        renderWorkDistanceFilters();
        renderListFiltersSummary();
        nameInput.value = '';
        addressInput.value = '';
        status.textContent = '\\u2713 Added';
        renderAll(lastData);
        setTimeout(() => {{ status.textContent = ''; }}, 1200);
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
    document.querySelectorAll('#columns-panel input[type="checkbox"]').forEach(cb => {{
      cb.checked = isColumnVisible(cb.dataset.col);
    }});
    applyColumnPrefs();
    renderWorkLocationsList();
    renderWorkLocationHeaders();
    renderWorkLocationMarkers();
    renderWorkDistanceFilters();
    renderListFiltersSummary();
    renderAll(INITIAL_DATA);
  </script>
</body>
</html>
'''

    SUMMARY_HTML.write_text(html_content, encoding='utf-8')
    print(f"✓ Generated {SUMMARY_HTML}")
    print(f"  Units within {config.get('search_radius_miles', '?')} mi: {total}")
    print(f"  Total units in database: {units_data.get('total_units', 0)}")
    print(f"  Last updated: {units_data.get('last_updated', 'N/A')}")




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

    .description {
      white-space: pre-wrap;
      font-size: 14px;
      color: var(--text);
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
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
      flex: 0 0 150px;
    }

    .timeline-add-row input[type="text"] {
      flex: 1;
    }

    .timeline-add-row input {
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
'''


def generate_unit_detail_html(unit, lease_end=None, target_location=None):
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
    if contact_phone:
        info_items.append(
            '<div class="info-item"><div class="info-label">Contact Phone</div>'
            f'<div class="info-value"><a href="tel:{html_escape(re.sub(r"[^0-9+]", "", contact_phone))}">{html_escape(contact_phone)}</a></div></div>'
        )
    if contact_email:
        info_items.append(
            '<div class="info-item"><div class="info-label">Contact Email</div>'
            f'<div class="info-value"><a href="mailto:{html_escape(contact_email)}">{html_escape(contact_email)}</a></div></div>'
        )
    if lease_end:
        days_left = (lease_end - datetime.now().date()).days
        info_items.append(
            '<div class="info-item"><div class="info-label">Your Lease Ends</div>'
            f'<div class="info-value">{lease_end.strftime("%b %d, %Y")} ({days_left}d)</div></div>'
        )

    description_html = ''
    notes = unit.get('notes')
    if notes:
        description_html = f'''
  <div class="section">
    <h2>Description (from listing)</h2>
    <div class="description">{html_escape(notes)}</div>
  </div>'''

    photos_html = ''
    photos = unit.get('photos') or []
    if photos:
        rel_paths = [p.replace('outputs/', '') if 'outputs/' in p else p for p in photos]
        imgs = ''.join(
            f'<a href="../../{p}" target="_blank"><img src="../../{p}" alt="Photo" loading="lazy"></a>'
            for p in rel_paths
        )
        photos_html = f'''
  <div class="section">
    <h2>Photos ({len(photos)})</h2>
    <div class="photos">{imgs}</div>
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
  <a class="back-link" href="../../units-summary.html">&larr; Back to list</a>
  <h1>{html_escape(title)}</h1>
  <div class="address">{html_escape(address)}</div>
  <div class="badges">{''.join(badges)}</div>
  <div class="info-grid">{''.join(info_items)}</div>
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
        <input type="text" id="detail-timeline-text" placeholder="e.g. Called landlord, toured unit...">
        <button onclick="addDetailTimelineEntry()">Add</button>
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

    // Distance to each work location is computed client-side from the same
    // `workLocations` list (name/address/lat/lon) saved by the list page
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
        return `<div class="info-item"><div class="info-label">${{escapeHtml(loc.name)}}</div><div class="info-value">${{d}} mi</div></div>`;
      }}).join('');
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
      list.innerHTML = sorted.map(entry => `<div class="timeline-entry">` +
        `<div class="timeline-entry-date">${{formatTimelineDate(entry.date)}}</div>` +
        `<div class="timeline-entry-text">${{escapeHtml(entry.text)}}</div>` +
        `<button class="timeline-entry-remove" title="Remove" onclick="removeDetailTimelineEntry(${{entry.i}})">\\u2715</button>` +
      `</div>`).join('');
    }}

    function addDetailTimelineEntry() {{
      const date = document.getElementById('detail-timeline-date').value;
      const text = document.getElementById('detail-timeline-text').value.trim();
      if (!date || !text) return;
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      const timeline = entry.timeline || [];
      timeline.push({{ date, text }});
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

      const points = [[UNIT_LAT, UNIT_LON]];
      L.marker([UNIT_LAT, UNIT_LON]).addTo(map)
        .bindPopup(`<b>This unit</b><br>${{escapeHtml(UNIT_ADDRESS)}}`)
        .openPopup();

      if (TARGET_LAT != null && TARGET_LON != null) {{
        points.push([TARGET_LAT, TARGET_LON]);
        L.marker([TARGET_LAT, TARGET_LON]).addTo(map)
          .bindPopup(`<b>${{escapeHtml(TARGET_NAME || 'Search center')}}</b>`);
        L.polyline(points, {{ color: '#63b3ed', weight: 2, dashArray: '4 6' }}).addTo(map);
      }}

      if (points.length > 1) {{
        map.fitBounds(points, {{ padding: [30, 30] }});
      }} else {{
        map.setView(points[0], 13);
      }}
      setTimeout(() => map.invalidateSize(), 100);
    }}

    (function() {{
      const overrides = loadDetailOverrides();
      const entry = overrides[UNIT_ID] || {{}};
      document.getElementById('detail-notes-textarea').value = entry.notes || '';
      updateFavToggleBtn(overrides);
      renderCommute();
      document.getElementById('detail-timeline-date').value = new Date().toISOString().slice(0, 10);
      renderDetailTimeline();
      initDetailMap();
    }})();
  </script>
</body>
</html>'''

if __name__ == "__main__":
  generate_html()
