"""Authentication and role-based access (BR-001, BR-002, BRL-11)."""

from django.test import TestCase
from django.urls import reverse

from pos.models import ActivityLog, Role
from pos.permissions import PRODUCT_PRICE, SETTINGS_MANAGE, capabilities_for, user_can

from .factories import PASSWORD, make_product, make_user


class LoginTests(TestCase):
    def setUp(self):
        self.user = make_user('cashier1', Role.CASHIER)

    def test_valid_credentials_sign_the_user_in(self):
        response = self.client.post(
            reverse('pos:login'),
            {'username': 'cashier1', 'password': PASSWORD, 'role': Role.CASHIER},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['user'], self.user)
        self.assertTrue(ActivityLog.objects.filter(action='LOGIN', user=self.user).exists())

    def test_invalid_password_is_rejected_and_logged(self):
        response = self.client.post(
            reverse('pos:login'), {'username': 'cashier1', 'password': 'wrong'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['user'].is_authenticated)
        self.assertTrue(ActivityLog.objects.filter(action='LOGIN_FAILED').exists())

    def test_role_selector_cannot_escalate_privileges(self):
        """The mock-up's role dropdown is cosmetic; the stored role wins."""
        response = self.client.post(
            reverse('pos:login'),
            {'username': 'cashier1', 'password': PASSWORD, 'role': Role.ADMIN},
        )
        self.assertFalse(response.context['user'].is_authenticated)
        self.assertContains(response, 'registered as Cashier')

    def test_inactive_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(
            reverse('pos:login'), {'username': 'cashier1', 'password': PASSWORD}
        )
        self.assertFalse(response.context['user'].is_authenticated)

    def test_logout_clears_the_session(self):
        self.client.force_login(self.user)
        self.client.post(reverse('pos:logout'))
        response = self.client.get(reverse('pos:dashboard'))
        self.assertEqual(response.status_code, 302)


class AccessControlTests(TestCase):
    def setUp(self):
        self.admin = make_user('boss', Role.ADMIN)
        self.manager = make_user('mgr', Role.MANAGER)
        self.cashier = make_user('till', Role.CASHIER)
        self.stock = make_user('store', Role.INVENTORY)
        self.product = make_product()

    def test_anonymous_users_are_redirected_to_login(self):
        response = self.client.get(reverse('pos:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pos:login'), response['Location'])

    def test_cashier_cannot_open_employee_management(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse('pos:employee_list')).status_code, 403)

    def test_cashier_cannot_open_settings(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse('pos:settings')).status_code, 403)

    def test_inventory_staff_cannot_use_the_till(self):
        self.client.force_login(self.stock)
        self.assertEqual(self.client.get(reverse('pos:pos_terminal')).status_code, 403)

    def test_inventory_staff_can_adjust_stock(self):
        self.client.force_login(self.stock)
        self.assertEqual(self.client.get(reverse('pos:stock_adjust')).status_code, 200)

    def test_manager_reaches_reports_but_not_settings(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('pos:reports')).status_code, 200)
        self.assertEqual(self.client.get(reverse('pos:settings')).status_code, 403)

    def test_admin_reaches_everything(self):
        self.client.force_login(self.admin)
        for name in ['dashboard', 'settings', 'employee_list', 'reports', 'pos_terminal']:
            self.assertEqual(
                self.client.get(reverse(f'pos:{name}')).status_code, 200, msg=name
            )

    def test_only_privileged_roles_may_change_prices(self):
        """BRL-4: price edits are restricted."""
        self.assertTrue(user_can(self.manager, PRODUCT_PRICE))
        self.assertTrue(user_can(self.admin, PRODUCT_PRICE))
        self.assertFalse(user_can(self.stock, PRODUCT_PRICE))
        self.assertFalse(user_can(self.cashier, PRODUCT_PRICE))

    def test_inventory_staff_price_edit_is_ignored(self):
        self.client.force_login(self.stock)
        self.client.post(
            reverse('pos:product_update', args=[self.product.pk]),
            {
                'name': self.product.name,
                'sku': self.product.sku,
                'barcode': '',
                'category': self.product.category_id,
                'supplier': '',
                'brand': '',
                'cost_price': '1.00',
                'selling_price': '2.00',
                'min_stock_level': 5,
                'unit': 'pc',
                'is_active': 'on',
            },
        )
        self.product.refresh_from_db()
        self.assertEqual(str(self.product.selling_price), '650.00')

    def test_navigation_only_lists_permitted_pages(self):
        self.client.force_login(self.cashier)
        labels = [item['label'] for item in self.client.get(reverse('pos:dashboard')).context['nav_items']]
        self.assertIn('New Sale', labels)
        self.assertNotIn('Employees', labels)
        self.assertNotIn('Settings', labels)

    def test_superuser_holds_every_capability(self):
        self.assertIn(SETTINGS_MANAGE, capabilities_for(self.admin))
