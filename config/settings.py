"""Django settings for the O'dell Tech Shopping point-of-sale system."""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {'1', 'true', 'yes', 'on'}


SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-0hii+htqbqcvfqn-9ejw*p3g+deg_&go93)9^z$&z%#)zp^xds',
)

DEBUG = env_bool('DJANGO_DEBUG', True)

ALLOWED_HOSTS = [h.strip() for h in os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'pos',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'pos.context_processors.store_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# ---------------------------------------------------------------------------
# DB_ENGINE picks the backend:
#   sqlite  -- zero setup, no server needed. Good for a fresh clone, a laptop
#              demo, or anyone you send the project to.
#   mysql   -- the production/shared setup, needs MySQL 8.0.11+ running.
#
# Default is sqlite so `python manage.py runserver` works on a machine with
# nothing installed. This project's own .env sets DB_ENGINE=mysql explicitly.
DB_ENGINE = os.getenv('DB_ENGINE', 'sqlite').strip().lower()

if DB_ENGINE in {'sqlite', 'sqlite3'}:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / os.getenv('SQLITE_NAME', 'db.sqlite3'),
            'OPTIONS': {
                # WAL keeps reads working while a write is in flight, and the
                # busy timeout stops "database is locked" under light concurrency.
                'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
                'transaction_mode': 'IMMEDIATE',
                'timeout': 20,
            },
        }
    }
elif DB_ENGINE in {'mysql', 'mariadb'}:
    DB_NAME = os.getenv('DB_NAME', 'pos_system')

    # Guard rail: the MySQL system schema must never hold application tables.
    if DB_NAME.strip().lower() in {'mysql', 'information_schema', 'performance_schema', 'sys'}:
        raise ImproperlyConfigured(
            f"DB_NAME is set to the MySQL system schema '{DB_NAME}'. "
            "Point DB_NAME at the application database (pos_system) instead."
        )

    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': DB_NAME,
            'USER': os.getenv('DB_USER', 'root'),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', '127.0.0.1'),
            'PORT': os.getenv('DB_PORT', '3306'),
            # MySQL can only resolve named time zones (needed by every ``__date``
            # lookup) once its mysql.time_zone tables are loaded. Matching the
            # connection time zone to TIME_ZONE removes the CONVERT_TZ call
            # entirely, so date filtering works on a stock server. See README.
            'TIME_ZONE': os.getenv('DJANGO_TIME_ZONE', 'Asia/Dhaka'),
            'OPTIONS': {
                'charset': 'utf8mb4',
                # STRICT_TRANS_TABLES makes MySQL reject truncating/invalid writes
                # instead of silently coercing them.
                'sql_mode': 'STRICT_TRANS_TABLES',
            },
            'TEST': {
                'CHARSET': 'utf8mb4',
                'COLLATION': 'utf8mb4_unicode_ci',
            },
        }
    }
else:
    raise ImproperlyConfigured(
        f"DB_ENGINE must be 'sqlite' or 'mysql', not '{DB_ENGINE}'."
    )


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'pos.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'pos:login'
LOGIN_REDIRECT_URL = 'pos:dashboard'
LOGOUT_REDIRECT_URL = 'pos:login'


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.getenv('DJANGO_TIME_ZONE', 'Asia/Dhaka')
USE_I18N = True
USE_TZ = True


# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'


# Sessions -- cashiers share terminals, so sessions must not outlive a shift.
SESSION_COOKIE_AGE = int(os.getenv('SESSION_COOKIE_AGE', 60 * 60 * 8))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # read by the POS screen's fetch() calls
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', True)
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {
        'pos': {
            'handlers': ['console'],
            # Keep the test runner's output readable.
            'level': os.getenv('POS_LOG_LEVEL', 'WARNING' if 'test' in sys.argv else 'INFO'),
        },
    },
}
