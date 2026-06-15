# Apartment Listing Scrapers

This directory contains scrapers for multiple apartment rental sources.

## Architecture

- **`common.py`** — Shared utilities for HTTP requests, image downloads, and authentication
- **`craigslist.py`** — Craigslist text-based scraper (most reliable)
- **`zillow.py`** — Zillow scraper (may be blocked; needs workarounds)
- **`apartments_com.py`** — Apartments.com scraper (JS-heavy)
- **`trulia.py`** — Trulia scraper (similar to Zillow)

## Running Scrapers

### All Sources (Recommended)
```bash
python scripts/crawl_all.py
```

### Single Source
```bash
# Craigslist (most reliable)
python scripts/scrapers/craigslist.py

# Zillow
python scripts/scrapers/zillow.py

# Apartments.com
python scripts/scrapers/apartments_com.py

# Trulia
python scripts/scrapers/trulia.py
```

### With Options
```bash
# Run only Craigslist with max 10 listings
python scripts/crawl_all.py --source craigslist --max 10

# List available scrapers
python scripts/crawl_all.py --list
```

## Source-Specific Notes

### Craigslist ✓
- **Status**: Working and reliable
- **Method**: HTML parsing with BeautifulSoup
- **Rate Limiting**: Respects delays between requests
- **Blocking**: Minimal issues; watch for CAPTCHAs

### Zillow ⚠️
- **Status**: Works but often blocked
- **Method**: Attempts HTML parsing; suggests Playwright for JS rendering
- **Rate Limiting**: Recommended delays of 2+ seconds
- **Blocking**: Actively blocks scrapers; may need:
  - Reverse proxy service
  - Playwright with headless browser
  - Rotating user agents
  - IP rotation

**Workaround**: Use Playwright for JS rendering:
```bash
pip install playwright
# Then the scraper will attempt JS rendering
```

### Apartments.com ⚠️
- **Status**: Partially working; limited by JS
- **Method**: HTML parsing (incomplete; JS blocks most content)
- **Rate Limiting**: 2-second delays
- **Blocking**: Heavy JS dependency; needs Playwright

**Workaround**: Install and use Playwright:
```bash
pip install playwright
python -m playwright install
```

### Trulia ⚠️
- **Status**: Similar to Zillow
- **Method**: HTML parsing (limited)
- **Rate Limiting**: 2-second delays
- **Blocking**: JS-heavy; benefits from Playwright

## Configuration

### Distance Filtering
Edit `config.json` to set your target location and search radius:
```json
{
  "target_location": {
    "name": "Bluestone on the Bay",
    "address": "19135 US Hwy 19 N, Clearwater, FL 33764",
    "lat": 27.946713,
    "lon": -82.7268793
  },
  "search_radius_miles": 5
}
```

The HTML generator automatically:
- Calculates distance for each listing
- Filters listings outside the radius
- Displays distance in the summary table

## Output

All scrapers output to:
- **`outputs/units.json`** — Canonical database of listings
- **`outputs/photos/{unit-id}/`** — Downloaded property photos
- **`outputs/units-summary.html`** — Browseable summary (run `python scripts/generate-html.py`)

## Troubleshooting

### "Module not found: requests"
```bash
pip install requests beautifulsoup4
```

### "Zillow/Apartments.com returns no listings"
The site is blocking the scraper or uses heavy JavaScript. Solutions:
1. Try installing Playwright and using a browser-based scraper
2. Wait a while and try again (IP rate limiting)
3. Use a VPN or proxy
4. Try other sources

### "Image downloads failing"
Check:
- Network connectivity
- SSL certificate issues: `pip install certifi`
- URL is valid and accessible

### "Distance calculation not working"
Ensure `config.json` exists and has valid lat/lon coordinates for target location.

## Adding New Sources

To add a scraper for a new source:

1. Create `scripts/scrapers/{source_name}.py`
2. Implement scraper following the pattern of `craigslist.py`:
   - Load units from `UNITS_JSON`
   - Fetch listings from source
   - Extract: address, price, beds, baths, sqft, photos, source_url
   - Download photos to `PHOTOS_DIR`
   - Save back to `UNITS_JSON`
3. Add to `SCRAPERS` dict in `crawl_all.py`
4. Test: `python scripts/crawl_all.py --source {source_name}`

## Rate Limiting & Ethics

All scrapers include:
- Respectful delays (2+ seconds between requests)
- Proper User-Agent headers
- Avoidance of CAPTCHA triggers

Always respect:
- Website Terms of Service
- robots.txt
- Legal requirements in your jurisdiction

## Monitoring Scraper Health

Check status of each source:
```bash
python scripts/crawl_all.py --list
```

Review output in `outputs/sources.json` for last_checked and status.

## Future Improvements

- [ ] Implement Playwright-based JS rendering for Zillow/Apartments.com
- [ ] Add API-based scrapers where available
- [ ] Implement caching to avoid re-downloading unchanged listings
- [ ] Add price history tracking
- [ ] Implement phone/contact extraction where available
- [ ] Add amenities parsing with NLP
