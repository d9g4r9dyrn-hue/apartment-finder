#!/usr/bin/env python3
"""
Master crawler script that runs all apartment listing scrapers.
Aggregates results into outputs/units.json and downloads photos.

Usage: python3 scripts/crawl_all.py
       python3 scripts/crawl_all.py --source craigslist
       python3 scripts/crawl_all.py --source zillow --max 10
"""
import sys
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# Ensure emoji/unicode in print() output doesn't crash on Windows consoles (cp1252)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Available scrapers
SCRAPERS = {
    'craigslist': 'scripts/scrapers/craigslist.py',
    'zillow': 'scripts/scrapers/zillow.py',
    'apartments_com': 'scripts/scrapers/apartments_com.py',
    'trulia': 'scripts/scrapers/trulia.py',
}


def run_scraper(scraper_module):
    """Run a single scraper script"""
    # Prefer invoking as a module so relative package imports work
    script_path = PROJECT_ROOT / scraper_module
    if not script_path.exists():
        print(f"❌ Scraper not found: {script_path}")
        return False

    # Convert path like 'scripts/scrapers/craigslist.py' -> 'scripts.scrapers.craigslist'
    module_name = scraper_module.replace('/', '.').replace('\\', '.')
    if module_name.endswith('.py'):
        module_name = module_name[:-3]

    print(f"\n{'='*60}")
    print(f"Running module: {module_name}")
    print(f"{'='*60}")

    try:
        result = subprocess.run(
            ['python', '-m', module_name],
            cwd=PROJECT_ROOT,
            check=False
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Error running scraper module: {e}")
        return False


def run_all_scrapers():
    """Run all available scrapers"""
    print("🔍 Starting comprehensive apartment search...")
    print(f"📍 Target location: Check config.json")
    print()

    results = {}
    for source_id, scraper_module in SCRAPERS.items():
        success = run_scraper(scraper_module)
        results[source_id] = '✓' if success else '✗'

    print(f"\n{'='*60}")
    print("📊 Scraper Results Summary")
    print(f"{'='*60}")
    for source_id, status in results.items():
        print(f"  {status} {source_id}")

    print(f"\n✓ All scrapers completed!")
    print(f"📁 Results saved to: outputs/units.json")
    print(f"📸 Photos saved to: outputs/photos/")

    # Regenerate the HTML dashboard
    print(f"\n{'='*60}")
    print("Regenerating dashboard HTML...")
    result = subprocess.run(['python', 'scripts/generate-html.py'], cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        print("  ⚠ HTML generation failed — open outputs/units-summary.html manually")
        return

    # Auto-publish to GitHub Pages if hosting is already set up (docs/ exists)
    docs_dir = PROJECT_ROOT / 'docs'
    git_dir  = PROJECT_ROOT / '.git'
    if git_dir.exists() and docs_dir.exists():
        print(f"\n{'='*60}")
        print("Publishing to GitHub Pages...")
        try:
            subprocess.run(['python', 'scripts/publish.py'], cwd=PROJECT_ROOT, check=True)
        except subprocess.CalledProcessError:
            print("  ⚠ Publish failed — run 'python scripts/publish.py' manually")
    else:
        print()
        print("Next steps:")
        print("  1. Open: outputs/units-summary.html in browser")
        print("  (Run 'python scripts/publish.py' to publish to GitHub Pages)")


def main():
    parser = argparse.ArgumentParser(
        description='Run apartment listing scrapers'
    )
    parser.add_argument(
        '--source',
        choices=list(SCRAPERS.keys()),
        help='Run specific scraper only'
    )
    parser.add_argument(
        '--max',
        type=int,
        default=24,
        help='Maximum listings per scraper (default: 24)'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available scrapers'
    )
    
    args = parser.parse_args()
    
    if args.list:
        print("Available scrapers:")
        for source_id, module in SCRAPERS.items():
            print(f"  - {source_id}: {module}")
        return
    
    if args.source:
        run_scraper(SCRAPERS[args.source])
    else:
        run_all_scrapers()


if __name__ == '__main__':
    main()
