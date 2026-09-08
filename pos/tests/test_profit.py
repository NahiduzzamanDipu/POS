"""Profit reporting: cost snapshots, margin arithmetic and honest gaps.

The rule this file defends is that a margin, once booked, is history. Changing
a product's cost price today must not rewrite what last month earned.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pos import reporting
from pos.models import Role, Sale, SaleItem
from pos.services import create_sale

from .factories import make_product, make_user, set_tax


class ProfitArithmeticTests(TestCase):
    """The worked example from the specification, and the edges around it."""

    def setUp(self):
        set_tax('0.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.today = timezone.localdate()

    def _sell(self, product, quantity, **kwargs):
        return create_sale(
            cashier=self.manager,
            items=[(product.pk, quantity)],
            amount_paid=Decimal('100000'),
            **kwargs,
        )

    def test_the_worked_example(self):
        """Cost 700, sells for 900, five sold -> 200 a unit, 1000 in total."""
        mouse = make_product(
            name='Logitech Mouse', sku='LM-1',
            cost='700.00', price='900.00', stock=50,
        )
        self._sell(mouse, 5)

        rows = reporting.profit_rows(reporting.sales_between(self.today, self.today))
        row = next(r for r in rows if r['product_name'] == 'Logitech Mouse')

        self.assertEqual(row['units'], 5)
        self.assertEqual(row['revenue'], Decimal('4500.00'))   # 900 x 5
        self.assertEqual(row['cost'], Decimal('3500.00'))      # 700 x 5
        self.assertEqual(row['profit'], Decimal('1000.00'))    # 200 x 5

    def test_margin_is_profit_over_revenue(self):
        product = make_product(sku='M-1', cost='500.00', price='1000.00', stock=10)
        self._sell(product, 2)

        totals = reporting.profit_summary(
            reporting.profit_rows(reporting.sales_between(self.today, self.today))
        )
        self.assertEqual(totals['revenue'], Decimal('2000.00'))
        self.assertEqual(totals['cost'], Decimal('1000.00'))
        self.assertEqual(totals['profit'], Decimal('1000.00'))
        self.assertEqual(totals['margin'], Decimal('50.00'))

    def test_no_sales_means_no_division_by_zero(self):
        """An empty range must report cleanly, not raise."""
        totals = reporting.profit_summary(
            reporting.profit_rows(reporting.sales_between(self.today, self.today))
        )
        self.assertEqual(totals['revenue'], Decimal('0.00'))
        self.assertEqual(totals['profit'], Decimal('0.00'))
        self.assertIsNone(totals['margin'])

    def test_selling_at_cost_earns_nothing(self):
        product = make_product(sku='Z-1', cost='300.00', price='300.00', stock=10)
        self._sell(product, 3)

        totals = reporting.profit_summary(
            reporting.profit_rows(reporting.sales_between(self.today, self.today))
        )
        self.assertEqual(totals['profit'], Decimal('0.00'))
        self.assertEqual(totals['margin'], Decimal('0.00'))

    def test_a_product_discount_reduces_the_profit_it_reduced(self):
        """Revenue is the line total after discount, so margin follows it."""
        product = make_product(
            sku='D-1', cost='600.00', price='1000.00',
            stock=10, discount_percent='10.00',
        )
        self._sell(product, 1)

        rows = reporting.profit_rows(reporting.sales_between(self.today, self.today))
        row = rows[0]
        self.assertEqual(row['revenue'], Decimal('900.00'))    # 1000 less 10%
        self.assertEqual(row['cost'], Decimal('600.00'))
        self.assertEqual(row['profit'], Decimal('300.00'))


class HistoricalCostTests(TestCase):
    """Repricing a product must not rewrite margins already booked."""

    def setUp(self):
        set_tax('0.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.today = timezone.localdate()
        self.product = make_product(sku='H-1', cost='500.00', price='700.00', stock=50)

    def _profit(self):
        rows = reporting.profit_rows(reporting.sales_between(self.today, self.today))
        return reporting.profit_summary(rows)['profit']

    def test_the_cost_is_snapshotted_onto_the_sale_line(self):
        create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                    amount_paid=Decimal('1000'))
        self.assertEqual(SaleItem.objects.get().unit_cost, Decimal('500.00'))

    def test_raising_the_cost_price_later_leaves_old_profit_alone(self):
        create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                    amount_paid=Decimal('1000'))
        self.assertEqual(self._profit(), Decimal('200.00'))     # 700 - 500

        self.product.cost_price = Decimal('600.00')
        self.product.save(update_fields=['cost_price'])

        # Still 200: the sale was made when the product cost 500.
        self.assertEqual(self._profit(), Decimal('200.00'))

    def test_a_later_sale_uses_the_new_cost(self):
        create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                    amount_paid=Decimal('1000'))
        self.product.cost_price = Decimal('600.00')
        self.product.save(update_fields=['cost_price'])
        create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                    amount_paid=Decimal('1000'))

        # 200 from the first sale, 100 from the second.
        self.assertEqual(self._profit(), Decimal('300.00'))

    def test_lines_with_no_recorded_cost_are_excluded_and_disclosed(self):
        """Sales predating the snapshot must not be valued at today's cost."""
        create_sale(cashier=self.manager, items=[(self.product.pk, 2)],
                    amount_paid=Decimal('2000'))
        SaleItem.objects.update(unit_cost=None)          # as a pre-upgrade row looks

        rows = reporting.profit_rows(reporting.sales_between(self.today, self.today))
        totals = reporting.profit_summary(rows)

        self.assertIsNone(rows[0]['profit'])
        self.assertFalse(totals['complete'])
        self.assertEqual(totals['units_unknown'], 2)
        self.assertEqual(totals['revenue_unknown'], Decimal('1400.00'))
        self.assertIsNone(totals['margin'])

    def test_a_mixed_range_reports_only_the_costed_part(self):
        known = create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                            amount_paid=Decimal('1000'))
        unknown = create_sale(cashier=self.manager, items=[(self.product.pk, 1)],
                              amount_paid=Decimal('1000'))
        SaleItem.objects.filter(sale=unknown).update(unit_cost=None)

        totals = reporting.profit_summary(
            reporting.profit_rows(reporting.sales_between(self.today, self.today))
        )
        self.assertEqual(totals['profit'], Decimal('200.00'))   # only the known sale
        self.assertEqual(totals['units_costed'], 1)
        self.assertEqual(totals['units_unknown'], 1)
        self.assertFalse(totals['complete'])
        self.assertTrue(known.pk and unknown.pk)


