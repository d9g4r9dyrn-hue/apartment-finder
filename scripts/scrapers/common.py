"""
Common scraper helpers: HTTP fetch, photo download, and Playwright interactive login helpers.
"""
import json
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUTS_DIR = PROJECT_ROOT / 'outputs'
AUTH_DIR = OUTPUTS_DIR / 'auth'
AUTH_DIR.mkdir(parents=True, exist_ok=True)


def fetch_html(url, session=None, headers=None, timeout=15):
    sess = session or requests.Session()
    hdrs = headers or {"User-Agent": "apartment-poc-bot/1.0 (+https://example.com)"}
    r = sess.get(url, headers=hdrs, timeout=timeout)
    r.raise_for_status()
    return r.text, sess


def get_storage_path_for_source(source_id):
    return AUTH_DIR / f"{source_id}-storage.json"


def get_authenticated_session(source_id):
    """Return a requests.Session preloaded with cookies from Playwright storage state if available."""
    s = requests.Session()
    storage_path = get_storage_path_for_source(source_id)
    if storage_path.exists():
        cookie_dict = load_requests_cookies_from_playwright_storage(storage_path)
        apply_cookies_to_session(s, cookie_dict)
    return s


def fetch_html_with_auth(url, source_id=None, session=None, headers=None, timeout=15):
    """Fetch HTML using an authenticated session for source_id when provided.

    If `session` is provided it is used; otherwise if `source_id` has saved storage,
    a requests.Session will be created with those cookies applied.
    """
    sess = session
    if not sess and source_id:
        sess = get_authenticated_session(source_id)
    sess = sess or requests.Session()
    hdrs = headers or {"User-Agent": "apartment-poc-bot/1.0 (+https://example.com)"}
    r = sess.get(url, headers=hdrs, timeout=timeout)
    r.raise_for_status()
    return r.text, sess


def download_image(url, dest_path, timeout=20):
    """Download image from URL and save to dest_path"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in r.iter_content(1024 * 8):
            f.write(chunk)
    return dest_path


# Playwright helpers (optional dependency)
try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


def interactive_login(source_id, login_url, browser='chromium', headless=False):
    """
    Launch a Playwright browser for interactive login and save storage state (cookies/localStorage).
    Usage:
      interactive_login('apartments_com', 'https://www.apartments.com/login')

    This will open a browser window where you can log in. Once you close the page, the storage
    state will be written to `outputs/auth/{source_id}-storage.json`.
    """
    if not sync_playwright:
        raise RuntimeError('Playwright is not installed. Install dependencies from requirements.txt')

    storage_path = AUTH_DIR / f"{source_id}-storage.json"

    with sync_playwright() as p:
        browser_launch = getattr(p, browser)
        b = browser_launch.launch(headless=headless)
        context = b.new_context()
        page = context.new_page()
        print(f"Opening browser for interactive login to: {login_url}")
        page.goto(login_url)
        print("Please complete login in the opened browser window. When finished, close the browser to save state.")
        try:
            # keep the script running until the browser is closed by the user
            page.wait_for_close()
        except Exception:
            pass
        # save storage state
        state = context.storage_state()
        with open(storage_path, 'w') as f:
            json.dump(state, f)
        print(f"Saved storage state to: {storage_path}")
        try:
            b.close()
        except Exception:
            pass
        return storage_path


def load_requests_cookies_from_playwright_storage(storage_path):
    """Convert Playwright storage state JSON to a requests-compatible cookiejar dict.
    Returns dict of cookie_name->value for setting in requests.Session().
    """
    if not Path(storage_path).exists():
        return {}
    st = json.loads(Path(storage_path).read_text())
    cookies = st.get('cookies', [])
    cookie_dict = {}
    for c in cookies:
        cookie_dict[c.get('name')] = c.get('value')
    return cookie_dict


def apply_cookies_to_session(session, cookie_dict):
    for k, v in cookie_dict.items():
        session.cookies.set(k, v)
    return session
