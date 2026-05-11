#!/usr/bin/env python
"""Test login and admin access."""

import requests

session = requests.Session()

# Test login
print("Testing login...")
resp = session.post('http://127.0.0.1:5000/login', data={
    'identifier': 'admin',
    'password': 'admin123'
}, allow_redirects=False)
print(f"Login response: {resp.status_code} {resp.reason}")
if resp.status_code in (301, 302, 303, 307, 308):
    print(f"  Redirects to: {resp.headers.get('Location')}")

# Test admin access
print("\nTesting /admin/ access...")
resp = session.get('http://127.0.0.1:5000/admin/', allow_redirects=False)
print(f"/admin/ response: {resp.status_code} {resp.reason}")
if resp.status_code in (301, 302, 303, 307, 308):
    print(f"  Redirects to: {resp.headers.get('Location')}")

# Test /admin/news
print("\nTesting /admin/news access...")
resp = session.get('http://127.0.0.1:5000/admin/news', allow_redirects=False)
print(f"/admin/news response: {resp.status_code} {resp.reason}")
if resp.status_code in (301, 302, 303, 307, 308):
    print(f"  Redirects to: {resp.headers.get('Location')}")
