"""Public self-registration must not exist.

The original version of this file exercised a sign-up flow reachable from the
login page. That flow was removed deliberately: accounts are created by an
administrator, and a POS terminal must never let a visitor mint themselves an
account.

These tests therefore assert the *absence* of that feature. They are kept as
tests rather than deleted so the removal cannot be quietly undone -- if anyone
reintroduces a register route, a sign-up link, or a role selector on the login
form, this file fails.
"""

from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from pos.models import Role, User

from .factories import PASSWORD, make_user, set_tax


class NoPublicRegistrationTests(TestCase):
    def setUp(self):
        set_tax('0.00')

    def test_there_is_no_register_route(self):
        with self.assertRaises(NoReverseMatch):
            reverse('pos:register')

    def test_common_signup_paths_are_not_served(self):
        """Nothing answers at the usual sign-up addresses."""
        for path in ['/register/', '/signup/', '/accounts/register/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(
                    response.status_code, 404,
                    f'{path} should not exist, got {response.status_code}',
                )

    def test_login_page_offers_no_way_to_sign_up(self):
        response = self.client.get(reverse('pos:login'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8', 'ignore').lower()
        for phrase in ['sign up', 'create account', 'create an account']:
            self.assertNotIn(phrase, body, f'login page must not offer "{phrase}"')

    def test_login_page_does_not_ask_for_a_role(self):
        """The role is read from the account, never chosen by the person signing in."""
        response = self.client.get(reverse('pos:login'))
        body = response.content.decode('utf-8', 'ignore')
        self.assertNotIn('name="role"', body)

    def test_login_asks_for_email_or_phone_not_username(self):
        response = self.client.get(reverse('pos:login'))
        self.assertContains(response, 'Email or Phone')

    def test_role_comes_from_the_database_on_sign_in(self):
        """Whatever the browser posts, the stored role is the one that applies."""
        cashier = make_user('till01', Role.CASHIER, email='till01@example.com')
        response = self.client.post(
            reverse('pos:login'),
            {'username': 'till01@example.com', 'password': PASSWORD, 'role': Role.ADMIN},
        )
        self.assertEqual(response.status_code, 302)
        cashier.refresh_from_db()
        self.assertEqual(cashier.role, Role.CASHIER)
        self.assertFalse(cashier.is_superuser)

    def test_an_anonymous_visitor_cannot_create_a_user(self):
        before = User.objects.count()
        self.client.post('/register/', {'username': 'intruder',
                                        'password1': 'x', 'password2': 'x'})
        self.assertEqual(User.objects.count(), before)
