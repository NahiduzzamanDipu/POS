"""Self-configuration on hosting that only syncs files.

There is no shell on the target host, so `config.bootstrap.prepare` is the
whole deployment: it migrates and creates the administrator when the first
worker starts. The properties worth defending are that it never raises, and
that several workers starting together do not fight over the database.
"""

import os
from unittest import mock

from django.conf import settings
from django.test import TestCase

from config import bootstrap
from pos.models import Role, User

from .factories import PASSWORD, make_user


class NeedsWorkTests(TestCase):
    """The two cheap checks that decide whether anything has to happen."""

    def test_an_administrator_is_wanted_when_there_is_none(self):
        self.assertTrue(bootstrap._needs_an_administrator())

    def test_a_plain_cashier_does_not_count_as_one(self):
        make_user('till', Role.CASHIER)
        self.assertTrue(bootstrap._needs_an_administrator())

    def test_a_superuser_settles_it(self):
        admin = make_user('boss', Role.ADMIN)
        User.objects.filter(pk=admin.pk).update(is_superuser=True)
        self.assertFalse(bootstrap._needs_an_administrator())

    def test_a_disabled_superuser_does_not_count(self):
        """Otherwise a locked-out install can never recover by restarting."""
        admin = make_user('boss', Role.ADMIN)
        User.objects.filter(pk=admin.pk).update(is_superuser=True, is_active=False)
        self.assertTrue(bootstrap._needs_an_administrator())

    def test_a_migrated_database_has_nothing_pending(self):
        self.assertEqual(bootstrap._pending_migrations(), [])


class PrepareTests(TestCase):
    def tearDown(self):
        try:
            os.unlink(bootstrap._lock_path())
        except OSError:
            pass

    def test_it_creates_the_administrator(self):
        bootstrap.prepare()
        admin = User.objects.get(email='odelltech@gmail.com')
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.check_password('Pos@12345'))

    def test_running_it_again_changes_nothing(self):
        bootstrap.prepare()
        bootstrap.prepare()
        self.assertEqual(User.objects.filter(is_superuser=True).count(), 1)

    def test_it_leaves_no_lock_behind(self):
        bootstrap.prepare()
        self.assertFalse(os.path.exists(bootstrap._lock_path()))

    def test_it_does_nothing_when_the_work_is_already_done(self):
        """The common case: every worker start after the first."""
        bootstrap.prepare()
        with mock.patch.object(bootstrap, '_run') as run:
            bootstrap.prepare()
        run.assert_not_called()

    def test_a_failure_during_setup_does_not_reach_the_visitor(self):
        """A broken migration must not replace the site with a stack trace."""
        with mock.patch.object(bootstrap, '_run', side_effect=RuntimeError('boom')):
            bootstrap.prepare()          # must not raise
        self.assertFalse(os.path.exists(bootstrap._lock_path()))

    def test_it_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {'POS_SKIP_BOOTSTRAP': '1'}):
            with mock.patch.object(bootstrap, '_run') as run:
                bootstrap.prepare()
        run.assert_not_called()


class LockTests(TestCase):
    def setUp(self):
        self.path = bootstrap._lock_path()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    tearDown = setUp

    def test_the_first_caller_takes_the_lock(self):
        self.assertTrue(bootstrap._acquire(self.path))
        self.assertTrue(os.path.exists(self.path))

    def test_a_second_caller_is_refused(self):
        bootstrap._acquire(self.path)
        self.assertFalse(bootstrap._acquire(self.path))

    def test_a_second_worker_waits_rather_than_migrating_too(self):
        bootstrap._acquire(self.path)
        with mock.patch.object(bootstrap, '_run') as run:
            with mock.patch.object(bootstrap, 'LOCK_WAIT', 0):
                bootstrap.prepare()
        run.assert_not_called()

    def test_an_abandoned_lock_is_taken_over(self):
        """A crashed worker must not block every future start forever."""
        bootstrap._acquire(self.path)
        stale = os.path.getmtime(self.path) - bootstrap.LOCK_TIMEOUT - 10
        os.utime(self.path, (stale, stale))
        self.assertTrue(bootstrap._acquire(self.path))

    def test_releasing_a_missing_lock_is_harmless(self):
        bootstrap._release(self.path)        # must not raise


class SelfHostingSettingsTests(TestCase):
    """Defaults that decide whether an unconfigured host is safe."""

    def test_debug_is_off_unless_asked_for(self):
        """A host with no .env must not serve debug pages to visitors."""
        from config.settings import env_bool

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(env_bool('DJANGO_DEBUG', False))

    def test_the_signing_key_is_not_a_placeholder(self):
        self.assertGreaterEqual(len(settings.SECRET_KEY), 40)
        self.assertNotIn('django-insecure-', settings.SECRET_KEY)

    def test_static_files_are_served_without_collectstatic(self):
        """WHITENOISE_USE_FINDERS is what styles a host that never collects."""
        if not getattr(settings, 'WHITENOISE_AVAILABLE', False):
            self.skipTest('whitenoise is not installed')
        self.assertTrue(settings.WHITENOISE_USE_FINDERS)

    def test_the_generated_key_file_is_never_committed(self):
        import subprocess

        tracked = subprocess.run(
            ['git', 'ls-files'], cwd=str(settings.BASE_DIR),
            capture_output=True, text=True,
        ).stdout.splitlines()
        for name in ('.secret_key', '.bootstrap.lock', 'db.sqlite3'):
            self.assertNotIn(name, tracked)
