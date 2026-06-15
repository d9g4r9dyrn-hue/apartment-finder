# Work Log

Running notes on in-progress work for this project. Not user-facing docs -
just a scratchpad so work-in-progress survives context resets.

## Active task batch (2026-06-15) - DONE

Queued requests, in order received:
1. AI photo "quality" rating (1-5 stars) + photo backfill during sync
2. "Our details" link/page per unit (separate from source listing link)
3. Resizable divider between sidebar (map) and list panel
4. Editable "your address" + "lease end date" criteria (persisted)
5. Expand criteria editor to cover radius/price/beds/baths/sqft too

### Status
- [x] `scripts/scan_photos.py` - photo backfill (Craigslist re-fetch) +
      Claude vision quality scan (needs `anthropic` package +
      `ANTHROPIC_API_KEY`, NOT configured in this env - script prints setup
      instructions and skips the scan gracefully).
- [x] `requirements.txt` - added `anthropic>=0.40.0`
- [x] `config.json` - added `"min_quality": null`
- [x] Quality column (stars) in table + min-quality filter dropdown
      (`currentMinQuality` / `lastData` re-render pattern)
- [x] "Details" link column -> `apartments/{id}/index.html`, detail pages
      fully rewritten with dark theme + all scraped fields
- [x] Resizable drag-divider between `.sidebar` and `.list-panel`
      (persists `sidebarWidth` to localStorage, calls `map.invalidateSize()`)
- [x] Editable criteria panel: address (Nominatim geocode), lease end date,
      radius, min/max price, min/max beds, min baths, min sqft - all
      persisted via a single `configOverrides` localStorage blob
      (`OVERRIDE_KEYS` + `saveOverrides()`), with a Reset button to clear
      overrides and reload config.json defaults.
- [x] Ran `scan_photos.py`, regenerated `units-summary.html` + detail pages

### Bug fixed along the way
- Craigslist scraper truncated `notes` (description) to 500 chars with no
  ellipsis, cutting descriptions off mid-word/sentence. Fixed
  `scrapers/craigslist.py` to store the full description. Added
  `backfill_descriptions()` to `scan_photos.py` to re-fetch and replace any
  previously-truncated (500-char) notes for existing Craigslist units - 12 of
  18 units backfilled with full descriptions (now run as part of the normal
  `scan_photos.py` pass, `--skip-descriptions` to skip).
- unit-0019's Craigslist listing genuinely has 0 photos (confirmed by
  fetching it directly - no `id="thumbs"` gallery, no `og:image`, 0 `<img>`
  tags). Not a bug - just a listing without photos.

### Notes / decisions
- Quality column placed right after Photo column. Stars: `★` filled /
  `☆` empty, `—` if unrated; cell `title` attr shows `quality_notes`.
- `min_quality` filter affects BOTH table and map via `filterUnits`.
- Detail pages live at `outputs/apartments/{id}/index.html`, dark theme
  matching summary page.

## Active task batch 2 (2026-06-15) - DONE

Queued requests, in order received:
1. Move Quality column to the end of the table
2. Switch AI photo quality scan from Anthropic to Google Gemini
3. Add favorites (star toggle) + editable per-unit notes, with a
   "Favorites only" filter
4. Make table column headers sortable