class ProfitReportViewTests(TestCase):
    def setUp(self):
        set_tax('0.00')
        self.manager = make_user('mgr', Role.MANAGER)
        self.cashier = make_user('till', Role.CASHIER)
        self.today = timezone.localdate()
        self.product = make_product(sku='V-1', cost='700.00', price='900.00', stock=100)
        self.client.force_login(self.manager)
        create_sale(cashier=self.manager, items=[(self.product.pk, 5)],
                    amount_paid=Decimal('10000'))

    def test_the_page_reports_the_right_figures(self):
        response = self.client.get(
            reverse('pos:report_profit'),
            {'from': self.today.isoformat(), 'to': self.today.isoformat()},
        )
        totals = response.context['totals']
        self.assertEqual(totals['revenue'], Decimal('4500.00'))
        self.assertEqual(totals['cost'], Decimal('3500.00'))
        self.assertEqual(totals['profit'], Decimal('1000.00'))
        self.assertContains(response, '1,000.00')

    def test_the_range_is_inclusive_at_both_ends(self):
        response = self.client.get(
            reverse('pos:report_profit'),
            {'from': self.today.isoformat(), 'to': self.today.isoformat()},
        )
        self.assertEqual(response.context['totals']['units'], 5)

    def test_a_range_before_the_sale_shows_nothing(self):
        old = (self.today.replace(year=self.today.year - 2)).isoformat()
        response = self.client.get(
            reverse('pos:report_profit'), {'from': old, 'to': old}
        )
        self.assertEqual(response.context['totals']['units'], 0)
        self.assertIsNone(response.context['totals']['margin'])

    def test_it_exports_csv(self):
        response = self.client.get(
            reverse('pos:report_profit'),
            {'from': self.today.isoformat(), 'to': self.today.isoformat(),
             'export': 'csv'},
        )
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('Margin %', response.content.decode())

    def test_a_cashier_cannot_open_it(self):
        self.client.force_login(self.cashier)
        self.assertEqual(
            self.client.get(reverse('pos:report_profit')).status_code, 403
        )

    def test_a_voided_sale_earns_no_profit(self):
        Sale.objects.update(status=Sale.Status.VOID)
        response = self.client.get(
            reverse('pos:report_profit'),
            {'from': self.today.isoformat(), 'to': self.today.isoformat()},
        )
        self.assertEqual(response.context['totals']['profit'], Decimal('0.00'))
