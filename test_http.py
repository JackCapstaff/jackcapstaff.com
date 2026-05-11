#!/usr/bin/env python
"""Test script to check HTTP responses."""

import requests

test_urls = [
    'http://127.0.0.1:5000/',
    'http://127.0.0.1:5000/index.html',
    'http://127.0.0.1:5000/login',
    'http://127.0.0.1:5000/admin/',
    'http://127.0.0.1:5000/contact',
]

for url in test_urls:
    try:
        resp = requests.get(url, allow_redirects=False)
        print(f"{url:50} -> {resp.status_code} {resp.reason}")
        if resp.status_code in (301, 302, 303, 307, 308):
            print(f"  Redirects to: {resp.headers.get('Location')}")
    except Exception as e:
        print(f"{url:50} -> ERROR: {e}")
