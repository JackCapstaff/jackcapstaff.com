#!/usr/bin/env python
import requests

urls = [
    'http://127.0.0.1:5000/admin/',
    'http://127.0.0.1:5000/admin',
    'http://127.0.0.1:5000/test',
    'http://127.0.0.1:5000/admin/test',
]

for url in urls:
    r = requests.get(url, allow_redirects=False)
    print(f"{url:50} -> {r.status_code}")
    if r.status_code in (301, 302, 303, 307, 308):
        print(f"  Redirect to: {r.headers.get('Location')}")
