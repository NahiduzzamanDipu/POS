"""Pricing: product-level discount, then the automatic existing-customer discount."""

from decimal import Decimal

from django.test import TestCase

from pos.customers import (
    is_existing_customer,
    normalise_customer_number,
    validate_customer_number,
)
from pos.models import Role, Sale
from pos.services import BusinessRuleError, create_sale, price_cart

from .factories import make_product, make_user, set_tax


class ProductDiscountTests(TestCase):
    """Section 8 of the spec: each product carries its own discount."""

    def setUp(self):
        set_tax('0.00')

    def test_product_final_price(self):
        product = make_product(sku='P1', price='1000.00', cost='100.00', discount_percent='10.00')
        self.assertEqual(product.discount_amount, Decimal('100.00'))
        self.assertEqual(product.final_price, Decimal('900.00'))

    def test_two_products_with_different_discounts(self):
        """The worked example: 1000 @5% + 2000 @10% = 950 + 1800 = 2750."""
        a = make_product(name='A', sku='P-A', price='1000.00', cost='100.00',
                         discount_percent='5.00')
        b = make_product(name='B', sku='P-B', price='2000.00', cost='100.00',
                         discount_percent='10.00')
        totals = price_cart([(a, 1), (b, 1)])
        self.assertEqual(totals.subtotal, Decimal('3000.00'))
        self.assertEqual(totals.product_discount, Decimal('250.00'))
        self.assertEqual(totals.lines[0].subtotal, Decimal('950.00'))
        self.assertEqual(totals.lines[1].subtotal, Decimal('1800.00'))
        self.assertEqual(totals.net_before_tax, Decimal('2750.00'))
        self.assertEqual(totals.total, Decimal('2750.00'))

    def test_product_discount_scales_with_quantity(self):
        product = make_product(sku='P1', price='100.00', cost='10.00', discount_percent='10.00')
        totals = price_cart([(product, 3)])
        self.assertEqual(totals.subtotal, Decimal('300.00'))
        self.assertEqual(totals.product_discount, Decimal('30.00'))
        self.assertEqual(totals.total, Decimal('270.00'))

    def test_zero_discount_leaves_the_price_alone(self):
        product = make_product(sku='P1', price='500.00', cost='10.00')
        totals = price_cart([(product, 2)])
        self.assertEqual(totals.product_discount, Decimal('0.00'))
        self.assertEqual(totals.total, Decimal('1000.00'))


