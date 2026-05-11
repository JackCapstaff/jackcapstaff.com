#!/usr/bin/env python
"""Use Flask test client to test routes."""

from app import app

with app.test_client() as client:
    print("Testing routes with Flask test client:")
    
    test_routes = [
        '/',
        '/login',
        '/admin/',
        '/admin/test',
        '/admin/news',
    ]
    
    for route in test_routes:
        resp = client.get(route)
        print(f"{route:30} -> {resp.status_code} {resp.status}")
        if resp.status_code == 404:
            print(f"  Response: {resp.get_data(as_text=True)[:100]}")
