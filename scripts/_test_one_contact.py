"""Test 2captcha contact fetch on a single unit before running all."""
import sys, json, os, time
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

key = os.environ.get('TWOCAPTCHA_API_KEY', '')
print('API key set:', bool(key), f'({key[:8]}...{key[-4:]})' if key else '(EMPTY)')
if not key:
    print('ERROR: Set $env:TWOCAPTCHA_API_KEY first')
    sys.exit(1)

with open('outputs/units.json', encoding='utf-8') as f:
    data = json.load(f)
units = data['units']

# Pick first unit without contact info
no_contact = [u for u in units if u.get('source') == 'Craigslist'
              and not u.get('contact_phone') and not u.get('contact_email')]
print(f'Units needing contact: {len(no_contact)}/{len(units)}')

if not no_contact:
    print('All units already have contact info!')
    sys.exit(0)

unit = no_contact[0]
print(f'\nTesting on: {unit["id"]} -> {unit["source_url"]}')
print('Starting fetch...')

sys.path.insert(0, '.')
from scripts.scrapers.craigslist import fetch_cl_contact_via_2captcha
try:
    result = fetch_cl_contact_via_2captcha(unit['source_url'], key)
    print(f'\nResult: {result}')
    if result:
        print('SUCCESS - contact info retrieved:')
        for k, v in result.items():
            print(f'  {k}: {v}')
    else:
        print('No contact info returned (listing may have no contact options)')
except Exception as e:
    print(f'\nERROR: {e}')
    import traceback; traceback.print_exc()
