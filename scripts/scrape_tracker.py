#!/usr/bin/env python3
"""
Scrape activity tracker — logs every scraper run, photo scan, and captcha solve
to outputs/scrape_log.json. Provides status reporting.

Usage as library:
  from scripts.scrape_tracker import ScrapeTracker
  tracker = ScrapeTracker()
  run = tracker.start_run('zumper')
  run.found(25)
  run.added(10)
  run.skipped(15)
  run.captcha()
  run.error('timeout on page 3')
  run.finish()
  tracker.print_status()

Usage standalone:
  python -m scripts.scrape_tracker              # print status
  python -m scripts.scrape_tracker --reset       # clear log
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
LOG_FILE = OUTPUTS_DIR / 'scrape_log.json'


def _load_log():
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'runs': [], 'summary': {}}


def _save_log(data):
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


class RunTracker:
    def __init__(self, source, tracker):
        self.source = source
        self.tracker = tracker
        self.data = {
            'source': source,
            'started': datetime.now().isoformat(),
            'finished': None,
            'status': 'in_progress',
            'listings_found': 0,
            'listings_added': 0,
            'listings_skipped': 0,
            'photos_downloaded': 0,
            'captchas_encountered': 0,
            'captchas_solved': 0,
            'image_scans': 0,
            'errors': [],
            'duration_seconds': 0,
            'pages_scraped': 0,
        }
        self._start_time = time.time()
        # Mark in-progress in the log immediately
        log = _load_log()
        log['runs'].append(self.data)
        _save_log(log)

    def found(self, count):
        self.data['listings_found'] += count

    def added(self, count=1):
        self.data['listings_added'] += count

    def skipped(self, count=1):
        self.data['listings_skipped'] += count

    def photo(self, count=1):
        self.data['photos_downloaded'] += count

    def captcha(self, solved=False):
        self.data['captchas_encountered'] += 1
        if solved:
            self.data['captchas_solved'] += 1

    def image_scan(self, count=1):
        self.data['image_scans'] += count

    def page(self, count=1):
        self.data['pages_scraped'] += count

    def error(self, msg):
        self.data['errors'].append(str(msg))

    def finish(self, status='completed'):
        self.data['finished'] = datetime.now().isoformat()
        self.data['status'] = status
        self.data['duration_seconds'] = round(time.time() - self._start_time, 1)
        # Update the last entry in the log
        log = _load_log()
        for i in range(len(log['runs']) - 1, -1, -1):
            if (log['runs'][i]['source'] == self.source
                    and log['runs'][i]['status'] == 'in_progress'):
                log['runs'][i] = self.data
                break
        self._update_summary(log)
        _save_log(log)

    def _update_summary(self, log):
        summary = log.get('summary', {})
        src = self.source
        if src not in summary:
            summary[src] = {
                'total_runs': 0,
                'total_added': 0,
                'total_found': 0,
                'total_captchas': 0,
                'total_image_scans': 0,
                'total_photos': 0,
                'last_run': None,
                'total_errors': 0,
            }
        s = summary[src]
        s['total_runs'] += 1
        s['total_added'] += self.data['listings_added']
        s['total_found'] += self.data['listings_found']
        s['total_captchas'] += self.data['captchas_encountered']
        s['total_image_scans'] += self.data['image_scans']
        s['total_photos'] += self.data['photos_downloaded']
        s['total_errors'] += len(self.data['errors'])
        s['last_run'] = self.data['finished'] or self.data['started']
        log['summary'] = summary


class ScrapeTracker:
    def start_run(self, source):
        return RunTracker(source, self)

    def log_image_scan(self, source, count=1, captchas=0, captchas_solved=0):
        """Log a photo scan / AI analysis run without a full scrape context."""
        log = _load_log()
        entry = {
            'source': f'{source}_scan',
            'started': datetime.now().isoformat(),
            'finished': datetime.now().isoformat(),
            'status': 'completed',
            'listings_found': 0,
            'listings_added': 0,
            'listings_skipped': 0,
            'photos_downloaded': 0,
            'captchas_encountered': captchas,
            'captchas_solved': captchas_solved,
            'image_scans': count,
            'errors': [],
            'duration_seconds': 0,
            'pages_scraped': 0,
        }
        log['runs'].append(entry)
        summary = log.get('summary', {})
        key = f'{source}_scan'
        if key not in summary:
            summary[key] = {
                'total_runs': 0, 'total_added': 0, 'total_found': 0,
                'total_captchas': 0, 'total_image_scans': 0,
                'total_photos': 0, 'last_run': None, 'total_errors': 0,
            }
        summary[key]['total_runs'] += 1
        summary[key]['total_image_scans'] += count
        summary[key]['total_captchas'] += captchas
        summary[key]['last_run'] = entry['finished']
        log['summary'] = summary
        _save_log(log)

    def get_in_progress(self):
        log = _load_log()
        return [r for r in log['runs'] if r['status'] == 'in_progress']

    def get_status(self):
        log = _load_log()
        return log.get('summary', {}), log.get('runs', [])

    def print_status(self):
        summary, runs = self.get_status()
        in_progress = [r for r in runs if r['status'] == 'in_progress']

        print('=' * 60)
        print('SCRAPE TRACKER STATUS')
        print('=' * 60)

        if in_progress:
            print('\nIN PROGRESS:')
            for r in in_progress:
                elapsed = ''
                try:
                    start = datetime.fromisoformat(r['started'])
                    elapsed = f' ({int((datetime.now() - start).total_seconds())}s ago)'
                except Exception:
                    pass
                print(f'  {r["source"]}: {r["listings_added"]} added, '
                      f'{r["listings_found"]} found{elapsed}')

        if summary:
            print('\nSOURCE TOTALS:')
            print(f'  {"Source":<20} {"Runs":>5} {"Found":>7} {"Added":>7} '
                  f'{"Photos":>7} {"Captchas":>9} {"Scans":>6} {"Errors":>7} {"Last Run":<20}')
            print(f'  {"-"*18:<20} {"---":>5} {"-----":>7} {"-----":>7} '
                  f'{"------":>7} {"--------":>9} {"-----":>6} {"------":>7} {"-"*18:<20}')
            for src, s in sorted(summary.items()):
                last = s.get('last_run', '?')
                if last and len(last) > 19:
                    last = last[:19]
                print(f'  {src:<20} {s["total_runs"]:>5} {s["total_found"]:>7} '
                      f'{s["total_added"]:>7} {s["total_photos"]:>7} '
                      f'{s["total_captchas"]:>9} {s["total_image_scans"]:>6} '
                      f'{s["total_errors"]:>7} {last:<20}')

        # Recent runs (last 10)
        recent = [r for r in runs if r['status'] != 'in_progress'][-10:]
        if recent:
            print(f'\nRECENT RUNS (last {len(recent)}):')
            for r in reversed(recent):
                ts = r.get('started', '?')[:19]
                dur = r.get('duration_seconds', 0)
                errs = len(r.get('errors', []))
                err_str = f' [{errs} errors]' if errs else ''
                print(f'  {ts}  {r["source"]:<16} +{r["listings_added"]} added '
                      f'({r["listings_found"]} found, {r["listings_skipped"]} skipped) '
                      f'{r["photos_downloaded"]} photos  {dur}s{err_str}')

        if not summary and not runs:
            print('\n  No scraping activity recorded yet.')

        print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Scrape activity tracker')
    parser.add_argument('--reset', action='store_true', help='Clear the scrape log')
    args = parser.parse_args()

    if args.reset:
        _save_log({'runs': [], 'summary': {}})
        print('Scrape log cleared.')
        return

    tracker = ScrapeTracker()
    tracker.print_status()


if __name__ == '__main__':
    main()
