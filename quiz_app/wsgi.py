"""WSGI entry point for production deployments."""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Ensure quiz_app can be imported
current_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Import create_app
try:
    # Try direct import first (when run from quiz_app directory)
    from app import create_app
except ImportError:
    try:
        # Try package import (when run from parent directory)
        from quiz_app.app import create_app
    except ImportError as e:
        print(f"Failed to import create_app: {e}")
        raise

config_name = os.environ.get("FLASK_CONFIG", "production")
app = create_app(config_name)

if __name__ == "__main__":
    app.run()
