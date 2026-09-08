"""The Employee table shows a real name, and still never a username.

`display_name` falls back to the username when a person has no name recorded,
which is exactly the leak this column could reintroduce. The table reads
`full_name` instead, so a missing name reads as missing.
"""

from django.test import TestCase
from django.urls import reverse

from pos.models import Role, User

from .factories import PASSWORD, make_user, set_tax


class FullNamePropertyTests(TestCase):
    def test_it_joins_the_existing_name_fields(self):
        user = User.objects.create_user(
            username='u1', password=PASSWORD, first_name='Ayesha', last_name='Rahman'
        )
        self.assertEqual(user.full_name, 'Ayesha Rahman')

    def test_it_is_blank_rather_than_falling_back_to_the_username(self):
        user = User.objects.create_user(username='hidden.handle', password=PASSWORD)
        self.assertEqual(user.full_name, '')
        # The old property still behaves as it always did for other callers.
        self.assertEqual(user.display_name, 'hidden.handle')

    def test_a_first_name_alone_is_enough(self):
        user = User.objects.create_user(
            username='u2', password=PASSWORD, first_name='Rafi'
        )
        self.assertEqual(user.full_name, 'Rafi')


class EmployeeListNameTests(TestCase):
    def setUp(self):
        set_tax('0.00')
        self.admin = make_user('boss', Role.ADMIN)
        self.client.force_login(self.admin)
        self.staff = User.objects.create_user(
            username='secret.handle',
            password=PASSWORD,
            first_name='Ayesha',
            last_name='Rahman',
            email='ayesha@odelltech.example',
            phone='01711223344',
            position='Senior Cashier',
            role=Role.CASHIER,
        )

    def test_the_table_has_an_employee_name_column(self):
        response = self.client.get(reverse('pos:employee_list'))
        self.assertContains(response, 'Employee Name')

    def test_the_name_is_shown(self):
        response = self.client.get(reverse('pos:employee_list'))
        self.assertContains(response, 'Ayesha Rahman')

    def test_the_employee_id_is_still_shown(self):
        response = self.client.get(reverse('pos:employee_list'))
        self.assertContains(response, self.staff.employee_id)

    def test_the_other_columns_survive(self):
        response = self.client.get(reverse('pos:employee_list'))
        for value in ('Senior Cashier', '01711223344',
                      'ayesha@odelltech.example', 'Cashier'):
            self.assertContains(response, value)

    def test_the_username_is_still_never_shown(self):
        response = self.client.get(reverse('pos:employee_list'))
        self.assertNotContains(response, 'secret.handle')

    def test_a_nameless_account_says_so_instead_of_leaking_the_username(self):
        User.objects.create_user(username='no.name.here', password=PASSWORD,
                                 email='nn@odelltech.example', role=Role.CASHIER)
        response = self.client.get(reverse('pos:employee_list'))
        self.assertContains(response, 'Name not set')
        self.assertNotContains(response, 'no.name.here')

    def test_the_name_is_searchable(self):
        # Asserted on the result set: the signed-in user's own ID also appears
        # in the sidebar and user menu, so page text alone proves nothing.
        response = self.client.get(reverse('pos:employee_list'), {'q': 'Ayesha'})
        self.assertContains(response, 'Ayesha Rahman')
        self.assertEqual(
            [u.pk for u in response.context['page_obj']], [self.staff.pk]
        )


class EmployeeNameEditingTests(TestCase):
    """Create and edit must still manage the name through the existing fields."""

    def setUp(self):
        set_tax('0.00')
        self.client.force_login(make_user('boss', Role.ADMIN))

    def test_the_form_offers_first_and_last_name(self):
        response = self.client.get(reverse('pos:employee_create'))
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="last_name"')
        self.assertNotContains(response, 'name="username"')

    def test_creating_an_employee_stores_the_name(self):
        self.client.post(reverse('pos:employee_create'), {
            'first_name': 'Nusrat', 'last_name': 'Jahan',
            'email': 'nusrat@odelltech.example', 'phone': '01799887766',
            'position': 'Cashier', 'role': Role.CASHIER, 'is_active': 'on',
            'password1': 'Str0ngPass!23', 'password2': 'Str0ngPass!23',
        })
        user = User.objects.get(email='nusrat@odelltech.example')
        self.assertEqual(user.full_name, 'Nusrat Jahan')

    def test_editing_an_employee_changes_the_name(self):
        staff = User.objects.create_user(
            username='edit.me', password=PASSWORD, first_name='Old',
            last_name='Name', email='edit@odelltech.example', role=Role.CASHIER,
        )
        self.client.post(reverse('pos:employee_update', args=[staff.pk]), {
            'first_name': 'New', 'last_name': 'Name',
            'email': 'edit@odelltech.example', 'phone': '',
            'position': '', 'role': Role.CASHIER, 'is_active': 'on',
            'password1': '', 'password2': '',
        })
        staff.refresh_from_db()
        self.assertEqual(staff.full_name, 'New Name')
        # The username is an internal detail and must be left alone.
        self.assertEqual(staff.username, 'edit.me')
