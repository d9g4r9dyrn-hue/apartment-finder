import sys, requests, re
sys.stdout.reconfigure(encoding='utf-8')

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120'}
js_url = 'https://www.craigslist.org/static/www/browsePostings-2026-06-08-17-04-11a8b25e835dde482c1b8166014461c96dbab0fc.js'
r = requests.get(js_url, headers=headers, timeout=30)
js = r.text

# Find the Rs class content - it handles the popup response
idx = js.find('class Rs extends t.Component')
if idx >= 0:
    print('Rs class:')
    print(js[idx:idx+5000])
