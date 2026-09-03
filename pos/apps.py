from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _tune_sqlite(sender, connection, **kwargs):
    """Put every new SQLite connection into WAL mode.

    WAL keeps reads working while a write is in flight, and synchronous=NORMAL
    is the matching durability setting. Django 4.0's SQLite backend accepts no
    'init_command' OPTION, so the PRAGMAs are issued here instead.
    """
    if connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL;')
        cursor.execute('PRAGMA synchronous=NORMAL;')


class PosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pos'
    verbose_name = 'Point of Sale'

    def ready(self):
        connection_created.connect(_tune_sqlite, dispatch_uid='pos.sqlite_pragmas')
