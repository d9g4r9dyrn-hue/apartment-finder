import sys, re
sys.stdout.reconfigure(encoding='utf-8')
with open('outputs/units-summary.html', encoding='utf-8') as f:
    html = f.read()

m = re.search(r'class="contact-col[^"]*"[^>]*>[^<]*</th>', html)
print('Contact TH:', m.group() if m else 'NOT FOUND')

phones = re.findall(r'tel:[^"]+', html)
print('Tel links:', phones[:5])

m2 = re.search(r'lf-has-contact[^>]+>', html)
print('Filter checkbox:', m2.group() if m2 else 'NOT FOUND')

m3 = re.search(r'data-col="contact"[^>]+>', html)
print('Column toggle:', m3.group() if m3 else 'NOT FOUND')

# Check contact phone data in unit JSON
idx = html.find('(601) 488-7525')
print('Phone in data:', 'FOUND at', idx if idx >= 0 else 'NOT FOUND')
