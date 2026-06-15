
**Handoff — Claude VS Code Session**

Purpose
 - Provide everything a developer using Claude in VS Code on another machine needs to inspect, run, and continue work on this project after you copy files over.

Quick summary
 - The repository generates an HTML summary table from `outputs/units.json` using `scripts/generate-html.py`.
 - The output is `outputs/units-summary.html` (compact table with thumbnails) and per-unit pages under `outputs/apartments/<unit-id>/index.html`.

Files to copy (minimum)
 - `scripts/generate-html.py`
 - `scripts/crawl.py` (crawler; may require network credentials)
 - `outputs/units.json`
 - `outputs/units-summary.html` (generated; useful reference)
 - `outputs/photos/` (contains all downloaded thumbnails and photos)
 - `outputs/apartments/` (optional — contains generated detail pages)

Environment
 - Python 3.x is required. Use `python3` on macOS/Linux.
 - No external Python packages required for `generate-html.py` (pure stdlib).
 - Working directory: project root (where `scripts/` lives).

Quick run (copy these commands exactly)

```bash
cd /path/to/project
python3 scripts/generate-html.py
```

Expected outcomes
 - Script exits reporting `✓ Generated outputs/units-summary.html` and prints the total units and last updated timestamp.
 - `outputs/units-summary.html` displays a compact table with rows for each unit, thumbnails in the first column, clickable addresses, price, beds, baths, sqft, amenities, and source.

Verification checklist (exact steps Claude should follow)
 1. Confirm Python availability:

```bash
python3 --version
```

2. Run generator and capture terminal output:

```bash
python3 scripts/generate-html.py | tee generate-output.txt
```

  - Verify `generate-output.txt` contains `✓ Generated outputs/units-summary.html`.

3. Open `outputs/units-summary.html` in the browser (recommended: VS Code Live Preview or open file URL).
  - Verify header shows the expected total units and last updated date.
  - Verify the table header is sticky when scrolling.
  - For at least 3 rows, confirm:
    - Thumbnail image is visible (or placeholder if no photo).
    - Address link opens the `source_url` in a new tab.
    - Price column shows `$<value>`.
    - Beds/Baths/Sqft columns have sensible values.

4. Spot-check detail pages for two units: open `outputs/apartments/<unit-id>/index.html` (where `<unit-id>` is from `units.json`) and verify title, photos, and move-in info render.

5. If thumbnails do not display, ensure `outputs/photos/` was copied and paths are relative to the `outputs/` folder.

Troubleshooting common failures
 - `python3: command not found` → install Python 3 or use system package manager. On macOS: `brew install python`.
 - JSON errors when running script → run `python3 -c "import json,sys; json.load(open('outputs/units.json')); print('ok')"` to surface parse errors.
 - Missing photos → confirm file names under `outputs/photos/` match entries in `outputs/units.json`.

Relevant files to inspect
 - `scripts/generate-html.py` — primary generator; edit this to change layout, columns, or write extra files.
 - `outputs/units.json` — canonical data source for listings.
 - `outputs/units-summary.html` — the generated table; useful to preview UI without re-running the script.

Developer notes (what changed recently)
 - The summary layout was changed from a card-based design to a compact HTML table with 70×70 thumbnails to improve information density.
 - `scripts/generate-html.py` contains helper functions that create per-unit detail pages; modify `generate_unit_detail_html()` for richer unit pages.

Suggested next tasks for Claude (prioritized)
 1. Add client-side filtering (by price range, beds) using the `data-price`, `data-beds`, and `data-baths` attributes added to each row.
 2. Add sorting controls and a small JS helper to sort rows client-side.
 3. Improve the Amenities column by mapping boolean fields (e.g., `has_washer_dryer`) to icons/badges.
 4. Implement a thumbnail lightbox or hover preview for quick viewing without opening detail pages.
 5. Add a small test or validation script to ensure `units.json` conforms to expected schema before running generation.

Transfer checklist (what you must copy)
 - Entire project directory (preferred) OR at minimum:
   - `scripts/` directory
   - `outputs/units.json`
   - `outputs/photos/` folder
   - `HANDOFF_CLAUDE_VS_CODE.md`

Notes about credentials & network
 - The crawler (`scripts/crawl.py`) may use external network sources (Craigslist, etc.) and sometimes requires rate-limiting or authentication. If running `crawl.py`, review the script for any stored API keys or credentials and do not copy secrets insecurely.

How to provide results back to you
 - After running the generator, compress the `outputs/` folder and upload or transfer it back.
 - If Claude makes UI changes, commit to a branch and provide diffs (or copy updated `scripts/generate-html.py`).

Session context link
 - Point Claude to this file and the repo root; include the chat transcript if available for reasoning and recent decisions.

Contact
 - If you want me to add screenshots or example expected outputs, say which units or how many sample rows to capture.

