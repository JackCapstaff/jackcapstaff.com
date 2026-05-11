#!/usr/bin/env python
"""Test admin panel with login."""

import requests

session = requests.Session()

# Log in
print("1. Logging in...")
resp = session.post('http://127.0.0.1:5000/login', data={
    'identifier': 'admin',
    'password': 'admin123'
}, allow_redirects=False)
print(f"   Login: {resp.status_code} {resp.reason}")
if resp.status_code in (301, 302, 303, 307, 308):
    print(f"   Redirects to: {resp.headers.get('Location')}")

# Access admin dashboard
print("\n2. Accessing /admin/...")
resp = session.get('http://127.0.0.1:5000/admin/', allow_redirects=False)
print(f"   /admin/: {resp.status_code} {resp.reason}")
print(f"   Content length: {len(resp.content)} bytes")

# Access admin news
print("\n3. Accessing /admin/news...")
resp = session.get('http://127.0.0.1:5000/admin/news', allow_redirects=False)
print(f"   /admin/news: {resp.status_code} {resp.reason}")
print(f"   Content length: {len(resp.content)} bytes")

# Test news creation form
print("\n4. Accessing /admin/news/create...")
resp = session.get('http://127.0.0.1:5000/admin/news/create', allow_redirects=False)
print(f"   /admin/news/create: {resp.status_code} {resp.reason}")
if b'title' in resp.content:
    print("   ✓ Form found (contains title field)")
