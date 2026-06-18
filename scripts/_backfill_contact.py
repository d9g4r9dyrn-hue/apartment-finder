"""One-time backfill: re-scan existing unit notes for phone/email."""
import sys, json, re
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from scripts.scrapers.craigslist import extract_contact_info

with open('outputs/units.json', encoding='utf-8') as f:
    data = json.load(f)
units = data['units']

updated = 0
for u in units:
    # Only backfill if fields are missing/null
    if u.get('contact_phone') or u.get('contact_email'):
        continue
    notes = u.get('notes', '')
    info = extract_contact_info(notes)
    if info:
        u['contact_phone'] = info.get('contact_phone')
        u['contact_email'] = info.get('contact_email')
        updated += 1
        print(f"  {u['id']}: phone={u['contact_phone']} email={u['contact_email']}")

data['units'] = units
with open('outputs/units.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f'\nBackfilled {updated} units.')
