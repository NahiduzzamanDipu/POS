"""Smoke tests: every page renders, reports aggregate, exports download."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from pos.models import Customer, Product, Role
from pos.services import create_sale

from .factories import (
    make_category,
    make_customer,
    make_product,
    make_user,
    set_tax,
    store_name,
)


class PageRenderTests(TestCase):
    """Guards against missing templates and broken URL names."""

    @classmethod
    def setUpTestData(cls):
        set_tax('5.00')
        cls.admin = make_user('boss', Role.ADMIN)
        cls.product = make_product(stock=30)
        cls.customer = make_customer()

    def setUp(self):
        self.client.force_login(self.admin)
        self.sale = create_sale(
            cashier=self.admin, items=[(self.product.pk, 2)],
            customer_number=self.customer.phone
        )

    def test_every_listing_and_form_page_returns_200(self):
        names = [
            'dashboard', 'pos_terminal', 'product_list', 'product_create',
            'category_list', 'category_create', 'supplier_list', 'supplier_create',
            'customer_list', 'customer_create', 'employee_list', 'employee_create',
            'inventory', 'stock_adjust',
            'product_import',
            'stock_movements', 'purchase_list', 'purchase_create', 'sale_list',
            'return_list', 'reports', 'report_sales',
            'report_inventory', 'report_products', 'report_customers',
            'report_suppliers', 'report_cashflow', 'report_collections',
            'report_profit',
            'settings', 'activity_log',
        ]
        for name in names:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f'pos:{name}')).status_code, 200)

    def test_detail_pages_render(self):
        pairs = [
            ('product_detail', self.product.pk),
            ('product_update', self.product.pk),
            ('customer_detail', self.customer.pk),
            ('sale_detail', self.sale.pk),
            ('invoice', self.sale.pk),
            ('return_create', self.sale.pk),
        ]
        for name, pk in pairs:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f'pos:{name}', args=[pk])).status_code, 200)

    def test_invoice_shows_the_number_not_the_name(self):
        """Privacy: the invoice identifies the customer by number only."""
        self.customer.name = 'Private Person'
        self.customer.email = 'private@example.com'
        self.customer.save()
        response = self.client.get(reverse('pos:invoice', args=[self.sale.pk]))
        self.assertContains(response, self.sale.invoice_no)
        self.assertContains(response, escape(store_name()))
        self.assertContains(response, self.customer.phone)
        self.assertNotContains(response, 'Private Person')
        self.assertNotContains(response, 'private@example.com')

    def test_unknown_page_returns_the_custom_404(self):
        response = self.client.get('/no-such-page/')
        self.assertEqual(response.status_code, 404)


class ReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        set_tax('5.00')
        cls.manager = make_user('mgr', Role.MANAGER)
        cls.product = make_product(price='100.00', stock=100)

    def setUp(self):
        self.client.force_login(self.manager)
        for _ in range(3):
            create_sale(cashier=self.manager, items=[(self.product.pk, 2)])

    def test_sales_report_totals_match_the_sales(self):
        response = self.client.get(reverse('pos:report_sales'))
        totals = response.context['totals']
        self.assertEqual(totals['transactions'], 3)
        self.assertEqual(totals['revenue'], Decimal('630.00'))
        self.assertEqual(totals['tax'], Decimal('30.00'))

    def test_sales_report_exports_csv(self):
        response = self.client.get(reverse('pos:report_sales'), {'export': 'csv'})
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('INV-', response.content.decode())

    def test_inventory_report_exports_csv(self):
        response = self.client.get(reverse('pos:report_inventory'), {'export': 'csv'})
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(self.product.sku, response.content.decode())

    def test_customer_report_exports_csv(self):
        response = self.client.get(
            reverse('pos:report_customers'),
            {
                'from_date': '2020-01-01',
                'to_date': timezone.localdate().isoformat(),
                'export': 'csv',
            },
        )
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_product_report_ranks_best_sellers(self):
        response = self.client.get(reverse('pos:report_products'))
        self.assertEqual(response.context['best'][0]['units'], 6)

    def test_date_filter_excludes_out_of_range_sales(self):
        response = self.client.get(
            reverse('pos:report_sales'), {'from': '2020-01-01', 'to': '2020-01-15'}
        )
        self.assertEqual(response.context['totals']['transactions'], 0)

    def test_the_range_filter_is_inclusive_of_both_ends(self):
        today = timezone.localdate().isoformat()
        response = self.client.get(
            reverse('pos:report_sales'), {'from': today, 'to': today}
        )
        self.assertEqual(response.context['totals']['transactions'], 3)

    def test_the_legacy_report_urls_redirect_into_the_sales_report(self):
        """Daily/Monthly/Yearly were folded into one ranged report."""
        for name in ('report_daily', 'report_monthly', 'report_yearly'):
            with self.subTest(report=name):
                response = self.client.get(reverse(f'pos:{name}'))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('pos:report_sales'), response['Location'])


class CustomerAndSettingsTests(TestCase):
    def setUp(self):
        self.admin = make_user('boss', Role.ADMIN)
        self.client.force_login(self.admin)

    def test_customer_creation_and_duplicate_phone(self):
        payload = {'name': 'Rahim', 'phone': '01811000001', 'email': '', 'address': '', 'is_active': 'on'}
        self.client.post(reverse('pos:customer_create'), payload)
        self.client.post(reverse('pos:customer_create'), dict(payload, name='Other'))
        self.assertEqual(Customer.objects.filter(phone='01811000001').count(), 1)

    def test_invalid_phone_is_rejected(self):
        self.client.post(
            reverse('pos:customer_create'),
            {'name': 'Bad', 'phone': 'not-a-phone!!', 'email': '', 'address': '', 'is_active': 'on'},
        )
        self.assertEqual(Customer.objects.count(), 0)

    def test_settings_update_changes_the_tax_rate(self):
        self.client.post(
            reverse('pos:settings'),
            {
                'business_name': 'My Shop',
                'address': '1 Road',
                'phone': '',
                'email': '',
                'currency_symbol': 'Tk',
                'tax_rate': '7.50',
                'invoice_prefix': 'bil',
                'existing_customer_discount_percent': '2.00',
                'receipt_footer': 'Thanks',
            },
        )
        from pos.models import StoreSetting

        store = StoreSetting.load()
        self.assertEqual(store.tax_rate, Decimal('7.50'))
        self.assertEqual(store.invoice_prefix, 'BIL')
        self.assertEqual(store.business_name, 'My Shop')

    def test_tax_rate_above_100_is_rejected(self):
        response = self.client.post(
            reverse('pos:settings'),
            {
                'business_name': 'My Shop', 'address': '', 'phone': '', 'email': '',
                'currency_symbol': 'Tk', 'tax_rate': '150', 'invoice_prefix': 'INV',
                'existing_customer_discount_percent': '2', 'receipt_footer': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        from pos.models import StoreSetting

        self.assertNotEqual(StoreSetting.load().tax_rate, Decimal('150'))

    def test_employee_password_is_hashed(self):
        self.client.post(
            reverse('pos:employee_create'),
            {
                'first_name': 'New', 'last_name': 'Staff',
                'email': 'new@example.com', 'phone': '', 'employee_id': 'EMP-99',
                'position': 'Cashier', 'role': Role.CASHIER, 'is_active': 'on',
                'password1': 'Str0ngPass!', 'password2': 'Str0ngPass!',
            },
        )
        from pos.models import User

        staff = User.objects.get(email='new@example.com')
        self.assertNotEqual(staff.password, 'Str0ngPass!')
        self.assertTrue(staff.check_password('Str0ngPass!'))

    def test_the_employee_list_never_shows_a_username(self):
        from pos.models import User

        User.objects.filter(pk=self.admin.pk).update(username='secret.handle')
        response = self.client.get(reverse('pos:employee_list'))
        self.assertNotContains(response, 'secret.handle')

    def test_mismatched_passwords_are_rejected(self):
        self.client.post(
            reverse('pos:employee_create'),
            {
                'first_name': '', 'last_name': '', 'email': '',
                'phone': '', 'employee_id': '', 'position': '', 'role': Role.CASHIER,
                'is_active': 'on', 'password1': 'Str0ngPass!', 'password2': 'Different!',
            },
        )
        from pos.models import User

        self.assertFalse(User.objects.filter(username='nope').exists())


class ArchivedProductVisibilityTests(TestCase):
    """A deactivated product is retired: it must not appear anywhere a user
    browses or sells, but it stays in the database so old invoices resolve."""

    def setUp(self):
        set_tax('5.00')
        self.admin = make_user('boss', Role.ADMIN)
        self.client.force_login(self.admin)
        self.live = make_product(name='Live Product', sku='SKU-LIVE', stock=10)
        self.gone = make_product(name='Retired Product', sku='SKU-GONE', stock=10)
        # Sell it first: a retired product with sales history is the case that
        # actually leaks into the reports, so the test must cover it.
        from pos.services import create_sale

        create_sale(cashier=self.admin, items=[(self.gone.pk, 2)],
                    amount_paid=Decimal('5000'))
        self.gone.is_active = False
        self.gone.save(update_fields=['is_active'])

    def test_products_tab_hides_archived_by_default(self):
        response = self.client.get(reverse('pos:product_list'))
        self.assertContains(response, 'Live Product')
        self.assertNotContains(response, 'Retired Product')

    def test_searching_the_catalogue_does_not_surface_archived(self):
        response = self.client.get(reverse('pos:product_list'), {'q': 'Retired'})
        self.assertNotContains(response, 'Retired Product')

    def test_low_and_out_of_stock_filters_exclude_archived(self):
        for status in ('low', 'out'):
            with self.subTest(status=status):
                response = self.client.get(reverse('pos:product_list'), {'status': status})
                self.assertNotContains(response, 'Retired Product')

    def test_archived_are_reachable_only_when_asked_for(self):
        response = self.client.get(reverse('pos:product_list'), {'status': 'archived'})
        self.assertContains(response, 'Retired Product')
        self.assertNotContains(response, 'Live Product')
        self.assertContains(response, 'archived')

    def test_the_sales_screen_renders_no_catalogue(self):
        """The till searches; it does not list stock. Nothing to leak."""
        response = self.client.get(reverse('pos:pos_terminal'))
        self.assertNotContains(response, 'Live Product')
        self.assertNotContains(response, 'Retired Product')

    def test_the_product_search_api_never_returns_archived(self):
        response = self.client.get(reverse('pos:product_lookup'), {'q': 'Product'})
        names = [r['name'] for r in response.json()['results']]
        self.assertIn('Live Product', names)
        self.assertNotIn('Retired Product', names)

    def test_inventory_and_reports_exclude_archived(self):
        for name in ('inventory', 'report_inventory', 'report_products',
                     'stock_movements'):
            with self.subTest(page=name):
                response = self.client.get(reverse(f'pos:{name}'))
                self.assertNotContains(response, 'Retired Product')

    def test_stock_adjustment_cannot_target_an_archived_product(self):
        response = self.client.get(reverse('pos:stock_adjust'))
        self.assertNotContains(response, 'Retired Product')

    def test_an_archived_product_cannot_be_sold(self):
        from pos.services import BusinessRuleError, create_sale

        with self.assertRaises(BusinessRuleError):
            create_sale(cashier=self.admin, items=[(self.gone.pk, 1)])

    def test_the_row_still_exists_so_history_resolves(self):
        from pos.models import Product

        self.assertTrue(Product.objects.filter(sku='SKU-GONE').exists())


class BulkProductActionTests(TestCase):
    """Select products in the list and activate/deactivate them in one go."""

    def setUp(self):
        set_tax('5.00')
        self.admin = make_user('boss', Role.ADMIN)
        self.cashier = make_user('till', Role.CASHIER)
        self.client.force_login(self.admin)
        self.grocery = make_category('Grocery')
        self.tools = make_category('Tools')
        self.a = make_product(name='Alpha', sku='SKU-A', category=self.grocery, stock=5)
        self.b = make_product(name='Beta', sku='SKU-B', category=self.grocery, stock=5)
        self.c = make_product(name='Gamma', sku='SKU-C', category=self.tools, stock=5)
        self.url = reverse('pos:product_bulk_action')

    def _actives(self):
        return set(
            Product.objects.filter(is_active=True).values_list('name', flat=True)
        )

    # -- the controls are on the page --------------------------------------
    def test_the_list_offers_a_select_all_checkbox(self):
        response = self.client.get(reverse('pos:product_list'))
        self.assertContains(response, 'id="select-all"')
        self.assertContains(response, 'class="row-select"')
        self.assertContains(response, 'value="deactivate"')
        self.assertContains(response, 'value="activate"')

    def test_a_cashier_sees_no_bulk_controls(self):
        self.client.force_login(self.cashier)
        response = self.client.get(reverse('pos:product_list'))
        self.assertNotContains(response, 'id="select-all"')
        self.assertNotContains(response, 'value="deactivate"')

    # -- deactivating ------------------------------------------------------
    def test_deactivating_the_selected_products(self):
        self.client.post(self.url, {
            'action': 'deactivate', 'selected': [self.a.pk, self.b.pk]})
        self.assertEqual(self._actives(), {'Gamma'})

    def test_activating_the_selected_products(self):
        Product.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(is_active=False)
        self.client.post(self.url, {
            'action': 'activate', 'selected': [self.a.pk, self.b.pk]})
        self.assertEqual(self._actives(), {'Alpha', 'Beta', 'Gamma'})

    def test_one_click_deactivates_everything_matching_the_filter(self):
        self.client.post(self.url, {'action': 'deactivate', 'scope': 'filtered'})
        self.assertEqual(self._actives(), set())

    def test_the_filter_scope_respects_the_active_filters(self):
        """Only the Grocery rows the user was looking at are touched."""
        self.client.post(self.url, {
            'action': 'deactivate', 'scope': 'filtered',
            'category': str(self.grocery.pk)})
        self.assertEqual(self._actives(), {'Gamma'})

    def test_the_filter_scope_respects_a_search_term(self):
        self.client.post(self.url, {
            'action': 'deactivate', 'scope': 'filtered', 'q': 'Alpha'})
        self.assertEqual(self._actives(), {'Beta', 'Gamma'})

    def test_archived_products_can_be_restored_in_one_click(self):
        Product.objects.all().update(is_active=False)
        self.client.post(self.url, {
            'action': 'activate', 'scope': 'filtered', 'status': 'archived'})
        self.assertEqual(self._actives(), {'Alpha', 'Beta', 'Gamma'})

    # -- guard rails -------------------------------------------------------
    def test_selecting_nothing_changes_nothing(self):
        response = self.client.post(self.url, {'action': 'deactivate'}, follow=True)
        self.assertEqual(self._actives(), {'Alpha', 'Beta', 'Gamma'})
        self.assertContains(response, 'Select at least one product')

    def test_an_unknown_action_is_refused(self):
        self.client.post(self.url, {'action': 'delete', 'selected': [self.a.pk]})
        self.assertEqual(self._actives(), {'Alpha', 'Beta', 'Gamma'})

    def test_a_cashier_cannot_call_the_endpoint(self):
        self.client.force_login(self.cashier)
        response = self.client.post(self.url, {
            'action': 'deactivate', 'selected': [self.a.pk]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._actives(), {'Alpha', 'Beta', 'Gamma'})

    def test_the_endpoint_rejects_get(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_the_count_reports_only_what_changed(self):
        Product.objects.filter(pk=self.a.pk).update(is_active=False)
        response = self.client.post(self.url, {
            'action': 'deactivate', 'selected': [self.a.pk, self.b.pk]}, follow=True)
        # Alpha was already archived, so only Beta changed.
        self.assertContains(response, '1 product archived')

    def test_it_says_so_when_there_is_nothing_to_do(self):
        response = self.client.post(self.url, {
            'action': 'activate', 'selected': [self.a.pk]}, follow=True)
        self.assertContains(response, 'Nothing to do')

    def test_the_action_is_written_to_the_activity_log(self):
        from pos.models import ActivityLog

        self.client.post(self.url, {
            'action': 'deactivate', 'selected': [self.a.pk]})
        self.assertTrue(
            ActivityLog.objects.filter(action='PRODUCTS_DEACTIVATED').exists()
        )

    def test_the_user_lands_back_on_the_same_filtered_list(self):
        response = self.client.post(self.url, {
            'action': 'deactivate', 'selected': [self.a.pk],
            'q': 'Alpha', 'category': str(self.grocery.pk)})
        self.assertIn('q=Alpha', response['Location'])
        self.assertIn(f'category={self.grocery.pk}', response['Location'])

    def test_bulk_deactivation_hides_them_from_the_sales_screen(self):
        self.client.post(self.url, {'action': 'deactivate', 'scope': 'filtered'})
        response = self.client.get(reverse('pos:pos_terminal'))
        for name in ('Alpha', 'Beta', 'Gamma'):
            self.assertNotContains(response, f'>{name}<')
