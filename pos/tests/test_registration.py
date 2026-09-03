"""Self-service registration from the login page."""

from django.test import TestCase
from django.urls import reverse

from pos.models import ActivityLog, Role, StoreSetting, User

from .factories import PASSWORD, make_user, set_tax


def payload(**overrides):
    data = {
        'username': 'newhire',
        'first_name': 'New',
        'last_name': 'Hire',
        'email': 'new.hire@example.com',
        'phone': '+8801811000009',
        'password1': 'Str0ngPass!23',
        'password2': 'Str0ngPass!23',
    }
    data.update(overrides)
    return data


class RegistrationTests(TestCase):
    def setUp(self):
        set_tax('5.00', allow_self_registration=True, require_registration_approval=True)

    def test_login_page_offers_registration_when_enabled(self):
        response = self.client.get(reverse('pos:login'))
        self.assertContains(response, reverse('pos:register'))

    def test_login_page_hides_registration_when_disabled(self):
        set_tax('5.00', allow_self_registration=False)
        response = self.client.get(reverse('pos:login'))
        self.assertNotContains(response, reverse('pos:register'))

    def test_registration_creates_a_pending_cashier(self):
        self.client.post(reverse('pos:register'), payload())
        user = User.objects.get(username='newhire')
        self.assertEqual(user.role, Role.CASHIER)
        self.assertFalse(user.is_active)          # awaiting approval
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(ActivityLog.objects.filter(action='REGISTERED').exists())

    def test_a_pending_account_cannot_sign_in_yet(self):
        self.client.post(reverse('pos:register'), payload())
        response = self.client.post(
            reverse('pos:login'), {'username': 'newhire', 'password': 'Str0ngPass!23'}
        )
        self.assertFalse(response.context['user'].is_authenticated)

    def test_approval_by_a_manager_lets_the_account_in(self):
        self.client.post(reverse('pos:register'), payload())
        manager = make_user('mgr', Role.MANAGER)
        self.client.force_login(manager)
        self.client.post(
            reverse('pos:employee_toggle', args=[User.objects.get(username='newhire').pk])
        )
        self.client.logout()

        response = self.client.post(
            reverse('pos:login'),
            {'username': 'newhire', 'password': 'Str0ngPass!23'},
            follow=True,
        )
        self.assertTrue(response.context['user'].is_authenticated)

    def test_instant_access_when_approval_is_not_required(self):
        set_tax('5.00', allow_self_registration=True, require_registration_approval=False)
        response = self.client.post(reverse('pos:register'), payload(), follow=True)
        self.assertTrue(response.context['user'].is_authenticated)
        self.assertTrue(User.objects.get(username='newhire').is_active)

    def test_a_registrant_cannot_choose_a_privileged_role(self):
        """The role field is never read from the submitted data."""
        self.client.post(
            reverse('pos:register'),
            payload(role=Role.ADMIN, is_staff='on', is_superuser='on', is_active='on'),
        )
        user = User.objects.get(username='newhire')
        self.assertEqual(user.role, Role.CASHIER)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_active)

    def test_duplicate_username_is_rejected(self):
        make_user('newhire', Role.CASHIER)
        self.client.post(reverse('pos:register'), payload())
        self.assertEqual(User.objects.filter(username='newhire').count(), 1)

    def test_duplicate_email_is_rejected(self):
        make_user('other', Role.CASHIER, email='new.hire@example.com')
        response = self.client.post(reverse('pos:register'), payload())
        self.assertContains(response, 'already uses this email')
        self.assertFalse(User.objects.filter(username='newhire').exists())

    def test_mismatched_passwords_are_rejected(self):
        self.client.post(reverse('pos:register'), payload(password2='Different!23'))
        self.assertFalse(User.objects.filter(username='newhire').exists())

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            reverse('pos:register'), payload(password1='12345678', password2='12345678')
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newhire').exists())

    def test_password_is_stored_hashed(self):
        self.client.post(reverse('pos:register'), payload())
        user = User.objects.get(username='newhire')
        self.assertNotEqual(user.password, 'Str0ngPass!23')
        self.assertTrue(user.check_password('Str0ngPass!23'))

    def test_registration_is_blocked_when_switched_off(self):
        set_tax('5.00', allow_self_registration=False)
        response = self.client.post(reverse('pos:register'), payload(), follow=True)
        self.assertFalse(User.objects.filter(username='newhire').exists())
        self.assertContains(response, 'turned off')

    def test_signed_in_users_are_sent_to_the_dashboard(self):
        self.client.force_login(make_user('someone', Role.CASHIER))
        response = self.client.get(reverse('pos:register'))
        self.assertRedirects(response, reverse('pos:dashboard'))

    def test_pending_accounts_are_listed_for_approval(self):
        self.client.post(reverse('pos:register'), payload())
        self.client.force_login(make_user('boss', Role.ADMIN))
        response = self.client.get(reverse('pos:employee_list'), {'status': 'pending'})
        self.assertContains(response, 'newhire')
        self.assertContains(response, 'Awaiting approval')
        self.assertEqual(response.context['pending_count'], 1)
