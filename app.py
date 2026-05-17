"""Root WSGI entry point for Render deployment.

Render's default start command is 'gunicorn app:app'.
This module re-exports the Flask app from web.app.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from web.app import app