### Status
- [x] Quality column moved to second-to-last position (before Details).
- [x] `scan_photos.py` rewritten to use Google Gemini (`gemini-2.5-flash`
      via `google-genai`, `GEMINI_API_KEY` env var, free tier at
      https://aistudio.google.com/apikey). `requirements.txt` updated
      (`anthropic` -> `google-genai`). Verified graceful skip message when
      no key is set.
- [x] Favorites + notes: new `unitOverrides` localStorage blob
      (`{ "<unitId>": { favorite: bool, notes: string } }`), purely
      client-side (no units.json changes). New "Mine" column with
      star (favorite) and pencil (notes) icon buttons, a notes-editing
      modal, a "Favorites only" filter dropdown, favorite star + notes
      shown in map popups, and a "Your Notes" section (favorite toggle +
      notes textarea) added to each unit detail page with its own small
      inline script reading/writing the same `unitOverrides` key.
- [x] Sortable column headers: Mine (favorite), Distance, Address, Price,
      Beds, Baths, Sqft, Type, Source, Quality - click to sort
      asc/desc with ▲/▼ indicator; `currentSort` state, `sortUnits()`,
      `applySort()`, `updateSortIndicators()`, applied in `renderAll`
      after filtering.
- [x] Compiled, regenerated `units-summary.html` + detail pages (16/18
      units within radius), spot-checked generated markup.

## Active task batch 3 (2026-06-15) - DONE

Queued requests, in order received:
1. Run the Gemini quality scan (user provided a `GEMINI_API_KEY`)
2. Add a "List filters" panel to narrow the displayed list (distance,
   price, beds, baths, sqft) without changing the search criteria
3. Add a short unique unit ID to each row (rows can otherwise look
   identical)
4. Add a progress indicator to the quality-scan CLI output

### Status
- [x] Ran `scan_photos.py --skip-backfill --skip-descriptions` with the
      provided Gemini key. Free tier is 5 requests/minute AND 20
      requests/day for `gemini-2.5-flash` - the original 0.5s delay blew
      through the per-minute limit immediately. Fixed:
      `generate_content_with_retry()` (retries 429/503 with the
      server-suggested `retryDelay`, falling back to 10s) and
      `QUALITY_REQUEST_DELAY = 13` between requests.
      Result: 13/18 units now rated. unit-0019 has no photos (not a bug,
      confirmed earlier). unit-0020 through unit-0023 hit the **daily**
      20-request quota - rerun `scan_photos.py --skip-backfill
      --skip-descriptions` tomorrow (quota resets ~24h) to pick those up
      (already-rated units are skipped automatically without `--rescan`).
- [x] New "List filters" panel in the sidebar (below the existing Type/
      Quality/Favorites row): max distance, min/max price, min beds, min
      baths, min sqft. State lives in a `listFilters` JS object, applied in
      `filterUnits` on top of the CONFIG-driven criteria filters. Not
      persisted (resets on reload) - "Edit criteria" remains the persisted
      scrape/fetch criteria, "List filters" is a session-only display
      narrowing. Toggled via an `.criteria-edit`-style collapsible panel
      with a "Clear" button and a live summary string.
- [x] Every row's address cell now shows a small monospace `unit-XXXX` id
      tag (`.unit-id-tag`) below the address, so visually-identical rows
      (same address/specs) can still be told apart/referenced.
- [x] `scan_quality()` in `scan_photos.py` now prints `[i/N] Rating
      quality for unit-XXXX...` based on the pre-computed list of units
      that actually need scanning.
- [x] Regenerated `units-summary.html` + detail pages.

## Active task batch 4 (2026-06-15) - DONE

Queued requests, in order received:
1. Show quality as a number (e.g. "4.5 ★") instead of star-glyph strings
2. Scan unit photos for primary flooring type (tile, carpet, hardwood,
   etc.) and add it as a column
3. Add a column-visibility selector, similar to the "List filters" panel

### Status
- [x] Quality rendering changed from `★★★★☆` repeated-glyph strings to a
      single numeric value + star, e.g. `4.0 ★` (table `qualityStarsHtml`,
      detail-page badge in `generate_unit_detail_html`). Uses
      `rating.toFixed(1)` / `f'{quality_rating:.1f}'` so half-star ratings
      would render correctly if Gemini ever returns one.
- [x] `QUALITY_PROMPT` in `scan_photos.py` extended to ask Gemini for a
      `"flooring"` field in the same JSON response as `rating`/`notes`
      (one of `hardwood`/`tile`/`carpet`/`vinyl`/`concrete`/`mixed`/
      `unknown`) - reuses the existing quality-scan API call instead of a
      second call, to stay within the 20/day free-tier quota.
      `rate_unit_quality()` now returns `(rating, notes, flooring)`;
      `scan_quality()` stores `unit['flooring_type']`. The "needs scan"
      check (`todo` list) now also triggers for units missing
      `flooring_type`, so already-rated units get backfilled with flooring
      on their next scan without needing `--rescan`.
- [x] New "Flooring" column in the table (between Amenities and Source),
      sortable via `data-sort="flooring_type"`. `flooringHtml()` shows the
      capitalized flooring type, or `—` if unscanned/`unknown`. Detail
      pages show a `"<Flooring> flooring"` badge when known.
- [x] New "Columns" panel in the sidebar (below "List filters"), styled
      with the same `.criteria-bar`/`.criteria-edit` pattern: a 2-column
      grid of checkboxes (one per toggleable column - everything except
      Address) that show/hide table columns via `.units-table.hide-<col>`
      CSS classes. State lives in `columnPrefs` (`{col: false}` for hidden
      columns) and IS persisted to `localStorage` (`columnPrefs`) - unlike
      `listFilters`, this is a display *preference* that should survive
      reloads, not a one-off narrowing of results. "Show All" button
      resets it. `beds`/`baths`/`sqft` got their own `beds-col`/
      `baths-col`/`sqft-col` classes (previously shared `spec-col`) so they
      can be toggled independently.
- [x] Re-ran `scan_photos.py --skip-backfill --skip-descriptions` to
      backfill `flooring_type` for already-rated units and pick up units
      0020-0023 (yesterday's daily-quota casualties).
      Result: only **1 request** was available in today's quota (still
      exhausted from yesterday) - unit-0000 was re-scanned under the
      refined prompt (4/5 stars, tile flooring), then the daily quota hit
      again on unit-0003.
- [x] **Bug fix**: discovered `generate_content_with_retry()` was retrying
      *daily*-quota 429 errors the same as per-minute ones - Gemini
      returns a real `retryDelay` (e.g. `57s`) even for the daily quota, so
      each unit burned ~4 retries x ~1min before giving up, i.e. the scan
      would have taken over an hour just to fail on all 17 remaining
      units. Fixed: a 429 whose message contains `PerDay` now raises
      `DailyQuotaExceeded` immediately (no retries); `scan_quality()`
      catches it and `break`s out of the loop right away with "daily
      Gemini quota reached - stopping scan, try again tomorrow."
- [x] Regenerated `units-summary.html` + detail pages. 1/18 units now have
      `flooring_type` (unit-0000 = tile).
- [x] User provided a second fresh `GEMINI_API_KEY`, giving a fresh 20/day
      quota. Reran `scan_photos.py --skip-backfill --skip-descriptions`:
      processed units 0003-0015 (9 more units, up to 4 retries each for
      per-minute rate limits) before hitting the new key's daily quota on
      unit-0016. Now **10/18 units have `flooring_type` and 13/18 have
      `quality_rating`** (unit-0019 has no photos; unit-0016/0017/0018 have
      `quality_rating` but no `flooring_type` from an earlier scan;
      unit-0020-0023 still unscanned). Regenerated
      `units-summary.html` + detail pages again.
- [x] User provided 2 more keys. Original first key (from earlier today)
      had regained 2 requests - picked up unit-0016 (4/5, vinyl) and
      unit-0017 (5/5, tile) before hitting its daily quota again on
      unit-0018. The second new key was already fully exhausted (failed
      immediately on unit-0018). Regenerated `units-summary.html` + detail
      pages again.
      Now **12/18 units have `flooring_type` and 13/18 have
      `quality_rating`**. Still need scanning: unit-0018, unit-0020,
      unit-0021, unit-0022, unit-0023 (unit-0019 excluded - no photos).
- [x] User upgraded to a paid Gemini plan and provided 2 more keys (one
      "expired", then a working one). The working key hit "API key
      expired" (400 INVALID_ARGUMENT / API_KEY_INVALID) on 3 of 5 requests
      but succeeded on the other 2 (unit-0021 5->4/5 mixed, unit-0022
      3/5 mixed) - no `PerDay` quota errors at all this run, consistent
      with the paid-tier upgrade lifting the daily cap. The intermittent
      "expired" error was clearly transient (same key, same run, mixed
      results), so **fixed `generate_content_with_retry()`** to also retry
      on `API_KEY_INVALID` with the same backoff as 429/503. Reran for the
      3 remaining units (0018, 0020, 0023) - all succeeded on retry (1-2
      retries each).
      **Final result: 17/18 units have `flooring_type` and
      `quality_rating`** (unit-0019 has no photos, by design - not a bug).
      Regenerated `units-summary.html` + detail pages - feature complete,
      no more scans needed unless new units are added.

## Active task batch 5 (2026-06-15) - DONE

Queued requests, in order received:
1. Show houses and apartments differently on the map (by color)
2. Track named "work locations" (e.g. Cort Work, Gia Work) and show a
   distance column per location

### Status
- [x] Map markers now use `L.divIcon` colored dots instead of the default
      blue pin: teal for Apartment, orange for House, gray for any other
      `housing_type` (`markerClassForType()` / `markerIcon()`). A small
      legend below the map (`#map-legend`, `renderMapLegend()`) shows which
      color maps to which type, built dynamically from the types present.
- [x] New "Work locations" panel in the sidebar (same `.criteria-bar`/
      `.criteria-edit` pattern as Columns/List filters): add a named
      location (e.g. "Cort Work") by address, geocoded via Nominatim
      (`geocodeAddress()`, same API as the home-address editor) and stored
      in `workLocations` (persisted to `localStorage`). Each saved location
      gets a sortable table column (`work_dist_<i>`, inserted before
      "Details" via `renderWorkLocationHeaders()`) showing the distance in
      miles from that unit to the work location (`haversineMiles`, computed
      in `filterUnits`). Locations can be removed via an "x" button, which
      drops the column.
- [x] Compiled, regenerated `units-summary.html`.

## Active task batch 6 (2026-06-15) - DONE

Queued requests, in order received:
1. Determine move-in date for properties, include in list
2. Populate baths (was empty for everything)
3. Populate amenities (was blank in the list)
4. Add Flooring to list filters, plus any other fields where a list filter
   makes sense
5. In the map, show more pictures (4-photo grid) when clicking properties
6. In the map, show which markers are houses vs apartments on hover/click

### Status
- [x] `scrapers/craigslist.py`: `extract_specs()` now also parses baths and
      move-in availability from the `attr important` badges (`2BR / 1Ba`,
      `available aug 15` / `available now`); `extract_move_in_date()` is a
      free-text fallback ("move-in ready" language). `extract_amenities()`
      parses the `attrgroup` `div.attr` tags (pet policy, laundry, parking,
      A/C, etc.), excluding `rent_period` and `housing_type=` links;
      `normalize_amenities()` derives `has_washer_dryer`/`is_gated`. New
      `unit` fields: `baths`, `move_in_date`, `amenities`,
      `has_washer_dryer`, `is_gated`.
- [x] `scan_photos.py`: mirrored extraction helpers
      (`extract_craigslist_specs`, `extract_move_in_date`,
      `extract_amenities`, `normalize_amenities`) plus `backfill_specs()`
      (triggers when `baths is None`) and `backfill_amenities()` (triggers
      when `amenities` is empty), both re-fetching the Craigslist listing
      once. Ran both backfills: all 18 units now have `baths` (1.0-2.5) and
      non-empty `amenities`; 3 units got a `move_in_date` (unit-0000/0003 =
      "now", unit-0015 = "2026-08-15"; the rest are unlisted -> `null`).
- [x] New "Move-in" column (sortable via `move_in_sort` - "now" sorts first,
      ISO dates by day, unknown last) + Columns-panel entry + detail-page
      badge (`moveInHtml()` / `move_in_sort_key()`).
- [x] List filters panel gained: Flooring dropdown, "Available by" date
      filter (move-in), Washer/Dryer checkbox, Gated checkbox - each wired
      into `filterUnits`/`applyListFilters`/`clearListFilters`/
      `renderListFiltersSummary`. Did not add a Source filter (only one
      source - Craigslist - exists currently).
- [x] Map popups now show up to 4 photos in a `.popup-photo-grid` (2x2,
      mirroring the list's thumb-grid), each clickable via the existing
      `openGallery()`/lightbox.
- [x] Map tooltip (hover) and popup (click) both gained a colored-dot +
      housing-type label (`.tooltip-type` / `.popup-type`, reusing
      `markerClassForType()` so colors match the map legend).
- [x] Bonus (in scope of "anything in the list... make sense"): added
      `groupByAddress()` so units at the same address (different
      floorplans) sit adjacent in the table regardless of sort, with
      `.same-property` (left accent border) / `.group-continues` (no
      border between grouped rows) styling. Per-row grouping is computed
      precisely in `renderTable` (address equality with the next row), not
      via a `:has()` CSS heuristic.
- [x] Deleted temporary debug scripts `_debug_avail.py` and
      `_debug_amenities.py`.
- [x] Compiled all changed files, regenerated `units-summary.html` + detail
      pages.

## Active task batch 7 (2026-06-15) - DONE

Queued requests, in order received:
1. The vertically-stacked List filters/Columns/Work locations panels were
   eating vertical space (even collapsed), cutting into the map. Put them
   side-by-side horizontally like Housing type/Min quality/Favorites, and
   review the overall sidebar organization for the wide-desktop +
   tall-mobile layouts.
2. Format square footage and price with thousands separators; left-justify
   text columns, right-justify numbers.
3. Track age restrictions (e.g. "55+ waterfront condo").
4. The amenities column's recycle-icon glyph was meaningless - show top
   amenities as icons (+ hover for the full list) instead.
5. Make the map's housing-type filter match the list (e.g. "apartment
   only" hides house markers too).
6. Add a free-text search (top-left of sidebar): "contains" / "doesn't
   contain" boxes that filter the list (incl. descriptions) in realtime,
   comma-separated terms (e.g. match anything containing "gated" but
   nothing containing "55+").

### Status
- [x] Replaced the old stacked `.criteria-bar` blocks for List filters/
      Columns/Work locations with a `.tools-row` of compact `.tool-btn`
      buttons (each with a small count `.tool-badge` when active, e.g.
      "2 hidden" columns or "3" work locations) that open a `.tools-panel`
      popover below the row - only one panel open at a time
      (`toggleToolPanel()`). This row sits alongside the existing Housing
      type/Min quality/Favorites selects below the map, so none of it
      consumes vertical space while collapsed.
- [x] Added a `@media (max-width: 860px)` block so the sidebar stacks above
      the list/table on tall/narrow (mobile) viewports, hides the resize
      `.resizer`, and gives `#map` a 16:9 aspect ratio instead of a fixed
      height.
- [x] New `formatNumber()` JS helper (`toLocaleString('en-US')`) applied to
      price/sqft in the table and map popups; Python detail-page badges use
      `:,` formatting for price/sqft. Column CSS updated so text columns
      (type, flooring, source, amenities, etc.) are left-aligned and numeric
      columns (price, beds/baths/sqft, distance, quality) are right-aligned.
- [x] `scrapers/craigslist.py`: new `extract_age_restriction()` matches
      "55+", "55 plus", "55 and older"/"62 or over" style language in the
      title/description, stored as `unit['age_restriction']` (int or
      `None`). Backfilled all 18 existing units via a one-off script (3
      units are 55+: unit-0003, unit-0004, unit-0013). Shown as a red "55+"
      badge next to Housing type in the table and on detail pages, plus a
      "Hide age-restricted (55+)" checkbox in the List filters panel.
- [x] Amenities column rewritten: `AMENITY_ICON_MAP` maps amenity-text
      patterns (laundry/W-D, gated, parking, pets, A/C, pool, wheelchair
      access, EV charging, furnished) to emoji icons; `amenitiesCellHtml()`
      shows up to 2 matched icons + a "+N" overflow badge, with the full
      raw amenities list in the cell's hover tooltip.
- [x] Map/list type-filter sync: `currentTypeFilter` (driven by the
      Housing-type select) is now applied once in `renderAll()` before both
      `renderTable()` and `renderMap()`, so selecting e.g. "Apartment" hides
      house markers on the map too (previously the map always showed every
      type).
- [x] New search row at the top of the sidebar: `#search-contains` /
      `#search-excludes` text inputs, `searchableText()` (title, address,
      description, housing type, source, flooring, move-in date, amenities,
      "<N>+" for age-restricted) and `applySearchFilters()`, wired into
      `filterUnits()`. Both fields accept comma-separated terms; "contains"
      requires at least one term to match, "excludes" rejects on any match.
- [x] **Bug fix**: the map legend dots under the map were always gray
      regardless of housing type - `.map-legend-dot` had no `background`,
      and the existing `.map-marker.marker-apartment/-house/-other` color
      rules only applied to elements with BOTH `.map-marker` and the
      type-specific class, not `.map-legend-dot`. Added
      `.map-legend-dot.marker-apartment/-house/-other` to those same rules
      so the legend now shows teal/orange/gray matching the map markers.
- [x] Compiled, regenerated `units-summary.html` + detail pages (16/18
      units within radius).

## Active task batch 8 (2026-06-15) - DONE

Queued requests, in order received:
1. The map/legend dots for apartment vs. house were both gray (legend bug
   from batch 7's verification pass).
2. Include "distance to work" (for all configured work locations) as an
   attribute in the list AND on unit detail pages.
3. Show the work locations themselves as markers on the map.
4. The map marker cluster grouping was too aggressive - units several
   blocks apart were being grouped into one "N properties" blob; only
   cluster markers that would actually overlap on screen.

### Status
- [x] **Bug fix** (map legend colors): see batch 7 entry - `.map-legend-dot`
      had no background; added `.map-legend-dot.marker-apartment/-house/
      -other` alongside the existing `.map-marker.*` rules so legend dots
      now show teal/orange/gray matching the map markers.
- [x] New `--purple` CSS var + `.map-marker-work` (rounded-square divIcon
      with a \U0001f4bc briefcase glyph) and `.map-legend-dot.marker-work`.
      New `workLocationMarkers` layer group (`L.layerGroup()`, added to map
      alongside the unit `markers` cluster group) + `workLocationIcon()` +
      `renderWorkLocationMarkers()` - plots one marker per saved work
      location (skips any without geocoded lat/lon), each with a popup
      showing its name + address. Called on initial load and whenever a
      work location is added/removed. `renderMapLegend()` appends a "Work
      location" legend entry whenever `workLocations.length > 0`.
- [x] Unit detail pages gained a "Commute" section (`#commute-list`,
      `.info-grid`): a small inline script (mirrors the existing favorites/
      notes pattern) reads `workLocations` from `localStorage`, embeds the
      unit's `UNIT_LAT`/`UNIT_LON`, and renders one `.info-item` per work
      location with its name and `haversineMiles()` distance in miles.
      Shows a placeholder message if no work locations are configured yet,
      or if the unit has no lat/lon. (The table's existing `work_dist_<i>`
      columns already covered "in the list".)
- [x] **Bug fix** (map clustering too aggressive): `L.markerClusterGroup()`
      used Leaflet's default `maxClusterRadius` of 80px, which grouped units
      several blocks apart into one cluster blob even though they're far
      enough apart to render individually. Reduced to `maxClusterRadius: 20`
      so clustering now only kicks in for markers that would visually
      overlap (e.g. multiple floorplans at the same address).
- [x] Compiled, regenerated `units-summary.html` + detail pages.

## Design note: scrape criteria vs. display/filter criteria (2026-06-15)

Currently `config.json` criteria (radius, price, beds, baths, sqft) serve a
dual purpose: (1) they're sent as query params to scrapers (e.g. Craigslist
`build_search_url`) to control what gets PULLED during sync, and (2) the same
values drive the client-side `filterUnits` that controls what's SHOWN on the
summary page/map. This is the current intended design - no action needed now.

Future possibility raised by user: separate "pull radius" (wider, e.g. 10mi,
used only for scraping) from "display radius" (narrower, e.g. 2mi, used for
the list/map filter), so you can cast a wider net during sync without
cluttering the default view. Not implemented - just noting for later.

Related minor inconsistency observed: `max_beds` is collected and shown in
the criteria text (and sent to Craigslist as a scrape param), but is NOT
applied as a hard filter in either `filter_units_by_distance` (Python) or
`filterUnits` (JS) - unlike `min_beds`/`min_price`/`max_price`/etc. Now that
`max_beds` is editable in the same panel as filters that DO apply, this could
read as inconsistent. Left as-is for now since it mirrors existing behavior;
worth revisiting if/when the scrape-vs-display split above is tackled.
