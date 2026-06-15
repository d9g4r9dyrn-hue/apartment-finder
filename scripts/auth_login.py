#!/usr/bin/env python3
"""
Interactive auth helper for gated sources.
Usage: python scripts/auth_login.py <source_id> <login_url>

Example:
  python scripts/auth_login.py apartments_com https://www.apartments.com/login/

This will open a browser for you to log in and save Playwright storage state to `outputs/auth/{source_id}-storage.json`.
"""
import sys
from scripts.scrapers.common import interactive_login

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python scripts/auth_login.py <source_id> <login_url>')
        sys.exit(1)
    source_id = sys.argv[1]
    login_url = sys.argv[2]
    interactive_login(source_id, login_url)
