#!/usr/bin/env python
"""Test script to check registered routes."""

from app import app

print("Registered routes:")
for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
    print(f"  {rule}")

print("\nAdmin routes specifically:")
admin_routes = [str(rule) for rule in app.url_map.iter_rules() if 'admin' in str(rule).lower()]
if admin_routes:
    for route in admin_routes:
        print(f"  {route}")
else:
    print("  (No admin routes found!)")
