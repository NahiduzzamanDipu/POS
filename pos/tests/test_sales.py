"""Sales, pricing, payment and invoicing (BR-013 .. BR-022, BRL-3, BRL-8, BRL-10)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from pos.models import Payment, Role, Sale, SaleItem, StockMovement
from pos.services import BusinessRuleError, create_sale, price_cart, void_sale

from .factories import make_product, make_user, set_tax


class CreateSaleTests(TestCase):
    def setUp(self):
        set_tax('5.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.product = make_product(price='100.00', stock=10)

    def test_sale_records_items_payment_and_invoice(self):
        sale = create_sale(
            cashier=self.cashier,
            items=[(self.product.pk, 2)],
            amount_paid=Decimal('250.00'),
        )
        self.assertTrue(sale.invoice_no.startswith('INV-'))
        self.assertEqual(sale.subtotal, Decimal('200.00'))
        self.assertEqual(sale.tax_amount, Decimal('10.00'))
        self.assertEqual(sale.total_amount, Decimal('210.00'))
        self.assertEqual(sale.change_due, Decimal('40.00'))
        self.assertEqual(sale.status, Sale.Status.COMPLETED)
        self.assertEqual(SaleItem.objects.filter(sale=sale).count(), 1)
        self.assertEqual(Payment.objects.filter(sale=sale, status='PAID').count(), 1)

    def test_successful_sale_reduces_stock_and_logs_the_movement(self):
        """BR-008 / BRL-8: 10 in stock, 3 sold, 7 remain."""
        create_sale(cashier=self.cashier, items=[(self.product.pk, 3)])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)
        movement = StockMovement.objects.filter(
            product=self.product, reason=StockMovement.Reason.SALE
        ).first()
        self.assertEqual(movement.quantity_change, -3)
        self.assertEqual(movement.balance_after, 7)

    def test_selling_more_than_stock_is_refused(self):
        """BRL-3."""
        with self.assertRaises(BusinessRuleError) as ctx:
            create_sale(cashier=self.cashier, items=[(self.product.pk, 11)])
        self.assertIn('only 10 in stock', str(ctx.exception))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)

    def test_a_refused_sale_leaves_no_partial_record(self):
        """NFR 23.4: a transaction is never half-written."""
        second = make_product(name='Other', sku='SKU-OTHER', price='50.00', stock=1)
        with self.assertRaises(BusinessRuleError):
            create_sale(
                cashier=self.cashier,
                items=[(self.product.pk, 1), (second.pk, 5)],
            )
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(SaleItem.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)

    def test_out_of_stock_product_cannot_be_sold(self):
        empty = make_product(name='Empty', sku='SKU-EMPTY', stock=0)
        with self.assertRaises(BusinessRuleError):
            create_sale(cashier=self.cashier, items=[(empty.pk, 1)])

    def test_inactive_product_cannot_be_sold(self):
        self.product.is_active = False
        self.product.save()
        with self.assertRaises(BusinessRuleError) as ctx:
            create_sale(cashier=self.cashier, items=[(self.product.pk, 1)])
        self.assertIn('not available for sale', str(ctx.exception))

    def test_short_cash_payment_is_refused(self):
        """BR-020 / BR-021."""
        with self.assertRaises(BusinessRuleError) as ctx:
            create_sale(
                cashier=self.cashier, items=[(self.product.pk, 1)],
                amount_paid=Decimal('10.00'),
            )
        self.assertIn('less than the total', str(ctx.exception))

    def test_card_payment_captures_the_exact_total(self):
        sale = create_sale(
            cashier=self.cashier, items=[(self.product.pk, 1)],
            payment_method='CARD', amount_paid=Decimal('5.00'),
        )
        self.assertEqual(sale.amount_paid, sale.total_amount)
        self.assertEqual(sale.change_due, Decimal('0.00'))

    def test_duplicate_lines_are_merged_into_one_item(self):
        sale = create_sale(cashier=self.cashier, items=[(self.product.pk, 2), (self.product.pk, 3)])
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.items.first().quantity, 5)

    def test_invoice_numbers_are_unique(self):
        """BRL-2."""
        first = create_sale(cashier=self.cashier, items=[(self.product.pk, 1)])
        second = create_sale(cashier=self.cashier, items=[(self.product.pk, 1)])
        self.assertNotEqual(first.invoice_no, second.invoice_no)

    def test_empty_cart_is_refused(self):
        with self.assertRaises(BusinessRuleError):
            create_sale(cashier=self.cashier, items=[])

    def test_a_cashier_cannot_dictate_any_discount(self):
        """There is no cart-level discount input any more: extra POST data is ignored."""
        sale = create_sale(cashier=self.cashier, items=[(self.product.pk, 1)])
        self.assertEqual(sale.discount_amount, Decimal('0.00'))
        self.assertEqual(sale.customer_discount_amount, Decimal('0.00'))


class VoidSaleTests(TestCase):
    def setUp(self):
        self.manager = make_user('mgr', Role.MANAGER)
        self.product = make_product(price='100.00', stock=10)
        self.sale = create_sale(cashier=self.manager, items=[(self.product.pk, 4)])

    def test_voiding_restores_stock_and_keeps_the_record(self):
        """BRL-6 / BRL-7."""
        void_sale(self.sale, self.manager, 'Customer changed their mind')
        self.sale.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.sale.status, Sale.Status.VOID)
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertTrue(Sale.objects.filter(pk=self.sale.pk).exists())

    def test_a_sale_cannot_be_voided_twice(self):
        void_sale(self.sale, self.manager, 'first')
        with self.assertRaises(BusinessRuleError):
            void_sale(self.sale, self.manager, 'again')


class CheckoutViewTests(TestCase):
    def setUp(self):
        set_tax('5.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.product = make_product(price='100.00', stock=10)
        self.client.force_login(self.cashier)

    def _token(self):
        return self.client.get(reverse('pos:pos_terminal')).context['txn_token']

    def test_checkout_creates_a_sale_and_shows_the_invoice(self):
        response = self.client.post(
            reverse('pos:checkout'),
            {
                'cart': f'{self.product.pk}:2',
                'payment_method': 'CASH',
                'amount_paid': '300.00',
                'txn_token': self._token(),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get()
        self.assertContains(response, sale.invoice_no)

    def test_replaying_a_token_does_not_charge_twice(self):
        token = self._token()
        payload = {
            'cart': f'{self.product.pk}:1',
            'payment_method': 'CASH',
            'amount_paid': '200.00',
            'txn_token': token,
        }
        self.client.post(reverse('pos:checkout'), payload)
        self.client.post(reverse('pos:checkout'), payload, follow=True)
        self.assertEqual(Sale.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 9)

    def test_malformed_cart_is_rejected(self):
        self.client.post(
            reverse('pos:checkout'),
            {'cart': 'nonsense', 'payment_method': 'CASH', 'txn_token': self._token()},
        )
        self.assertEqual(Sale.objects.count(), 0)

    def test_checkout_requires_post(self):
        self.assertEqual(self.client.get(reverse('pos:checkout')).status_code, 405)

    def test_quote_endpoint_returns_server_side_totals(self):
        response = self.client.get(
            reverse('pos:cart_quote'), {'cart': f'{self.product.pk}:2'}
        )
        self.assertEqual(response.json()['total'], '210.00')

    def test_quote_reflects_an_existing_customer(self):
        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number='01712345678', amount_paid=Decimal('500'))
        response = self.client.get(
            reverse('pos:cart_quote'),
            {'cart': f'{self.product.pk}:2', 'customer_number': '01712345678'},
        )
        data = response.json()
        self.assertTrue(data['existing_customer'])
        self.assertEqual(data['customer_discount'], '4.00')

    def test_customer_lookup_endpoint(self):
        response = self.client.get(reverse('pos:customer_lookup'), {'number': '01712345678'})
        self.assertEqual(response.json()['existing'], False)
        create_sale(cashier=self.cashier, items=[(self.product.pk, 1)],
                    customer_number='01712345678', amount_paid=Decimal('500'))
        response = self.client.get(reverse('pos:customer_lookup'), {'number': '01712345678'})
        self.assertTrue(response.json()['existing'])

    def test_checkout_records_the_customer_number(self):
        self.client.post(
            reverse('pos:checkout'),
            {
                'cart': f'{self.product.pk}:1',
                'customer_number': '01712345678',
                'payment_method': 'CASH',
                'amount_paid': '500.00',
                'txn_token': self._token(),
            },
        )
        sale = Sale.objects.get()
        self.assertEqual(sale.customer_number, '01712345678')
        self.assertEqual(sale.customer.phone, '01712345678')

    def test_product_lookup_finds_by_name(self):
        response = self.client.get(reverse('pos:product_lookup'), {'q': 'Rice'})
        self.assertEqual(len(response.json()['results']), 1)


class SaleVisibilityTests(TestCase):
    def setUp(self):
        self.cashier_a = make_user('a', Role.CASHIER)
        self.cashier_b = make_user('b', Role.CASHIER)
        self.manager = make_user('m', Role.MANAGER)
        product = make_product(stock=20)
        self.sale_a = create_sale(cashier=self.cashier_a, items=[(product.pk, 1)])

    def test_a_cashier_cannot_open_another_cashiers_sale(self):
        self.client.force_login(self.cashier_b)
        self.assertEqual(
            self.client.get(reverse('pos:sale_detail', args=[self.sale_a.pk])).status_code, 404
        )

    def test_a_manager_sees_every_sale(self):
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse('pos:sale_detail', args=[self.sale_a.pk])).status_code, 200
        )
