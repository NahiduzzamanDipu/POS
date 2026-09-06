import os
import sys

# Path to your Django project (the folder containing manage.py)
sys.path.insert(0, os.path.dirname(__file__))

# Replace 'yourproject.settings' with your actual settings module,
# e.g. 'myapp.settings' or 'config.settings.production'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()