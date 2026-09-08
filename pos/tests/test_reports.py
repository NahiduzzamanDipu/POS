"""Daily, Monthly, Yearly and Customer reports."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pos import reporting
from pos.models import Role
from pos.services import create_sale

from .factories import make_product, make_user, set_tax


class ReportDataTests(TestCase):
    """Aggregation correctness, independent of the templates."""

    def setUp(self):
        set_tax('0.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.product = make_product(sku='P1', price='100.00', cost='10.00', stock=500)
        self.today = timezone.localdate()

    def _sale(self, days_ago=0, quantity=1, number=''):
        sale = create_sale(
            cashier=self.cashier,
            items=[(self.product.pk, quantity)],
            customer_number=number,
            amount_paid=Decimal('100000'),
        )
        if days_ago:
            sale.created_at = timezone.now() - timedelta(days=days_ago)
            sale.save(update_fields=['created_at'])
        return sale

    def test_range_is_inclusive_at_both_ends(self):
        self._sale(days_ago=5)
        self._sale(days_ago=0)
        start = self.today - timedelta(days=5)
        self.assertEqual(reporting.sales_between(start, self.today).count(), 2)

    def test_sales_outside_the_range_are_excluded(self):
        self._sale(days_ago=10)
        self._sale(days_ago=0)
        start = self.today - timedelta(days=2)
        self.assertEqual(reporting.sales_between(start, self.today).count(), 1)

    def test_same_day_range_works(self):
        self._sale()
        self.assertEqual(reporting.sales_between(self.today, self.today).count(), 1)

    def test_summary_totals(self):
        self._sale(quantity=2)
        self._sale(quantity=3)
        totals = reporting.sales_summary(reporting.sales_between(self.today, self.today))
        self.assertEqual(totals['transactions'], 2)
        self.assertEqual(totals['items'], 5)
        self.assertEqual(totals['revenue'], Decimal('500.00'))

    def test_void_sales_are_excluded(self):
        from pos.services import void_sale

        sale = self._sale(quantity=2)
        self._sale(quantity=1)
        void_sale(sale, make_user('mgr', Role.MANAGER), 'cancelled')
        totals = reporting.sales_summary(reporting.sales_between(self.today, self.today))
        self.assertEqual(totals['transactions'], 1)
        self.assertEqual(totals['revenue'], Decimal('100.00'))

    def test_empty_range_returns_zeros_not_none(self):
        past = self.today - timedelta(days=400)
        totals = reporting.sales_summary(reporting.sales_between(past, past))
        self.assertEqual(totals['transactions'], 0)
        self.assertEqual(totals['revenue'], Decimal('0.00'))
        self.assertEqual(totals['net'], Decimal('0.00'))

    def test_customer_rows_group_by_number(self):
        self._sale(quantity=2, number='01712345678')
        self._sale(quantity=3, number='01712345678')
        self._sale(quantity=1, number='01812345678')
        rows = reporting.customer_rows(self.today, self.today)
        self.assertEqual(len(rows), 2)
        top = rows[0]
        self.assertEqual(top['customer_number'], '01712345678')
        self.assertEqual(top['orders'], 2)
        self.assertEqual(top['items'], 5)

    def test_walk_in_sales_are_not_in_the_customer_report(self):
        self._sale(quantity=1)                      # no number
        self._sale(quantity=1, number='01712345678')
        rows = reporting.customer_rows(self.today, self.today)
        self.assertEqual(len(rows), 1)

    def test_customer_rows_never_expose_personal_details(self):
        self._sale(number='01712345678')
        row = reporting.customer_rows(self.today, self.today)[0]
        self.assertNotIn('name', row)
        self.assertNotIn('email', row)
        self.assertNotIn('customer__name', row)


class ReportViewTests(TestCase):
    def setUp(self):
        set_tax('0.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.cashier = make_user('till', Role.CASHIER)
        self.product = make_product(sku='P1', price='100.00', cost='10.00', stock=500)
        self.client.force_login(self.manager)
        self.today = timezone.localdate()
        create_sale(cashier=self.cashier, items=[(self.product.pk, 2)],
                    customer_number='01712345678', amount_paid=Decimal('1000'))

    ALL_REPORTS = [
        'reports', 'report_sales', 'report_inventory', 'report_products',
        'report_suppliers', 'report_cashflow', 'report_collections',
        'report_profit', 'report_customers',
    ]

    def test_every_report_in_the_centre_renders(self):
        for name in self.ALL_REPORTS:
            with self.subTest(report=name):
                self.assertEqual(self.client.get(reverse(f'pos:{name}')).status_code, 200)

    def test_sales_report_for_a_chosen_day(self):
        response = self.client.get(
            reverse('pos:report_sales'),
            {'from': self.today.isoformat(), 'to': self.today.isoformat()},
        )
        self.assertEqual(response.context['totals']['transactions'], 1)
        self.assertEqual(response.context['totals']['revenue'], Decimal('200.00'))

    def test_sales_report_for_a_quiet_range(self):
        old = (self.today - timedelta(days=300)).isoformat()
        response = self.client.get(reverse('pos:report_sales'), {'from': old, 'to': old})
        self.assertEqual(response.context['totals']['transactions'], 0)

    def test_a_backwards_range_is_read_the_right_way_round(self):
        old = (self.today - timedelta(days=7)).isoformat()
        response = self.client.get(
            reverse('pos:report_sales'), {'from': self.today.isoformat(), 'to': old}
        )
        self.assertEqual(response.context['totals']['transactions'], 1)

    def test_the_legacy_daily_url_redirects_to_that_day(self):
        response = self.client.get(
            reverse('pos:report_daily'), {'date': self.today.isoformat()}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'from={self.today.isoformat()}', response['Location'])
        self.assertIn(f'to={self.today.isoformat()}', response['Location'])

    def test_the_legacy_monthly_url_redirects_to_that_month(self):
        response = self.client.get(
            reverse('pos:report_monthly'),
            {'month': self.today.month, 'year': self.today.year},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'from={self.today.replace(day=1).isoformat()}', response['Location'])

    def test_the_legacy_yearly_url_redirects_to_that_year(self):
        response = self.client.get(reverse('pos:report_yearly'), {'year': self.today.year})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'from={self.today.year}-01-01', response['Location'])
        self.assertIn(f'to={self.today.year}-12-31', response['Location'])

    def test_customer_report_range_is_inclusive(self):
        response = self.client.get(
            reverse('pos:report_customers'),
            {'from_date': self.today.isoformat(), 'to_date': self.today.isoformat()},
        )
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['customer_number'], '01712345678')

    def test_customer_report_rejects_a_reversed_range(self):
        response = self.client.get(
            reverse('pos:report_customers'),
            {
                'from_date': self.today.isoformat(),
                'to_date': (self.today - timedelta(days=5)).isoformat(),
            },
        )
        self.assertFalse(response.context['form'].is_valid())
        self.assertIsNone(response.context['rows'])

    def test_customer_report_can_filter_to_one_number(self):
        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number='01812345678', amount_paid=Decimal('1000'))
        response = self.client.get(
            reverse('pos:report_customers'),
            {
                'from_date': self.today.isoformat(),
                'to_date': self.today.isoformat(),
                'customer_number': '01812345678',
            },
        )
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['customer_number'], '01812345678')

    def test_customer_report_hides_names_and_emails(self):
        from pos.models import Customer

        Customer.objects.filter(phone='01712345678').update(
            name='Private Person', email='private@example.com'
        )
        response = self.client.get(
            reverse('pos:report_customers'),
            {'from_date': self.today.isoformat(), 'to_date': self.today.isoformat()},
        )
        self.assertContains(response, '01712345678')
        self.assertNotContains(response, 'Private Person')
        self.assertNotContains(response, 'private@example.com')

    def test_reports_export_csv(self):
        span = {'from': self.today.isoformat(), 'to': self.today.isoformat()}
        cases = [
            ('report_sales', span),
            ('report_inventory', {}),
            ('report_products', span),
            ('report_suppliers', span),
            ('report_cashflow', span),
            ('report_collections', span),
            ('report_profit', span),
            ('report_customers', {'from_date': self.today.isoformat(),
                                  'to_date': self.today.isoformat()}),
        ]
        for name, params in cases:
            with self.subTest(report=name):
                response = self.client.get(reverse(f'pos:{name}'), {**params, 'export': 'csv'})
                self.assertEqual(response['Content-Type'], 'text/csv')

    def test_a_cashier_cannot_open_reports(self):
        self.client.force_login(self.cashier)
        for name in self.ALL_REPORTS:
            with self.subTest(report=name):
                self.assertEqual(self.client.get(reverse(f'pos:{name}')).status_code, 403)


class CustomerDashboardTests(TestCase):
    def setUp(self):
        set_tax('0.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.product = make_product(sku='P1', price='100.00', cost='10.00', stock=100)
        self.client.force_login(self.manager)

    def test_a_customer_appears_automatically_after_a_sale(self):
        create_sale(cashier=self.manager, items=[(self.product.pk, 2)],
                    customer_number='01712345678', amount_paid=Decimal('1000'))
        response = self.client.get(reverse('pos:customer_list'))
        self.assertContains(response, '01712345678')
        self.assertContains(response, '200.00')

    def test_dashboard_hides_personal_details(self):
        from pos.models import Customer

        create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                    customer_number='01712345678', amount_paid=Decimal('1000'))
        Customer.objects.filter(phone='01712345678').update(
            name='Private Person', email='private@example.com'
        )
        response = self.client.get(reverse('pos:customer_list'))
        self.assertNotContains(response, 'Private Person')
        self.assertNotContains(response, 'private@example.com')

    def test_total_shopping_sums_completed_sales(self):
        """Sales 2 and 3 earn the 2% loyalty discount: 100 + 98 + 98."""
        for _ in range(3):
            create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                        customer_number='01712345678', amount_paid=Decimal('1000'))
        response = self.client.get(reverse('pos:customer_list'))
        customer = response.context['page_obj'][0]
        self.assertEqual(customer.purchase_count, 3)
        self.assertEqual(customer.purchase_total, Decimal('296.00'))
