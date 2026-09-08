"""Report navigation, page identity and the grouped chart."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pos import charts
from pos.models import Role
from pos.services import create_sale

from .factories import make_product, make_user, set_tax

# The report centre as it now stands. Daily / Monthly / Yearly were retired:
# one Sales Report with an inclusive From/To range answers all three, so those
# routes now redirect and are deliberately absent from this list.
REPORTS = [
    ('report_sales', '/reports/sales/', 'Sales Report'),
    ('report_inventory', '/reports/inventory/', 'Inventory Report'),
    ('report_products', '/reports/products/', 'Product Performance'),
    ('report_suppliers', '/reports/suppliers/', 'Supplier Report'),
    ('report_cashflow', '/reports/cash-flow/', 'Cash Flow'),
    ('report_collections', '/reports/collections/', 'User Wise Collection'),
    ('report_profit', '/reports/profit/', 'Profits'),
    ('report_customers', '/reports/customer/', 'Customer Report'),
]


class ReportNavigationTests(TestCase):
    """Each report must open its own page, not another report's content."""

    def setUp(self):
        set_tax('0.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.product = make_product(sku='P1', price='100.00', cost='10.00', stock=200)
        self.client.force_login(self.manager)
        create_sale(cashier=self.manager, items=[(self.product.pk, 3)],
                    customer_number='01712345678', amount_paid=Decimal('1000'))

    def test_every_report_url_resolves_to_its_own_path(self):
        for name, path, _title in REPORTS:
            with self.subTest(report=name):
                self.assertEqual(reverse(f'pos:{name}'), path)

    def test_every_report_shows_its_own_title(self):
        for name, _path, title in REPORTS:
            with self.subTest(report=name):
                response = self.client.get(reverse(f'pos:{name}'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'<h1 class="topbar__title">{title}</h1>')

    def test_inventory_report_shows_inventory_content(self):
        """Regression: it used to render only the report shortcut links."""
        response = self.client.get(reverse('pos:report_inventory'))
        self.assertContains(response, 'Stock value (cost)')
        self.assertContains(response, 'Current stock')
        self.assertNotContains(response, 'Available reports')

    def test_product_performance_shows_its_own_content(self):
        response = self.client.get(reverse('pos:report_products'))
        self.assertContains(response, 'Product performance')
        self.assertContains(response, 'Units Sold')
        self.assertNotContains(response, 'Available reports')

    def test_no_report_page_is_just_a_list_of_links(self):
        """The old landing page had nothing but shortcut buttons as content."""
        for name, _path, _title in REPORTS + [('reports', '/reports/', 'Reports')]:
            with self.subTest(report=name):
                response = self.client.get(reverse(f'pos:{name}'))
                self.assertNotContains(response, 'Available reports')

    def test_every_report_carries_the_same_tab_strip(self):
        for name, _path, _title in REPORTS:
            with self.subTest(report=name):
                response = self.client.get(reverse(f'pos:{name}'))
                self.assertContains(response, 'report-tabs')
                for _n, path, label in REPORTS:
                    self.assertContains(response, f'href="{path}"')
                    self.assertContains(response, f'>{label}</a>')

    def test_the_sidebar_lists_every_report_on_a_report_page(self):
        response = self.client.get(reverse('pos:report_inventory'))
        self.assertContains(response, 'nav-link--sub')
        for _name, path, _label in REPORTS:
            self.assertContains(response, f'href="{path}"')

    def test_the_open_report_is_marked_active_in_the_tabs(self):
        response = self.client.get(reverse('pos:report_products'))
        self.assertContains(
            response, 'href="/reports/products/" class="report-tab is-active"', html=False
        )

    def test_a_cashier_cannot_reach_any_report(self):
        self.client.force_login(make_user('till', Role.CASHIER))
        for name, _path, _title in REPORTS:
            with self.subTest(report=name):
                self.assertEqual(self.client.get(reverse(f'pos:{name}')).status_code, 403)


class ChartTests(TestCase):
    def setUp(self):
        set_tax('0.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.client.force_login(self.manager)
        self.a = make_product(name='Alpha', sku='P1', price='100.00', cost='10.00', stock=200)
        self.b = make_product(name='Beta', sku='P2', price='50.00', cost='5.00', stock=200)

    def test_no_chart_is_built_from_no_data(self):
        self.assertIsNone(charts.build_grouped_chart([], label_key='x', series=[('y', 'Y')]))

    def test_no_chart_is_built_when_every_value_is_zero(self):
        rows = [{'x': 'a', 'y': 0}, {'x': 'b', 'y': 0}]
        self.assertIsNone(charts.build_grouped_chart(rows, label_key='x', series=[('y', 'Y')]))

    def test_bars_are_proportional_to_the_values(self):
        rows = [{'x': 'a', 'y': 50}, {'x': 'b', 'y': 100}]
        chart = charts.build_grouped_chart(rows, label_key='x', series=[('y', 'Y')])
        small = chart['groups'][0]['bars'][0]['height']
        large = chart['groups'][1]['bars'][0]['height']
        self.assertAlmostEqual(large / small, 2.0, places=5)

    def test_a_grouped_chart_has_one_bar_per_series(self):
        rows = [{'x': 'a', 'y': 10, 'z': 4}]
        chart = charts.build_grouped_chart(
            rows, label_key='x', series=[('y', 'Y'), ('z', 'Z')]
        )
        self.assertEqual(len(chart['groups'][0]['bars']), 2)
        self.assertEqual(len(chart['legend']), 2)

    def test_bars_never_overflow_the_plot_area(self):
        rows = [{'x': 'a', 'y': 999999}]
        chart = charts.build_grouped_chart(rows, label_key='x', series=[('y', 'Y')])
        bar = chart['groups'][0]['bars'][0]
        self.assertGreaterEqual(bar['y'], 0)
        self.assertLessEqual(bar['y'] + bar['height'], chart['baseline'] + 0.01)

    def test_chart_uses_real_sale_data(self):
        create_sale(cashier=self.manager, items=[(self.a.pk, 4)], amount_paid=Decimal('1000'))
        response = self.client.get(reverse('pos:report_products'))
        chart = response.context['chart']
        self.assertIsNotNone(chart)
        labels = [g['label'] for g in chart['groups']]
        self.assertIn('Alpha', labels)
        alpha = next(g for g in chart['groups'] if g['label'] == 'Alpha')
        self.assertEqual(alpha['bars'][0]['value'], Decimal('400.00'))

    def test_chart_follows_the_selected_filter(self):
        create_sale(cashier=self.manager, items=[(self.a.pk, 4)], amount_paid=Decimal('1000'))
        old = (timezone.localdate().replace(year=timezone.localdate().year - 2))
        response = self.client.get(
            reverse('pos:report_products'),
            {'from': old.isoformat(), 'to': old.isoformat()},
        )
        self.assertIsNone(response.context['chart'])

    def test_a_quiet_day_renders_without_a_chart(self):
        # Retargeted from the retired Daily Report onto the Sales Report, which
        # took over its job via the inclusive From/To range.
        response = self.client.get(
            reverse('pos:report_sales'), {'from': '2020-01-15', 'to': '2020-01-15'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['chart'])

    def test_chart_svg_is_rendered_inline(self):
        create_sale(cashier=self.manager, items=[(self.a.pk, 2)], amount_paid=Decimal('1000'))
        response = self.client.get(reverse('pos:report_products'))
        self.assertContains(response, '<svg class="chart"')
        self.assertContains(response, 'class="chart__bar"')
