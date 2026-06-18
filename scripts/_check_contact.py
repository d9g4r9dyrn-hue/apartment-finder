import sys, json, re
sys.stdout.reconfigure(encoding='utf-8')

with open('outputs/units.json', encoding='utf-8') as f:
    data = json.load(f)
units = data['units']

# Loose phone regex: 10 total digits, possibly separated by spaces/dashes/dots
# Strategy: find any sequence where stripping non-digits gives 10-11 digits
PHONE_BROAD = re.compile(
    r'(\(?\d{3}\)?[\s.\-]{0,2}\d{3}[\s.\-]{0,2}\d{2,4}[\s.\-]{0,2}\d{0,4})'
)
EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}')

def extract_phone(text):
    """Find 10-digit phone numbers with flexible separators."""
    for m in PHONE_BROAD.finditer(text):
        digits = re.sub(r'\D', '', m.group(1))
        if len(digits) == 10:
            return m.group(1).strip(), digits
        elif len(digits) == 11 and digits[0] == '1':
            d = digits[1:]
            return m.group(1).strip(), d
    return None, None

found = 0
for u in units:
    notes = u.get('notes', '')
    raw_phone, digits = extract_phone(notes)
    email_match = EMAIL_RE.search(notes)
    if raw_phone or email_match:
        found += 1
        print(f"{u['id']} [{u['source']}]: phone={raw_phone!r} digits={digits} email={email_match.group() if email_match else None}")

print(f'\nFound phone/email in description: {found}/{len(units)}')
print()

# Summary by source
from collections import defaultdict
by_source = defaultdict(lambda: {'total': 0, 'with_phone': 0, 'with_email': 0})
for u in units:
    src = u.get('source', 'unknown')
    by_source[src]['total'] += 1
    notes = u.get('notes', '')
    raw_phone, _ = extract_phone(notes)
    if raw_phone:
        by_source[src]['with_phone'] += 1
    if EMAIL_RE.search(notes):
        by_source[src]['with_email'] += 1

for src, counts in by_source.items():
    print(f"{src}: {counts['with_phone']}/{counts['total']} phone, {counts['with_email']}/{counts['total']} email")
