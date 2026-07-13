#!/usr/bin/env python
import sys
sys.path.insert(0, '.')
import os
os.environ['FLASK_CONFIG'] = 'development'
from app import app

print(f'app.wsgi_app type: {type(app.wsgi_app).__name__}')
print(f'Is DispatcherMiddleware: {"DispatcherMiddleware" in str(type(app.wsgi_app))}')

# Try to test a request
with app.test_client() as client:
    resp = client.get('/quiz')
    print(f'GET /quiz -> {resp.status_code}')
    
    resp = client.get('/quiz/')
    print(f'GET /quiz/ -> {resp.status_code}')
