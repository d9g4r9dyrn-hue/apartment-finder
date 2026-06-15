#!/usr/bin/env python3
import json
import math
import sys
from pathlib import Path

# Ensure unicode in unit data doesn't crash on Windows consoles (cp1252)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
units = json.loads((ROOT / 'outputs' / 'units.json').read_text(encoding='utf-8')).get('units', [])

RADIUS = config.get('search_radius_miles')
MIN_BEDS = config.get('min_beds')
MIN_BATHS = config.get('min_baths')
MIN_SQFT = config.get('min_sqft')
MIN_PRICE = config.get('min_price')
MAX_PRICE = config.get('max_price')


def haversine(lat1, lon1, lat2, lon2):
    R = 3959
    lat1, lat2, lon1, lon2 = map(math.radians, (lat1, lat2, lon1, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


target = config.get('target_location') or {}
tl = target.get('lat')
tt = target.get('lon')

within = []
for u in units:
    lat = u.get('lat')
    lon = u.get('lon')
    dist = None
    if lat is not None and lon is not None and tl is not None and tt is not None:
        try:
            dist = haversine(tl, tt, lat, lon)
        except Exception:
            dist = None
    reasons = []
    if dist is None:
        reasons.append('no-latlon')
    else:
        if RADIUS is not None and dist > RADIUS:
            reasons.append(f'distance={dist:.1f}mi>radius')
    beds = u.get('beds') or 0
    if MIN_BEDS is not None and beds < MIN_BEDS:
        reasons.append(f'beds={beds}<min_beds({MIN_BEDS})')
    baths = u.get('baths')
    baths_val = baths if baths is not None else 0
    if MIN_BATHS is not None and baths_val < MIN_BATHS:
        reasons.append(f'baths={baths_val}<min_baths({MIN_BATHS})')
    sqft = u.get('sqft') or 0
    if MIN_SQFT is not None and sqft < MIN_SQFT:
        reasons.append(f'sqft={sqft}<min_sqft({MIN_SQFT})')
    price = u.get('price') or 0
    if MIN_PRICE is not None and price < MIN_PRICE:
        reasons.append(f'price={price}<min_price({MIN_PRICE})')
    if MAX_PRICE is not None and price > MAX_PRICE:
        reasons.append(f'price={price}>max_price({MAX_PRICE})')

    included = len(reasons) == 0
    if dist is not None and RADIUS is not None and dist <= RADIUS:
        within.append((u, dist, included, reasons))

# Output summary
print(f"Config: radius={RADIUS}, min_beds={MIN_BEDS}, min_baths={MIN_BATHS}, min_sqft={MIN_SQFT}, min_price={MIN_PRICE}, max_price={MAX_PRICE}\n")
print(f"Total units in DB: {len(units)}")
print(f"Units with lat/lon within radius: {len(within)}\n")

for u, dist, included, reasons in within:
    print(
        f"{u.get('id')} | {u.get('address')} | beds={u.get('beds')} | baths={u.get('baths')} | sqft={u.get('sqft')} | price={u.get('price')} | dist={dist:.1f}mi | included={included} | reasons={'None' if included else ','.join(reasons)}"
    )

# Show closest 10 overall
all_with_dist = []
for u in units:
    lat = u.get('lat')
    lon = u.get('lon')
    d = None
    if lat is not None and lon is not None and tl is not None and tt is not None:
        try:
            d = haversine(tl, tt, lat, lon)
        except Exception:
            d = None
    all_with_dist.append((d if d is not None else 1e9, u))
all_with_dist.sort(key=lambda x: x[0])

print('\nClosest 10 units:')
for d, u in all_with_dist[:10]:
    dstr = f"{d:.1f}mi" if d < 1e9 else 'N/A'
    print(f"{u.get('id')} | {u.get('address')} | beds={u.get('beds')} | baths={u.get('baths')} | sqft={u.get('sqft')} | dist={dstr}")
