"""Automatic employee IDs and the change-password flow."""

from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from pos.models import ActivityLog, Role, User

from .factories import PASSWORD, make_user, set_tax


class EmployeeIdTests(TestCase):
    def test_an_id_is_assigned_on_creation(self):
        user = User.objects.create_user(username='newbie', password=PASSWORD)
        self.assertTrue(user.employee_id.startswith('EMP-'))

    def test_ids_increment(self):
        first = User.objects.create_user(username='a', password=PASSWORD)
        second = User.objects.create_user(username='b', password=PASSWORD)
        self.assertNotEqual(first.employee_id, second.employee_id)
        self.assertEqual(
            int(second.employee_id[4:]), int(first.employee_id[4:]) + 1
        )

    def test_ids_are_zero_padded(self):
        user = User.objects.create_user(username='padded', password=PASSWORD)
        self.assertRegex(user.employee_id, r'^EMP-\d{3,}$')

    def test_an_existing_id_is_never_changed(self):
        user = User.objects.create_user(username='keeper', password=PASSWORD)
        original = user.employee_id
        user.first_name = 'Renamed'
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.employee_id, original)

    def test_a_supplied_id_is_honoured_for_data_migration(self):
        """Imports and fixtures may carry their own ID; generation only fills blanks."""
        user = User(username='legacy', employee_id='EMP-900')
        user.set_password(PASSWORD)
        user.save()
        self.assertEqual(user.employee_id, 'EMP-900')

    def test_generation_continues_past_the_highest_existing_id(self):
        User.objects.create_user(username='high', password=PASSWORD)
        User.objects.filter(username='high').update(employee_id='EMP-500')
        nxt = User.objects.create_user(username='after', password=PASSWORD)
        self.assertEqual(nxt.employee_id, 'EMP-501')

    def test_ids_are_unique_across_many_creations(self):
        ids = {
            User.objects.create_user(username=f'u{i}', password=PASSWORD).employee_id
            for i in range(25)
        }
        self.assertEqual(len(ids), 25)


class AdminCreatedEmployeeIdTests(TestCase):
    """Section 20: the employee never types an ID, and never sees a username.

    Accounts are created by an administrator under Employees -- there is no
    public sign-up -- so that is the flow these exercise.
    """

    def setUp(self):
        set_tax('5.00')
        self.client.force_login(make_user('boss', Role.ADMIN))

    def _payload(self, **extra):
        data = {
            'first_name': 'New',
            'last_name': 'Hire',
            'email': 'new.hire@example.com',
            'phone': '01811000009',
            'position': 'Cashier',
            'role': Role.CASHIER,
            'is_active': 'on',
            'password1': 'Str0ngPass!23',
            'password2': 'Str0ngPass!23',
        }
        data.update(extra)
        return data

    def test_the_employee_form_has_no_employee_id_field(self):
        response = self.client.get(reverse('pos:employee_create'))
        self.assertNotContains(response, 'name="employee_id"')

    def test_the_employee_form_has_no_username_field(self):
        """Staff sign in with an email or phone; the username is internal."""
        response = self.client.get(reverse('pos:employee_create'))
        self.assertNotContains(response, 'name="username"')

    def test_creating_an_employee_generates_an_id(self):
        self.client.post(reverse('pos:employee_create'), self._payload())
        user = User.objects.get(email='new.hire@example.com')
        self.assertTrue(user.employee_id.startswith('EMP-'))

    def test_a_username_is_derived_from_the_email(self):
        self.client.post(reverse('pos:employee_create'), self._payload())
        user = User.objects.get(email='new.hire@example.com')
        self.assertTrue(user.username)
        self.assertNotIn('@', user.username)

    def test_a_submitted_employee_id_is_ignored(self):
        self.client.post(
            reverse('pos:employee_create'), self._payload(employee_id='EMP-999')
        )
        user = User.objects.get(email='new.hire@example.com')
        self.assertNotEqual(user.employee_id, 'EMP-999')

    def test_a_submitted_username_is_ignored(self):
        self.client.post(
            reverse('pos:employee_create'), self._payload(username='chosen.handle')
        )
        user = User.objects.get(email='new.hire@example.com')
        self.assertNotEqual(user.username, 'chosen.handle')

    def test_two_employees_get_different_ids(self):
        self.client.post(reverse('pos:employee_create'), self._payload())
        self.client.post(reverse('pos:employee_create'), self._payload(
            email='second@example.com', phone='01811000010'))
        ids = list(
            User.objects.filter(email__in=['new.hire@example.com', 'second@example.com'])
            .values_list('employee_id', flat=True)
        )
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_a_duplicate_email_is_refused(self):
        """The email is the sign-in identifier, so it has to be unique."""
        self.client.post(reverse('pos:employee_create'), self._payload())
        response = self.client.post(reverse('pos:employee_create'), self._payload(
            phone='01811000011'))
        self.assertContains(response, 'already uses this email')
        self.assertEqual(User.objects.filter(email='new.hire@example.com').count(), 1)

    def test_a_duplicate_phone_is_refused(self):
        """A shared number would make phone sign-in ambiguous."""
        self.client.post(reverse('pos:employee_create'), self._payload())
        response = self.client.post(reverse('pos:employee_create'), self._payload(
            email='third@example.com'))
        self.assertContains(response, 'already uses this phone')
        self.assertFalse(User.objects.filter(email='third@example.com').exists())


