#!/usr/bin/env python
import sys
sys.path.insert(0, '.')
import os
os.environ['FLASK_CONFIG'] = 'development'
from app import app

# Check route registration
for rule in app.url_map.iter_rules():
    if 'quiz' in rule.rule:
        print(f'Route: {rule.rule} -> {rule.endpoint}')

# Try to test a request BEFORE middleware
print("\nBefore testing middleware:")
with app.test_client() as client:
    resp = client.get('/quiz', follow_redirects=False)
    print(f'GET /quiz -> {resp.status_code} (Location: {resp.headers.get("Location", "N/A")})')
    
    resp = client.get('/quiz/', follow_redirects=False)
    print(f'GET /quiz/ -> {resp.status_code}')
