"""WSGI entry point for production deployments."""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Import create_app from the quiz app's package
# Use relative import from the package
if __name__ == '__main__':
    # When run directly, use absolute import
    from quiz_app.app import create_app
else:
    # When imported as a module, quiz_app is in parent directory
    try:
        from quiz_app.app import create_app
    except ImportError:
        from app import create_app

config_name = os.environ.get("FLASK_CONFIG", "production")
app = create_app(config_name)

if __name__ == "__main__":
    app.run()
