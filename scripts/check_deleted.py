#!/usr/bin/env python3
"""
Check if Craigslist listings have been deleted/expired.

Fetches each Craigslist source_url and marks units as deleted if the page
returns 404 or shows the "This posting has been deleted" message.

Usage:
  python -m scripts.check_deleted
  python -m scripts.check_deleted --remove   # actually remove dead listings
  python -m scripts.check_deleted --dry-run   # just report (default)
"""
import json
import sys
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UNITS_JSON = PROJECT_ROOT / 'outputs' / 'units.json'

DELETED_MARKERS = [
    'this posting has been deleted',
    'this posting has been flagged',
    'this posting has expired',
    'the page you requested is no longer available',
]

REQUEST_DELAY = 1.5  # seconds between requests to avoid rate limiting


def check_url(url):
    """Returns (is_live, reason)."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(50000).decode('utf-8', errors='ignore').lower()
            for marker in DELETED_MARKERS:
                if marker in body:
                    return False, f'page contains "{marker}"'
            return True, 'live'
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, '404 not found'
        if e.code == 410:
            return False, '410 gone'
        return False, f'HTTP {e.code}'
    except urllib.error.URLError as e:
        return None, f'network error: {e.reason}'
    except Exception as e:
        return None, f'error: {e}'


def main():
    parser = argparse.ArgumentParser(description='Check for deleted Craigslist listings')
    parser.add_argument('--remove', action='store_true', help='Remove dead listings from units.json')
    parser.add_argument('--dry-run', action='store_true', help='Just report, don\'t modify (default)')
    args = parser.parse_args()

    if not UNITS_JSON.exists():
        print('No units.json found')
        return

    data = json.loads(UNITS_JSON.read_text(encoding='utf-8'))
    units = data.get('units', [])

    cl_units = [u for u in units if (u.get('source') or '').lower() == 'craigslist']
    print(f'Found {len(cl_units)} Craigslist listings to check\n')

    live = []
    dead = []
    errors = []

    for i, u in enumerate(cl_units):
        uid = u.get('id', '?')
        url = u.get('source_url', '')
        if not url:
            print(f'  [{i+1}/{len(cl_units)}] {uid}: no source_url, skipping')
            continue

        is_live, reason = check_url(url)
        status = 'LIVE' if is_live else ('DEAD' if is_live is False else 'ERROR')
        print(f'  [{i+1}/{len(cl_units)}] {uid}: {status} - {reason}')

        if is_live:
            live.append(uid)
        elif is_live is False:
            dead.append(uid)
        else:
            errors.append(uid)

        if i < len(cl_units) - 1:
            time.sleep(REQUEST_DELAY)

    print(f'\nResults: {len(live)} live, {len(dead)} dead, {len(errors)} errors')

    if dead:
        print(f'\nDead listings: {", ".join(dead)}')

    if args.remove and dead:
        dead_set = set(dead)
        original_count = len(units)
        units = [u for u in units if u.get('id') not in dead_set]
        data['units'] = units
        data['total_units'] = len(units)
        data['last_updated'] = datetime.now().isoformat()
        UNITS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'\nRemoved {original_count - len(units)} dead listings from units.json')
    elif dead and not args.remove:
        print(f'\nRun with --remove to delete dead listings from units.json')


if __name__ == '__main__':
    main()
