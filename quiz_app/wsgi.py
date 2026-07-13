"""WSGI entry point for production deployments."""
import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402

config_name = os.environ.get("FLASK_CONFIG", "production")
app = create_app(config_name)

if __name__ == "__main__":
    app.run()
