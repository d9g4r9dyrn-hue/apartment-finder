import sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # Capture the full response body of the init endpoint
    captured = {}
    def on_response(response):
        if '/reply/' in response.url and '/init' in response.url:
            captured['url'] = response.url
            captured['status'] = response.status
            try:
                captured['body'] = response.text()
            except Exception as e:
                captured['body_err'] = str(e)
    page.on('response', on_response)

    page.goto('https://tampa.craigslist.org/pnl/apa/d/clearwater-open-for-tour-today/7939048315.html',
              wait_until='domcontentloaded', timeout=20000)
    page.wait_for_timeout(2000)

    try:
        page.click('button.reply-button', timeout=5000)
    except Exception as e:
        print('Click failed:', e)

    page.wait_for_timeout(3000)
    browser.close()

print('Captured URL:', captured.get('url'))
print('Status:', captured.get('status'))
body = captured.get('body', '')
print('Body (first 2000 chars):', body[:2000])
try:
    data = json.loads(body)
    print('Parsed JSON keys:', list(data.keys()) if isinstance(data, dict) else type(data))
except Exception:
    pass
