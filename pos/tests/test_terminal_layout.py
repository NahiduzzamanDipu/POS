"""The sales screen's shape: search-driven, and the cart always reachable.

These assert structure rather than pixels. A cashier should never have to
scroll the document to find the total or the Complete Sale button, and the
markup that guarantees that is worth pinning down.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from pos.models import Role
from pos.services import create_sale

from .factories import make_product, make_user, set_tax


class TerminalLayoutTests(TestCase):
    def setUp(self):
        set_tax('5.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.client.force_login(self.cashier)
        self.product = make_product(
            name='Mechanical Keyboard', sku='P-102',
            cost='700.00', price='1200.00', stock=30,
        )

    def test_the_page_opts_into_the_viewport_locked_layout(self):
        response = self.client.get(reverse('pos:pos_terminal'))
        self.assertContains(response, 'class="pos-page"')

    def test_the_cart_totals_and_checkout_sit_in_a_pinned_footer(self):
        """They must live outside the scrolling regions, not below them."""
        html = self.client.get(reverse('pos:pos_terminal')).content.decode()
        self.assertIn('cart__cta', html)
        cta = html.index('cart__cta')
        # Both the grand total and the submit button belong to that footer.
        self.assertGreater(html.index('id="t-total"'), cta)
        self.assertGreater(html.index('id="complete-sale"'), cta)

    def test_only_the_item_list_is_a_scrolling_region(self):
        html = self.client.get(reverse('pos:pos_terminal')).content.decode()
        self.assertIn('cart__lines', html)
        self.assertIn('pos__results-scroll', html)

    def test_no_product_catalogue_is_rendered_on_open(self):
        html = self.client.get(reverse('pos:pos_terminal')).content.decode()
        self.assertNotIn('Mechanical Keyboard', html)
        self.assertNotIn('product-tile', html)

    def test_the_search_box_mentions_id_and_sku(self):
        response = self.client.get(reverse('pos:pos_terminal'))
        self.assertContains(response, 'product-search')
        self.assertContains(response, 'SKU')

    def test_the_customer_number_field_is_present_and_visible(self):
        response = self.client.get(reverse('pos:pos_terminal'))
        self.assertContains(response, 'id="customer-number"')
        self.assertContains(response, 'Customer Number')

    def test_the_lookup_api_returns_what_a_cart_line_needs(self):
        """Name, SKU and price all come from one call, so the cart can show them."""
        response = self.client.get(reverse('pos:product_lookup'), {'q': 'Keyboard'})
        row = response.json()['results'][0]
        for key in ('id', 'name', 'sku', 'price', 'discount_percent',
                    'final_price', 'stock'):
            self.assertIn(key, row)
        self.assertEqual(row['sku'], 'P-102')

    def test_searching_by_sku_finds_the_product(self):
        response = self.client.get(reverse('pos:product_lookup'), {'q': 'P-102'})
        names = [r['name'] for r in response.json()['results']]
        self.assertIn('Mechanical Keyboard', names)


class CustomerDiscountStillAppliesTests(TestCase):
    """The redesign must not disturb the returning-customer rule (BR-026)."""

    NUMBER = '01712345678'

    def setUp(self):
        set_tax('0.00')
        self.store = set_tax('0.00', existing_customer_discount_percent=Decimal('2.00'))
        self.cashier = make_user('till', Role.CASHIER)
        self.client.force_login(self.cashier)
        self.product = make_product(sku='P-1', cost='500.00', price='1000.00', stock=50)

    def _quote(self):
        response = self.client.get(reverse('pos:cart_quote'), {
            'cart': f'{self.product.pk}:1',
            'customer_number': self.NUMBER,
        })
        return response.json()

    def test_a_first_time_number_earns_no_discount(self):
        quote = self._quote()
        self.assertFalse(quote['existing_customer'])
        self.assertEqual(Decimal(quote['customer_discount']), Decimal('0.00'))

    def test_a_returning_number_earns_two_percent_automatically(self):
        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number=self.NUMBER, amount_paid=Decimal('2000'))
        quote = self._quote()
        self.assertTrue(quote['existing_customer'])
        self.assertEqual(Decimal(quote['customer_discount_percent']), Decimal('2.00'))
        self.assertEqual(Decimal(quote['customer_discount']), Decimal('20.00'))
        self.assertEqual(Decimal(quote['total']), Decimal('980.00'))

    def test_the_quote_returns_the_per_line_figures_the_cart_shows(self):
        quote = self._quote()
        line = quote['lines'][0]
        for key in ('id', 'name', 'quantity', 'unit_price',
                    'discount_percent', 'discount', 'subtotal'):
            self.assertIn(key, line)
