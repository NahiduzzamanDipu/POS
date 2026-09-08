"""Entry point for Phusion Passenger (cPanel "Setup Python App").

Passenger imports this file and looks for a module-level `application`. The
project's own WSGI callable is in config/wsgi.py; this only makes sure the
project directory is importable first, because Passenger does not always run
with the application root on sys.path.

Nothing here is specific to one host, so it is safe to keep in the repository.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from config.wsgi import application            # noqa: E402,F401
