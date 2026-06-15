#!/usr/bin/env python3
"""Geocode unit addresses from outputs/units.json.

This script geocodes unit addresses using Nominatim (OpenStreetMap) and caches
results to `outputs/geocache.json`. It updates `outputs/units.json` with
`lat` and `lon` fields for each unit. The combined map + summary page
(outputs/units-summary.html, built by generate-html.py) renders the map
directly from units.json, so this script only needs to keep coordinates fresh.
"""
import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"
UNITS_FILE = OUTPUTS / "units.json"
GEOCACHE_FILE = OUTPUTS / "geocache.json"


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def geocode_address(address, session, cache):
    if not address:
        return None
    if address in cache:
        return cache[address]
    # Try provider order: Google (if key), OpenCage (if key), Nominatim
    google_key = os.environ.get('GOOGLE_GEOCODE_API_KEY')
    opencage_key = os.environ.get('OPENCAGE_API_KEY')
    headers = {"User-Agent": "apartment-poc/1.0 (+https://example.com)"}

    def try_nominatim(q):
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": q, "format": "json", "limit": 1}
        resp = session.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}

    def try_google(q):
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": q, "key": google_key}
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') != 'OK' or not data.get('results'):
            return None
        loc = data['results'][0]['geometry']['location']
        return {"lat": float(loc['lat']), "lon": float(loc['lng'])}

    def try_opencage(q):
        url = "https://api.opencagedata.com/geocode/v1/json"
        params = {"q": q, "key": opencage_key, "limit": 1}
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get('results'):
            return None
        geom = data['results'][0]['geometry']
        return {"lat": float(geom['lat']), "lon": float(geom['lng'])}

    # try variations
    candidates = [address, f"{address}, USA"]
    # also try dropping apartment/unit markers
    if '#' in address:
        candidates.append(address.split('#', 1)[0].strip())

    for q in candidates:
        try:
            if google_key:
                res = try_google(q)
                if res:
                    cache[address] = res
                    return res
            if opencage_key:
                res = try_opencage(q)
                if res:
                    cache[address] = res
                    return res
            res = try_nominatim(q)
            if res:
                cache[address] = res
                time.sleep(1)
                return res
        except Exception:
            # try next provider/variation
            continue

    cache[address] = None
    return None


def main():
    units_data = load_json(UNITS_FILE, {})
    units = units_data.get("units", [])
    if not units:
        print("No units found in", UNITS_FILE)
        return

    geocache = load_json(GEOCACHE_FILE, {})
    session = requests.Session()

    default_center = (27.9468822, -82.725352)
    for u in units:
        if "lat" in u and "lon" in u:
            continue
        address = u.get("address")
        geo = geocode_address(address, session, geocache)
        if geo:
            u["lat"] = geo["lat"]
            u["lon"] = geo["lon"]
            print(f"Geocoded {u.get('id','?')} -> {geo['lat']},{geo['lon']}")
        else:
            print(f"Failed to geocode: {address}; placing at default center")
            u["lat"] = default_center[0]
            u["lon"] = default_center[1]

    # save geocache and updated units
    save_json(GEOCACHE_FILE, geocache)
    units_data["units"] = units
    save_json(UNITS_FILE, units_data)
    print(f"Updated lat/lon for {len(units)} units in {UNITS_FILE}")


if __name__ == "__main__":
    main()
