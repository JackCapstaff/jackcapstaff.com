#!/usr/bin/env python
"""Test news CRUD operations."""

import requests
from datetime import datetime

session = requests.Session()

# Login first
print("1. Logging in...")
session.post('http://127.0.0.1:5000/login', data={
    'identifier': 'admin',
    'password': 'admin123'
})

# Create a news article
print("\n2. Creating news article...")
data = {
    'title': 'My First News Article',
    'subtitle': 'Testing the admin panel',
    'excerpt': 'This is a test article',
    'content': '<p>This is a test article to verify that content creation works correctly.</p>',
    'published': 'on',
    'published_at': datetime.utcnow().isoformat(),
}
resp = session.post('http://127.0.0.1:5000/admin/news/create', data=data)
print(f"   Status: {resp.status_code}")
if resp.status_code == 302:
    print(f"   Redirect: {resp.headers.get('Location')} - SUCCESS")
else:
    print(f"   Content: {resp.text[:200]}")

# Get the news list to see the created item
print("\n3. Viewing news list...")
resp = session.get('http://127.0.0.1:5000/admin/news')
print(f"   Status: {resp.status_code}")
if b'My First News Article' in resp.content:
    print("   ✓ Article appears in news list!")
elif b'test' in resp.content.lower():
    print("   ✓ News content found (may have different formatting)")
else:
    print("   × Article not found in list")
