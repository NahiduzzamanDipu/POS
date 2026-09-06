"""Every report tile must actually print its number.

A summary tile bound to the wrong context key renders as an empty string rather
than raising, so a page can look finished while quietly showing nothing. These
tests read the rendered tiles back and assert a figure is present, which is the
failure the Cash Flow page shipped with.
"""

import re
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pos.models import Role
from pos.services import create_sale

from .factories import make_product, make_user, set_tax

TILE = re.compile(
    r'stat__label">\s*(.*?)\s*</div>.*?stat__value[^>]*>\s*(.*?)\s*</div>',
    re.S,
)

RANGED = [
    'report_sales', 'report_profit', 'report_cashflow',
    'report_collections', 'report_suppliers',
]


def tiles(html):
    """(label, value) for each summary tile, with tags and entities stripped."""
    out = []
    for label, value in TILE.findall(html):
        label = ' '.join(re.sub(r'<[^>]+>', ' ', label).split())
        value = ' '.join(re.sub(r'<[^>]+>', ' ', value).split())
        out.append((label, value))
    return out


class ReportTileTests(TestCase):
    def setUp(self):
        set_tax('5.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.client.force_login(self.manager)
        self.today = timezone.localdate()
        product = make_product(sku='T-1', cost='400.00', price='600.00', stock=40)
        create_sale(cashier=self.manager, items=[(product.pk, 3)],
                    customer_number='01712345678', amount_paid=Decimal('5000'))

    def _range(self):
        return {'from': self.today.isoformat(), 'to': self.today.isoformat()}

    def test_every_tile_on_every_report_prints_a_value(self):
        for name in RANGED + ['reports', 'report_inventory', 'report_products']:
            params = self._range() if name in RANGED or name == 'report_products' else {}
            with self.subTest(report=name):
                html = self.client.get(reverse(f'pos:{name}'), params).content.decode()
                found = tiles(html)
                self.assertTrue(found, f'{name} rendered no summary tiles at all')
                for label, value in found:
                    # A tile may legitimately read "0.00" or a dash, but never
                    # the currency symbol alone or an empty box.
                    self.assertTrue(
                        value and value not in {'৳', 'Tk', '৳'},
                        f'{name}: tile "{label}" printed no figure (got {value!r})',
                    )

    def test_cash_flow_tiles_carry_the_right_figures(self):
        """Regression: these three were bound to dicts and rendered blank."""
        html = self.client.get(
            reverse('pos:report_cashflow'), self._range()
        ).content.decode()
        found = dict(tiles(html))

        for label in ('Cash in', 'Cash out', 'Net cash flow',
                      'Sales takings', 'Refunds paid', 'Purchases paid'):
            self.assertIn(label, found)
            self.assertRegex(
                found[label], r'\d',
                f'"{label}" printed no digits: {found[label]!r}',
            )

    def test_the_reports_all_carry_the_tab_strip(self):
        for name in RANGED + ['reports', 'report_inventory', 'report_products',
                              'report_customers']:
            with self.subTest(report=name):
                html = self.client.get(reverse(f'pos:{name}')).content.decode()
                self.assertIn('report-tabs', html)
                self.assertIn(reverse('pos:report_profit'), html)

    def test_no_report_offers_daily_monthly_or_yearly(self):
        """Those three were folded into the Sales Report; the tabs must not return."""
        for name in RANGED + ['reports', 'report_inventory']:
            with self.subTest(report=name):
                html = self.client.get(reverse(f'pos:{name}')).content.decode()
                for gone in ('>Daily Report<', '>Monthly Report<', '>Yearly Report<'):
                    self.assertNotIn(gone, html)


class CostPriceVisibilityTests(TestCase):
    """Cost is internal margin data, shown only to roles that may set prices."""

    def setUp(self):
        set_tax('0.00')
        self.product = make_product(sku='C-1', cost='450.00', price='700.00', stock=5)

    def test_a_manager_sees_the_cost_price_column(self):
        self.client.force_login(make_user('mgr', Role.MANAGER))
        for page in ('product_list', 'inventory'):
            with self.subTest(page=page):
                html = self.client.get(reverse(f'pos:{page}')).content.decode()
                self.assertIn('Cost Price', html)
                self.assertIn('450.00', html)

    def test_a_cashier_never_sees_the_cost_price(self):
        self.client.force_login(make_user('till', Role.CASHIER))
        html = self.client.get(reverse('pos:product_list')).content.decode()
        self.assertNotIn('Cost Price', html)
        self.assertNotIn('450.00', html)

    def test_a_product_with_no_cost_is_flagged_rather_than_shown_as_free(self):
        make_product(name='Uncosted', sku='C-2', cost='0.00', price='300.00', stock=5)
        self.client.force_login(make_user('mgr', Role.MANAGER))
        html = self.client.get(reverse('pos:product_list')).content.decode()
        self.assertIn('not set', html)