class ConcurrentEmployeeIdTests(TransactionTestCase):
    """Ids must stay unique when registrations overlap."""

    reset_sequences = True

    def test_parallel_creation_yields_unique_ids(self):
        import threading

        errors = []

        def create(index):
            try:
                User.objects.create_user(username=f'race{index}', password=PASSWORD)
            except Exception as exc:      # pragma: no cover - surfaced by the assert
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], msg=f'errors during concurrent creation: {errors}')
        ids = list(
            User.objects.filter(username__startswith='race')
            .values_list('employee_id', flat=True)
        )
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8, msg=f'duplicate ids: {ids}')


class ChangePasswordTests(TestCase):
    def setUp(self):
        self.user = make_user('till', Role.CASHIER)
        self.client.force_login(self.user)
        self.url = reverse('pos:change_password')

    def test_the_page_renders_for_a_signed_in_user(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_anonymous_users_are_redirected(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pos:login'), response['Location'])

    def test_a_valid_change_succeeds(self):
        response = self.client.post(self.url, {
            'old_password': PASSWORD,
            'new_password1': 'Br@ndNewPass99',
            'new_password2': 'Br@ndNewPass99',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Br@ndNewPass99'))
        self.assertTrue(ActivityLog.objects.filter(action='PASSWORD_CHANGED').exists())

    def test_the_user_stays_signed_in_afterwards(self):
        self.client.post(self.url, {
            'old_password': PASSWORD,
            'new_password1': 'Br@ndNewPass99',
            'new_password2': 'Br@ndNewPass99',
        })
        self.assertEqual(self.client.get(reverse('pos:dashboard')).status_code, 200)

    def test_a_wrong_current_password_is_rejected(self):
        self.client.post(self.url, {
            'old_password': 'not-my-password',
            'new_password1': 'Br@ndNewPass99',
            'new_password2': 'Br@ndNewPass99',
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_mismatched_confirmation_is_rejected(self):
        self.client.post(self.url, {
            'old_password': PASSWORD,
            'new_password1': 'Br@ndNewPass99',
            'new_password2': 'Different99!',
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_a_weak_password_is_rejected(self):
        self.client.post(self.url, {
            'old_password': PASSWORD,
            'new_password1': '12345678',
            'new_password2': '12345678',
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_the_password_is_stored_hashed(self):
        self.client.post(self.url, {
            'old_password': PASSWORD,
            'new_password1': 'Br@ndNewPass99',
            'new_password2': 'Br@ndNewPass99',
        })
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.password, 'Br@ndNewPass99')
        self.assertTrue(self.user.password.startswith('pbkdf2_'))

    def test_the_header_links_to_the_page(self):
        response = self.client.get(reverse('pos:dashboard'))
        self.assertContains(response, reverse('pos:change_password'))


class ChangePasswordEveryRoleTests(TestCase):
    """Every role must be able to change their own password and find the link."""

    ROLES = [Role.ADMIN, Role.MANAGER, Role.CASHIER, Role.INVENTORY]

    def test_every_role_can_open_the_page(self):
        for role in self.ROLES:
            with self.subTest(role=role):
                user = make_user(f'user_{role.lower()}', role)
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(reverse('pos:change_password')).status_code, 200
                )

    def test_every_role_can_reach_change_password_from_any_page(self):
        """The entry point moved from the sidebar into the account menu.

        What matters is that it is reachable from every page for every role,
        which is what the URL assertion checks; the label is matched
        case-insensitively so a wording tweak does not fail the test.
        """
        url = reverse('pos:change_password')
        for role in self.ROLES:
            with self.subTest(role=role):
                user = make_user(f'nav_{role.lower()}', role)
                self.client.force_login(user)
                response = self.client.get(reverse('pos:dashboard'))
                self.assertContains(response, url)
                self.assertIn(
                    'change password',
                    response.content.decode('utf-8', 'ignore').lower(),
                )

    def test_every_role_can_actually_change_their_password(self):
        for role in self.ROLES:
            with self.subTest(role=role):
                user = make_user(f'pw_{role.lower()}', role)
                self.client.force_login(user)
                new_password = f'Chg{role.title()}!2026'
                self.client.post(reverse('pos:change_password'), {
                    'old_password': PASSWORD,
                    'new_password1': new_password,
                    'new_password2': new_password,
                })
                user.refresh_from_db()
                self.assertTrue(
                    user.check_password(new_password),
                    msg=f'{role} could not change their password',
                )

    def test_a_user_cannot_change_another_users_password(self):
        """The form only ever acts on request.user."""
        victim = make_user('victim', Role.ADMIN)
        attacker = make_user('attacker', Role.CASHIER)
        self.client.force_login(attacker)
        self.client.post(reverse('pos:change_password'), {
            'old_password': PASSWORD,
            'new_password1': 'Hijack3d!pass',
            'new_password2': 'Hijack3d!pass',
            'user': victim.pk,          # ignored
            'username': victim.username,
        })
        victim.refresh_from_db()
        attacker.refresh_from_db()
        self.assertTrue(victim.check_password(PASSWORD))
        self.assertTrue(attacker.check_password('Hijack3d!pass'))
