"""The hardening around the one page an attacker can always reach.

The login form is public by definition, so it is the place a POS system gets
attacked. These cover the rate limit, and the settings that stop a stolen
repository from being enough to forge an administrator session.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pos.models import ActivityLog, Role, User
from pos.views.auth import LOCKOUT_WINDOW, MAX_FAILED_ATTEMPTS

from .factories import PASSWORD, make_user, set_tax


class LoginRateLimitTests(TestCase):
    def setUp(self):
        set_tax('0.00')
        self.user = make_user('till', Role.CASHIER)
        self.user.email = 'till@odelltech.example'
        self.user.save(update_fields=['email'])
        self.url = reverse('pos:login')

    def _fail(self, times=1):
        for _ in range(times):
            self.client.post(self.url, {'username': 'till@odelltech.example',
                                        'password': 'wrong'})

    def test_a_wrong_password_is_recorded(self):
        self._fail()
        self.assertEqual(ActivityLog.objects.filter(action='LOGIN_FAILED').count(), 1)

    def test_a_few_wrong_guesses_do_not_lock_anyone_out(self):
        """A cashier fumbling their password must still be able to sign in."""
        self._fail(3)
        response = self.client.post(
            self.url, {'username': 'till@odelltech.example', 'password': PASSWORD},
            follow=True,
        )
        self.assertTrue(response.context['user'].is_authenticated)

    def test_it_warns_before_it_locks(self):
        self._fail(MAX_FAILED_ATTEMPTS - 2)
        response = self.client.post(
            self.url, {'username': 'till@odelltech.example', 'password': 'wrong'},
            follow=True,
        )
        self.assertContains(response, 'before this address is locked out')

    def test_too_many_guesses_locks_the_address_out(self):
        self._fail(MAX_FAILED_ATTEMPTS)
        response = self.client.post(
            self.url, {'username': 'till@odelltech.example', 'password': 'wrong'},
            follow=True,
        )
        self.assertContains(response, 'Too many failed sign-in attempts')

    def test_the_lockout_refuses_even_the_correct_password(self):
        """Otherwise the limit tells an attacker when they have guessed right."""
        self._fail(MAX_FAILED_ATTEMPTS)
        response = self.client.post(
            self.url, {'username': 'till@odelltech.example', 'password': PASSWORD},
            follow=True,
        )
        self.assertFalse(response.context['user'].is_authenticated)
        self.assertContains(response, 'Too many failed sign-in attempts')

    def test_the_lockout_expires(self):
        self._fail(MAX_FAILED_ATTEMPTS)
        # Age every failure past the window.
        ActivityLog.objects.filter(action='LOGIN_FAILED').update(
            created_at=timezone.now() - LOCKOUT_WINDOW - timedelta(minutes=1)
        )
        response = self.client.post(
            self.url, {'username': 'till@odelltech.example', 'password': PASSWORD},
            follow=True,
        )
        self.assertTrue(response.context['user'].is_authenticated)

    def test_the_lock_follows_the_address_not_the_account(self):
        """Guessing many accounts from one address is the attack, not one account."""
        make_user('other', Role.CASHIER, email='other@odelltech.example')
        for index in range(MAX_FAILED_ATTEMPTS):
            self.client.post(self.url, {'username': f'guess{index}@example.com',
                                        'password': 'wrong'})
        response = self.client.post(
            self.url, {'username': 'other@odelltech.example', 'password': PASSWORD},
            follow=True,
        )
        self.assertFalse(response.context['user'].is_authenticated)

    def test_a_good_password_still_works_on_a_clean_address(self):
        response = self.client.post(
            self.url, {'username': 'till@odelltech.example', 'password': PASSWORD},
            follow=True,
        )
        self.assertTrue(response.context['user'].is_authenticated)


class DefaultPasswordWarningTests(TestCase):
    def test_an_admin_on_the_published_password_is_told_to_change_it(self):
        from django.core.management import call_command
        from io import StringIO

        call_command('ensure_admin', stdout=StringIO())
        response = self.client.post(
            reverse('pos:login'),
            {'username': 'odelltech@gmail.com', 'password': 'Pos@12345'},
            follow=True,
        )
        self.assertContains(response, 'still uses the default password')

    def test_no_nagging_once_it_has_been_changed(self):
        admin = make_user('boss', Role.ADMIN, email='boss@odelltech.example')
        admin.is_superuser = True
        admin.save()
        response = self.client.post(
            reverse('pos:login'),
            {'username': 'boss@odelltech.example', 'password': PASSWORD},
            follow=True,
        )
        self.assertNotContains(response, 'still uses the default password')


class SecretKeyTests(TestCase):
    def test_no_signing_key_is_committed_to_the_source(self):
        """A key in the repository is not a secret; it forges any session."""
        from pathlib import Path

        from django.conf import settings

        source = (Path(settings.BASE_DIR) / 'config' / 'settings.py').read_text(
            encoding='utf-8'
        )
        self.assertNotIn('django-insecure-', source)

    def test_the_generated_development_key_is_not_tracked(self):
        import subprocess

        from django.conf import settings

        tracked = subprocess.run(
            ['git', 'ls-files'], cwd=str(settings.BASE_DIR),
            capture_output=True, text=True,
        ).stdout.splitlines()
        self.assertNotIn('.secret_key', tracked)

    def test_the_database_is_not_shipped_in_the_repository(self):
        """It carries password hashes and customer phone numbers."""
        import subprocess

        from django.conf import settings

        tracked = subprocess.run(
            ['git', 'ls-files'], cwd=str(settings.BASE_DIR),
            capture_output=True, text=True,
        ).stdout.splitlines()
        self.assertNotIn('db.sqlite3', tracked)


class SessionSecurityTests(TestCase):
    def test_the_session_cookie_is_not_readable_from_javascript(self):
        from django.conf import settings

        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_sessions_do_not_outlive_a_shift(self):
        from django.conf import settings

        self.assertLessEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 12)
        self.assertTrue(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)

    def test_clickjacking_and_sniffing_defences_are_on(self):
        from django.conf import settings

        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)

    def test_signing_out_clears_the_session(self):
        user = make_user('till', Role.CASHIER)
        self.client.force_login(user)
        self.client.post(reverse('pos:logout'))
        self.assertEqual(self.client.get(reverse('pos:dashboard')).status_code, 302)
