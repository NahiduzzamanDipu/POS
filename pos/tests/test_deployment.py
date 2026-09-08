"""Everything a fresh deployment depends on.

The deploy runs `manage.py ensure_admin`, so a host that has just been set up
is reachable without anyone opening a shell. These pin that behaviour down,
including the part that matters most on a second deploy: not clobbering a
password an administrator has since changed.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from pos.models import Role, User

from .factories import PASSWORD, make_user

ADMIN_EMAIL = 'odelltech@gmail.com'
ADMIN_PASSWORD = 'Pos@12345'


def run(*args, **kwargs):
    out = StringIO()
    call_command('ensure_admin', *args, stdout=out, **kwargs)
    return out.getvalue()


class EnsureAdminTests(TestCase):
    def test_it_creates_the_administrator_on_an_empty_database(self):
        self.assertFalse(User.objects.exists())
        run()

        admin = User.objects.get(email=ADMIN_EMAIL)
        self.assertTrue(admin.check_password(ADMIN_PASSWORD))
        self.assertEqual(admin.role, Role.ADMIN)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_active)
        self.assertTrue(admin.employee_id.startswith('EMP-'))

    def test_the_new_administrator_can_actually_sign_in(self):
        run()
        response = self.client.post(
            reverse('pos:login'),
            {'username': ADMIN_EMAIL, 'password': ADMIN_PASSWORD},
            follow=True,
        )
        self.assertTrue(response.context['user'].is_authenticated)
        self.assertEqual(response.context['user'].email, ADMIN_EMAIL)

    def test_running_it_twice_makes_no_second_account(self):
        run()
        run()
        self.assertEqual(User.objects.filter(email=ADMIN_EMAIL).count(), 1)

    def test_it_adopts_an_existing_admin_account_rather_than_duplicating(self):
        """An older install has `admin` under a different email."""
        legacy = User.objects.create_user(
            username='admin', password=PASSWORD, email='admin@smartpos.example'
        )
        run()

        self.assertEqual(User.objects.count(), 1)
        legacy.refresh_from_db()
        self.assertEqual(legacy.email, ADMIN_EMAIL)
        self.assertEqual(legacy.role, Role.ADMIN)
        self.assertTrue(legacy.is_superuser)

    def test_it_does_not_reset_a_password_someone_changed(self):
        run()
        admin = User.objects.get(email=ADMIN_EMAIL)
        admin.set_password('Something-Else!99')
        admin.save()

        run()                                   # a later deploy

        admin.refresh_from_db()
        self.assertTrue(admin.check_password('Something-Else!99'))
        self.assertFalse(admin.check_password(ADMIN_PASSWORD))

    def test_reset_password_is_available_when_it_is_wanted(self):
        run()
        admin = User.objects.get(email=ADMIN_EMAIL)
        admin.set_password('Forgotten!99')
        admin.save()

        run('--reset-password')

        admin.refresh_from_db()
        self.assertTrue(admin.check_password(ADMIN_PASSWORD))

    def test_it_reactivates_a_disabled_administrator(self):
        """Otherwise a locked-out account cannot be recovered by deploying."""
        run()
        User.objects.filter(email=ADMIN_EMAIL).update(is_active=False, role=Role.CASHIER)

        run()

        admin = User.objects.get(email=ADMIN_EMAIL)
        self.assertTrue(admin.is_active)
        self.assertEqual(admin.role, Role.ADMIN)

    def test_the_credentials_can_be_overridden(self):
        run('--email', 'boss@example.com', '--password', 'Another-One!42')
        admin = User.objects.get(email='boss@example.com')
        self.assertTrue(admin.check_password('Another-One!42'))

    def test_it_leaves_other_staff_alone(self):
        cashier = make_user('till', Role.CASHIER)
        run()
        cashier.refresh_from_db()
        self.assertEqual(cashier.role, Role.CASHIER)
        self.assertFalse(cashier.is_superuser)


class DeploymentSettingsTests(TestCase):
    def test_the_production_domain_is_allowed_by_default(self):
        """A host with no .env yet must still answer, not raise DisallowedHost."""
        from django.conf import settings

        self.assertIn('pos.odelltech.com', settings.DEFAULT_ALLOWED_HOSTS)

    def test_static_files_have_a_home_to_be_collected_into(self):
        from django.conf import settings

        self.assertTrue(str(settings.STATIC_ROOT).endswith('staticfiles'))

    def test_the_passenger_entry_point_exposes_a_wsgi_application(self):
        from pathlib import Path

        from django.conf import settings

        entry = Path(settings.BASE_DIR) / 'passenger_wsgi.py'
        self.assertTrue(entry.exists(), 'passenger_wsgi.py is missing')
        self.assertIn('application', entry.read_text(encoding='utf-8'))

    def test_requirements_stay_installable_on_a_host_without_a_compiler(self):
        """mysqlclient needs build tools cPanel accounts rarely have."""
        from pathlib import Path

        from django.conf import settings

        def packages(path):
            """Requirement lines only -- the comments discuss mysqlclient."""
            return [
                line.strip()
                for line in path.read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.strip().startswith('#')
            ]

        core = packages(Path(settings.BASE_DIR) / 'requirements.txt')
        self.assertFalse([p for p in core if p.startswith('mysqlclient')])
        self.assertTrue([p for p in core if p.startswith('Django==')])

        extra_path = Path(settings.BASE_DIR) / 'requirements-mysql.txt'
        self.assertTrue(extra_path.exists())
        extra = packages(extra_path)
        self.assertTrue([p for p in extra if p.startswith('mysqlclient')])
        self.assertIn('-r requirements.txt', extra)
