"""Bring a freshly deployed copy of the application into a working state.

This exists for hosting where the repository is synced to the server and
nothing else runs -- no shell, no build step, no `.cpanel.yml`. Everything the
deploy would normally do has to happen the first time a request arrives.

Called from `passenger_wsgi.py` before the WSGI application is created, so it
runs once per worker process rather than once per request.

Two rules shape the code below:

  * **It must never take the site down.** Every step is wrapped: if migrating
    fails, the site still serves with whatever schema exists, and the reason is
    written to the log rather than to the visitor.
  * **It must be safe to run concurrently.** A host starts several workers at
    once, and SQLite takes a database-wide write lock. A lock file means only
    the first worker migrates while the others wait for it and then carry on.
"""

import logging
import os
import time

logger = logging.getLogger('pos')

LOCK_NAME = '.bootstrap.lock'
LOCK_TIMEOUT = 120          # seconds before a lock is treated as abandoned
LOCK_WAIT = 30              # seconds a second worker waits for the first


def _lock_path():
    from django.conf import settings

    return os.path.join(str(settings.BASE_DIR), LOCK_NAME)


def _acquire(path):
    """Take the lock, or return False if another worker holds a fresh one.

    A crashed worker would otherwise leave a lock behind forever, so a lock
    older than LOCK_TIMEOUT is taken over.
    """
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(path) > LOCK_TIMEOUT:
                logger.warning('bootstrap: taking over an abandoned lock')
                os.unlink(path)
                return _acquire(path)
        except OSError:
            pass
        return False
    except OSError as exc:
        # A read-only deployment cannot lock, and cannot migrate either.
        logger.warning('bootstrap: cannot create a lock file (%s)', exc)
        return False


def _release(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _pending_migrations():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return executor.migration_plan(targets)


def _needs_an_administrator():
    from pos.models import User

    return not User.objects.filter(is_superuser=True, is_active=True).exists()


def _run():
    """The actual work, once the lock is held."""
    from django.core.management import call_command

    if _pending_migrations():
        logger.info('bootstrap: applying migrations')
        call_command('migrate', interactive=False, verbosity=0)
        logger.info('bootstrap: migrations applied')

    if _needs_an_administrator():
        logger.info('bootstrap: creating the administrator account')
        call_command('ensure_admin', verbosity=0)
        logger.info('bootstrap: administrator ready')


def prepare():
    """Make the database usable and make sure someone can sign in.

    Idempotent and cheap once the work is done: two indexed queries that find
    nothing to do. Safe to call on every worker start.
    """
    if os.getenv('POS_SKIP_BOOTSTRAP', '').strip().lower() in {'1', 'true', 'yes'}:
        return

    try:
        import django

        django.setup()
    except Exception:                                    # noqa: BLE001
        logger.exception('bootstrap: Django would not start')
        return

    try:
        if not _pending_migrations() and not _needs_an_administrator():
            return                                       # already prepared
    except Exception:                                    # noqa: BLE001
        # An empty or unreachable database lands here on the very first boot;
        # fall through and let the work below try to create it.
        pass

    path = _lock_path()
    if not _acquire(path):
        # Another worker is setting up. Wait for it rather than racing, then
        # serve whatever state it produced.
        deadline = time.time() + LOCK_WAIT
        while os.path.exists(path) and time.time() < deadline:
            time.sleep(0.5)
        return

    try:
        _run()
    except Exception:                                    # noqa: BLE001
        # Never propagate: a half-migrated database still serves most pages,
        # and a stack trace here would replace the whole site with an error.
        logger.exception('bootstrap: setup did not complete')
    finally:
        _release(path)
