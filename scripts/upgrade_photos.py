#!/usr/bin/env python3
"""
Upgrade existing Realtor.com listing photos:
 - Replace small thumbnails with full-size originals
 - Fetch detail pages to discover additional photos
 - Re-download upgraded URLs

Usage:
  python -m scripts.upgrade_photos
  python -m scripts.upgrade_photos --source realtor.com
  python -m scripts.upgrade_photos --unit unit-0072
"""
import json
import re
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
PHOTOS_DIR = OUTPUTS_DIR / 'photos'
UNITS_JSON = OUTPUTS_DIR / 'units.json'

from scripts.scrapers.common import download_image
from scripts.scrapers.realtor_com import upsize_photo_url, fetch_detail_photos, HEADERS

MAX_PHOTOS = 30


def main():
    parser = argparse.ArgumentParser(description='Upgrade photos for existing listings')
    parser.add_argument('--source', type=str, default='Realtor.com', help='Source to upgrade (default: Realtor.com)')
    parser.add_argument('--unit', type=str, default=None, help='Specific unit ID to upgrade')
    parser.add_argument('--skip-detail', action='store_true', help='Skip fetching detail pages')
    args = parser.parse_args()

    if not UNITS_JSON.exists():
        print('No units.json found')
        return

    data = json.loads(UNITS_JSON.read_text(encoding='utf-8'))
    units = data.get('units', [])

    targets = []
    for u in units:
        if args.unit and u.get('id') != args.unit:
            continue
        if not args.unit and (u.get('source') or '').lower() != args.source.lower():
            continue
        targets.append(u)

    print(f'Upgrading photos for {len(targets)} units\n')

    upgraded = 0
    for i, u in enumerate(targets):
        uid = u.get('id', '?')
        source_url = u.get('source_url', '')
        old_sources = u.get('photo_sources', [])
        old_photos = u.get('photos', [])

        # Upsize existing URLs
        new_sources = [upsize_photo_url(url) for url in old_sources]

        # Fetch detail page for more photos
        if not args.skip_detail and source_url:
            print(f'  [{i+1}/{len(targets)}] {uid}: fetching detail page...')
            detail_photos = fetch_detail_photos(source_url)
            if detail_photos:
                seen = set(new_sources)
                added_from_detail = 0
                for dp in detail_photos:
                    if dp not in seen:
                        new_sources.append(dp)
                        seen.add(dp)
                        added_from_detail += 1
                if added_from_detail:
                    print(f'    Found {added_from_detail} new photos from detail page')
            time.sleep(3)

        new_sources = new_sources[:MAX_PHOTOS]

        # Figure out which photos need downloading
        needs_download = []
        photo_dir = PHOTOS_DIR / uid
        for j, url in enumerate(new_sources):
            dest = photo_dir / f'photo-{j + 1}.jpg'
            if j < len(old_sources) and old_sources[j] == url and dest.exists():
                continue  # same URL, already downloaded
            needs_download.append((j, url, dest))

        if not needs_download and len(new_sources) == len(old_sources):
            print(f'  [{i+1}/{len(targets)}] {uid}: already up to date ({len(old_sources)} photos)')
            continue

        # Download new/changed photos
        photo_paths = []
        final_sources = []
        photo_dir.mkdir(parents=True, exist_ok=True)

        download_indices = {j for j, _, _ in needs_download}
        for j, url in enumerate(new_sources):
            dest = photo_dir / f'photo-{j + 1}.jpg'
            rel = (Path('outputs') / 'photos' / uid / dest.name).as_posix()

            if j in download_indices:
                try:
                    download_image(url, dest)
                    photo_paths.append(rel)
                    final_sources.append(url)
                    time.sleep(0.3)
                except Exception as e:
                    print(f'    Photo {j+1} failed: {e}')
                    if dest.exists():
                        photo_paths.append(rel)
                        final_sources.append(url)
            else:
                photo_paths.append(rel)
                final_sources.append(url)

        old_count = len(old_photos)
        new_count = len(photo_paths)
        u['photos'] = photo_paths
        u['photo_sources'] = final_sources
        upgraded += 1
        print(f'  [{i+1}/{len(targets)}] {uid}: {old_count} -> {new_count} photos')

    if upgraded:
        data['last_updated'] = datetime.now().isoformat()
        UNITS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'\nUpgraded {upgraded} units')
    else:
        print('\nAll photos already up to date')


if __name__ == '__main__':
    main()
