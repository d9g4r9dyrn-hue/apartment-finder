#!/usr/bin/env python3
"""Remove the sample/template unit (unit-0000) from outputs and delete its files."""
import json
import shutil
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent
UNITS = ROOT / 'outputs' / 'units.json'
APARTMENTS_DIR = ROOT / 'outputs' / 'apartments'


def main():
    if not UNITS.exists():
        print('No units.json found.')
        return
    data = json.loads(UNITS.read_text())
    units = data.get('units', [])
    new_units = [u for u in units if u.get('id') != 'unit-0000']
    removed = len(units) - len(new_units)
    if removed == 0:
        print('No sample unit found.')
        return
    data['units'] = new_units
    data['total_units'] = len(new_units)
    data['last_updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    UNITS.write_text(json.dumps(data, indent=2))
    # remove per-unit directory if exists
    sample_dir = APARTMENTS_DIR / 'unit-0000'
    if sample_dir.exists():
        try:
            shutil.rmtree(sample_dir)
            print(f'Removed directory: {sample_dir}')
        except Exception as e:
            print('Failed to remove sample directory:', e)
    print(f'Removed {removed} sample unit(s) from {UNITS}')


if __name__ == '__main__':
    main()