class CustomerDiscountTests(TestCase):
    """Sections 13-14: automatic 2%, applied once, after product discounts."""

    def setUp(self):
        set_tax('0.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.product = make_product(sku='P1', price='1000.00', cost='10.00', stock=100)

    def test_new_customer_gets_no_discount(self):
        totals = price_cart([(self.product, 5)], existing_customer=False)
        self.assertEqual(totals.customer_discount, Decimal('0.00'))
        self.assertEqual(totals.total, Decimal('5000.00'))

    def test_existing_customer_gets_two_percent(self):
        """Spec example: 5000 subtotal, 2% = 100 off, net 4900."""
        totals = price_cart([(self.product, 5)], existing_customer=True)
        self.assertEqual(totals.customer_discount, Decimal('100.00'))
        self.assertEqual(totals.total, Decimal('4900.00'))

    def test_worked_example_from_the_spec(self):
        """1000 @5% + 2000 @10% -> 2750 subtotal, 2% = 55, net 2695."""
        a = make_product(name='A', sku='P-A', price='1000.00', cost='10.00',
                         discount_percent='5.00', stock=10)
        b = make_product(name='B', sku='P-B', price='2000.00', cost='10.00',
                         discount_percent='10.00', stock=10)
        totals = price_cart([(a, 1), (b, 1)], existing_customer=True)
        self.assertEqual(totals.product_discount, Decimal('250.00'))
        self.assertEqual(totals.customer_discount, Decimal('55.00'))
        self.assertEqual(totals.net_before_tax, Decimal('2695.00'))

    def test_customer_discount_is_applied_once_not_compounded(self):
        product = make_product(name='C', sku='P-C', price='1000.00', cost='10.00',
                               discount_percent='10.00', stock=10)
        totals = price_cart([(product, 1)], existing_customer=True)
        # 1000 - 100 = 900, then 2% of 900 = 18. Never 2% of 1000, never twice.
        self.assertEqual(totals.product_discount, Decimal('100.00'))
        self.assertEqual(totals.customer_discount, Decimal('18.00'))
        self.assertEqual(totals.net_before_tax, Decimal('882.00'))

    def test_tax_applies_after_both_discounts(self):
        set_tax('5.00')
        totals = price_cart([(self.product, 5)], existing_customer=True)
        self.assertEqual(totals.net_before_tax, Decimal('4900.00'))
        self.assertEqual(totals.tax_amount, Decimal('245.00'))
        self.assertEqual(totals.total, Decimal('5145.00'))

    def test_line_subtotals_sum_to_the_product_discounted_amount(self):
        a = make_product(name='A', sku='P-A', price='333.33', cost='1.00',
                         discount_percent='7.00', stock=10)
        b = make_product(name='B', sku='P-B', price='111.11', cost='1.00',
                         discount_percent='3.00', stock=10)
        totals = price_cart([(a, 3), (b, 2)], existing_customer=True)
        self.assertEqual(
            sum(line.subtotal for line in totals.lines),
            totals.subtotal - totals.product_discount,
        )


class CustomerNumberTests(TestCase):
    def test_normalisation_of_every_accepted_form(self):
        for raw in ['01712345678', '+8801712345678', '8801712345678',
                    '017-1234 5678', '00 8801712345678']:
            self.assertEqual(normalise_customer_number(raw), '01712345678', msg=raw)

    def test_invalid_numbers_are_rejected(self):
        for raw in ['12345', 'abcdefg', '0171234567', '02112345678']:
            with self.assertRaises(Exception, msg=raw):
                validate_customer_number(raw)

    def test_a_blank_number_is_allowed_when_optional(self):
        self.assertEqual(validate_customer_number('', required=False), '')


class AutomaticDetectionTests(TestCase):
    """Section 12: the system decides, the cashier never picks old/new."""

    def setUp(self):
        set_tax('0.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.product = make_product(sku='P1', price='1000.00', cost='10.00', stock=100)
        self.number = '01712345678'

    def test_first_ever_sale_gets_no_customer_discount(self):
        sale = create_sale(
            cashier=self.cashier, items=[(self.product.pk, 5)],
            customer_number=self.number, amount_paid=Decimal('6000'),
        )
        self.assertEqual(sale.customer_discount_amount, Decimal('0.00'))
        self.assertEqual(sale.total_amount, Decimal('5000.00'))
        self.assertEqual(sale.customer_number, self.number)

    def test_second_sale_earns_the_loyalty_discount(self):
        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number=self.number, amount_paid=Decimal('2000'))
        second = create_sale(
            cashier=self.cashier, items=[(self.product.pk, 5)],
            customer_number=self.number, amount_paid=Decimal('6000'),
        )
        self.assertEqual(second.customer_discount_percent, Decimal('2.00'))
        self.assertEqual(second.customer_discount_amount, Decimal('100.00'))
        self.assertEqual(second.total_amount, Decimal('4900.00'))

    def test_a_different_number_is_still_a_new_customer(self):
        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number=self.number, amount_paid=Decimal('2000'))
        other = create_sale(
            cashier=self.cashier, items=[(self.product.pk, 1)],
            customer_number='01812345678', amount_paid=Decimal('2000'),
        )
        self.assertEqual(other.customer_discount_amount, Decimal('0.00'))

    def test_number_variants_match_the_same_customer(self):
        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number='+8801712345678', amount_paid=Decimal('2000'))
        self.assertTrue(is_existing_customer('01712345678'))

    def test_a_voided_sale_does_not_earn_loyalty(self):
        from pos.services import void_sale

        first = create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                            customer_number=self.number, amount_paid=Decimal('2000'))
        manager = make_user('mgr', Role.MANAGER)
        void_sale(first, manager, 'cancelled')
        self.assertFalse(is_existing_customer(self.number))

    def test_the_customer_record_is_created_automatically(self):
        from pos.models import Customer

        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number=self.number, amount_paid=Decimal('2000'))
        customer = Customer.objects.get(phone=self.number)
        self.assertEqual(customer.name, '')

    def test_repeat_sales_do_not_duplicate_the_customer(self):
        from pos.models import Customer

        for _ in range(3):
            create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                        customer_number=self.number, amount_paid=Decimal('2000'))
        self.assertEqual(Customer.objects.filter(phone=self.number).count(), 1)

    def test_a_walk_in_sale_needs_no_number(self):
        sale = create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                           amount_paid=Decimal('2000'))
        self.assertEqual(sale.customer_number, '')
        self.assertIsNone(sale.customer)
        self.assertEqual(sale.customer_discount_amount, Decimal('0.00'))

    def test_an_invalid_number_is_refused(self):
        with self.assertRaises(Exception):
            create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                        customer_number='12345', amount_paid=Decimal('2000'))
        self.assertEqual(Sale.objects.count(), 0)

    def test_sale_discount_total_is_the_sum_of_both_kinds(self):
        product = make_product(name='D', sku='P-D', price='1000.00', cost='10.00',
                               discount_percent='10.00', stock=10)
        create_sale(cashier=self.cashier, items=[(product.pk, 1)],
                    customer_number=self.number, amount_paid=Decimal('2000'))
        sale = create_sale(cashier=self.cashier, items=[(product.pk, 1)],
                           customer_number=self.number, amount_paid=Decimal('2000'))
        self.assertEqual(sale.product_discount_amount, Decimal('100.00'))
        self.assertEqual(sale.customer_discount_amount, Decimal('18.00'))
        self.assertEqual(sale.discount_amount, Decimal('118.00'))
