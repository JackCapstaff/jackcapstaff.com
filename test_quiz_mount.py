#!/usr/bin/env python
import os
import sys
from app import app

print("Testing quiz app mount...")
print()

print(f"WSGI app type: {type(app.wsgi_app).__name__}")

if hasattr(app.wsgi_app, 'mounts'):
    print(f"Mounted paths: {list(app.wsgi_app.mounts.keys())}")
    if '/quiz' in app.wsgi_app.mounts:
        print("✓ Quiz app IS mounted at /quiz")
    else:
        print("✗ Quiz app NOT found in mounts")
else:
    print("✗ DispatcherMiddleware not active")
    print(f"  WSGI is: {app.wsgi_app}")
