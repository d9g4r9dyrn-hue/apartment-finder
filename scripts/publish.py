#!/usr/bin/env python3
"""
Sanitize outputs/ and publish to docs/ for GitHub Pages.

Replaces the precise target_location address/lat/lon with a generalized
placeholder so the public demo doesn't expose your exact search center.
Then commits and pushes docs/ to the GitHub remote so GitHub Pages updates.

Usage: python scripts/publish.py
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR  = PROJECT_ROOT / 'outputs'
DOCS_DIR     = PROJECT_ROOT / 'docs'
CONFIG_JSON  = PROJECT_ROOT / 'config.json'


def load_config():
    if not CONFIG_JSON.exists():
        print('config.json not found — cannot publish')
        sys.exit(1)
    return json.loads(CONFIG_JSON.read_text(encoding='utf-8'))


def generalized_location(target):
    """Return a sanitized version of target_location for the public demo."""
    if not target:
        return {'name': 'Search Area', 'address': 'Search area', 'lat': 0.0, 'lon': 0.0}
    addr = target.get('address', '')
    parts = [p.strip() for p in addr.split(',')]
    # Build "City, ST" from the tail of the comma-separated address
    if len(parts) >= 3:
        city = parts[-2].strip()
        state = parts[-1].strip().split()[0]
        city_state = f'{city}, {state}'
    elif len(parts) == 2:
        city_state = ', '.join(parts[-2:]).strip()
    else:
        city_state = addr[:30] if addr else 'Area'
    return {
        'name': city_state,
        'address': f'{city_state} area',
        'lat': round(target.get('lat', 0), 2),
        'lon': round(target.get('lon', 0), 2),
    }


def sanitize(text, real, pub):
    """Replace real location strings with public placeholders in HTML/JS."""
    for key in ('address', 'name'):
        if real.get(key):
            text = text.replace(json.dumps(real[key]), json.dumps(pub[key]))
    for key in ('lat', 'lon'):
        if real.get(key) is not None:
            text = text.replace(str(real[key]), str(pub[key]))
    return text


def copy_photos(src_dir, dest_dir):
    """Copy only new/changed photos (avoids full re-copy on each publish)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for photo in src_dir.rglob('*.jpg'):
        rel  = photo.relative_to(src_dir)
        dest = dest_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != photo.stat().st_size:
            shutil.copy2(photo, dest)
            count += 1
    return count


def build_docs(real_loc, pub_loc):
    DOCS_DIR.mkdir(exist_ok=True)

    # Root redirect so github.io/repo-name/ goes straight to the dashboard
    index = DOCS_DIR / 'index.html'
    if not index.exists():
        index.write_text(
            '<!doctype html><html><head><meta charset="UTF-8">'
            '<meta http-equiv="refresh" content="0; url=units-summary.html">'
            '</head><body><a href="units-summary.html">View dashboard</a></body></html>',
            encoding='utf-8'
        )

    # Main summary page
    src = OUTPUTS_DIR / 'units-summary.html'
    if src.exists():
        content = sanitize(src.read_text(encoding='utf-8'), real_loc, pub_loc)
        (DOCS_DIR / 'units-summary.html').write_text(content, encoding='utf-8')
        print('  units-summary.html')

    # units.json (the Re-fetch button loads this; target_location not embedded)
    src_json = OUTPUTS_DIR / 'units.json'
    if src_json.exists():
        shutil.copy2(src_json, DOCS_DIR / 'units.json')
        print('  units.json')

    # Per-unit detail pages
    apts_src  = OUTPUTS_DIR / 'apartments'
    apts_dest = DOCS_DIR    / 'apartments'
    if apts_src.exists():
        n = 0
        for detail in apts_src.rglob('index.html'):
            rel  = detail.relative_to(apts_src)
            dest = apts_dest / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = sanitize(detail.read_text(encoding='utf-8'), real_loc, pub_loc)
            dest.write_text(content, encoding='utf-8')
            n += 1
        print(f'  {n} detail pages')

    # Photos
    photos_src  = OUTPUTS_DIR / 'photos'
    photos_dest = DOCS_DIR    / 'photos'
    if photos_src.exists():
        n = copy_photos(photos_src, photos_dest)
        total = sum(1 for _ in photos_dest.rglob('*.jpg'))
        print(f'  {n} new photos copied ({total} total)')


def git_push():
    """Stage docs/, commit if anything changed, and push."""
    subprocess.run(['git', 'add', 'docs/'], cwd=PROJECT_ROOT, check=True)
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=PROJECT_ROOT)
    if diff.returncode == 0:
        print('Nothing new to publish (docs/ already up to date)')
        return False
    ts = time.strftime('%Y-%m-%d %H:%M')
    subprocess.run(
        ['git', 'commit', '-m', f'Update listings [{ts}]'],
        cwd=PROJECT_ROOT, check=True
    )
    subprocess.run(['git', 'push'], cwd=PROJECT_ROOT, check=True)
    return True


def main():
    config   = load_config()
    real_loc = config.get('target_location') or {}
    pub_loc  = generalized_location(real_loc)

    print(f'Sanitizing: "{real_loc.get("address")}" -> "{pub_loc["address"]}"')
    print('Building docs/:')
    build_docs(real_loc, pub_loc)
    print('Publishing to GitHub...')
    pushed = git_push()
    if pushed:
        print('Pushed — GitHub Pages will update in ~30 seconds.')
    print('Done.')


if __name__ == '__main__':
    main()
