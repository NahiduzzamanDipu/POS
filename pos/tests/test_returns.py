"""Returns and refunds (BR-028, BR-029, BRL-9)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from pos.models import Role, Sale, SaleReturn, StockMovement
from pos.services import BusinessRuleError, create_sale, process_return, void_sale

from .factories import make_product, make_user, set_tax


class ReturnTests(TestCase):
    def setUp(self):
        set_tax('5.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.product = make_product(price='100.00', stock=20)
        self.sale = create_sale(cashier=self.cashier, items=[(self.product.pk, 5)])
        self.item = self.sale.items.first()

    def test_partial_return_refunds_with_tax_and_restocks(self):
        sale_return = process_return(
            sale=self.sale,
            lines=[(self.item.pk, 2)],
            reason='Damaged packaging',
            user=self.cashier,
        )
        self.product.refresh_from_db()
        self.sale.refresh_from_db()
        self.item.refresh_from_db()

        self.assertEqual(sale_return.refund_amount, Decimal('210.00'))  # 2 x 100 + 5% tax
        self.assertEqual(self.product.stock_quantity, 17)               # 20 - 5 + 2
        self.assertEqual(self.item.returned_quantity, 2)
        self.assertEqual(self.sale.status, Sale.Status.PARTIALLY_RETURNED)
        self.assertEqual(self.sale.refunded_amount, Decimal('210.00'))
        self.assertTrue(
            self.product.movements.filter(reason=StockMovement.Reason.RETURN).exists()
        )

    def test_returning_everything_marks_the_sale_returned(self):
        process_return(
            sale=self.sale, lines=[(self.item.pk, 5)], reason='Wrong item', user=self.cashier
        )
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, Sale.Status.RETURNED)
        self.assertEqual(self.sale.refunded_amount, self.sale.total_amount)

    def test_returning_more_than_sold_is_refused(self):
        with self.assertRaises(BusinessRuleError) as ctx:
            process_return(
                sale=self.sale, lines=[(self.item.pk, 6)], reason='Too many', user=self.cashier
            )
        self.assertIn('remain returnable', str(ctx.exception))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 15)
        self.assertEqual(SaleReturn.objects.count(), 0)

    def test_two_returns_cannot_exceed_the_original_quantity(self):
        process_return(
            sale=self.sale, lines=[(self.item.pk, 3)], reason='First', user=self.cashier
        )
        with self.assertRaises(BusinessRuleError):
            process_return(
                sale=self.sale, lines=[(self.item.pk, 3)], reason='Second', user=self.cashier
            )
        self.item.refresh_from_db()
        self.assertEqual(self.item.returned_quantity, 3)

    def test_unsaleable_returns_do_not_restock(self):
        process_return(
            sale=self.sale,
            lines=[(self.item.pk, 2)],
            reason='Broken',
            user=self.cashier,
            restock=False,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 15)
        self.assertTrue(
            self.product.movements.filter(reason=StockMovement.Reason.DAMAGE).exists()
        )

    def test_empty_return_is_refused(self):
        with self.assertRaises(BusinessRuleError):
            process_return(
                sale=self.sale, lines=[(self.item.pk, 0)], reason='Nothing', user=self.cashier
            )

    def test_items_from_another_invoice_are_refused(self):
        other = create_sale(cashier=self.cashier, items=[(self.product.pk, 1)])
        with self.assertRaises(BusinessRuleError) as ctx:
            process_return(
                sale=self.sale,
                lines=[(other.items.first().pk, 1)],
                reason='Mismatch',
                user=self.cashier,
            )
        self.assertIn('does not belong to this invoice', str(ctx.exception))

    def test_a_voided_sale_cannot_be_returned_against(self):
        manager = make_user('mgr', Role.MANAGER)
        void_sale(self.sale, manager, 'Cancelled')
        with self.assertRaises(BusinessRuleError):
            process_return(
                sale=self.sale, lines=[(self.item.pk, 1)], reason='Late', user=self.cashier
            )

    def test_discounted_sale_refunds_the_net_price(self):
        self.product.discount_percent = Decimal('10.00')
        self.product.save(update_fields=['discount_percent'])
        sale = create_sale(
            cashier=self.cashier,
            items=[(self.product.pk, 4)],
        )
        item = sale.items.first()
        # 4 x 100 = 400, less 10% = 360, so 90.00 per unit plus 5% tax.
        sale_return = process_return(
            sale=sale, lines=[(item.pk, 1)], reason='Return', user=self.cashier
        )
        self.assertEqual(sale_return.refund_amount, Decimal('94.50'))


class ReturnViewTests(TestCase):
    def setUp(self):
        set_tax('5.00')
        self.cashier = make_user('till', Role.CASHIER)
        self.stock_staff = make_user('store', Role.INVENTORY)
        self.product = make_product(price='100.00', stock=20)
        self.sale = create_sale(cashier=self.cashier, items=[(self.product.pk, 3)])
        self.item = self.sale.items.first()

    def test_cashier_can_process_a_return_through_the_form(self):
        self.client.force_login(self.cashier)
        self.client.post(
            reverse('pos:return_create', args=[self.sale.pk]),
            {f'quantity_{self.item.pk}': '1', 'reason': 'Faulty', 'restock': 'on'},
        )
        self.assertEqual(SaleReturn.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 18)

    def test_over_quantity_through_the_form_is_refused(self):
        self.client.force_login(self.cashier)
        response = self.client.post(
            reverse('pos:return_create', args=[self.sale.pk]),
            {f'quantity_{self.item.pk}': '9', 'reason': 'Too many', 'restock': 'on'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SaleReturn.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 17)

    def test_a_reason_is_required(self):
        self.client.force_login(self.cashier)
        self.client.post(
            reverse('pos:return_create', args=[self.sale.pk]),
            {f'quantity_{self.item.pk}': '1', 'reason': '', 'restock': 'on'},
        )
        self.assertEqual(SaleReturn.objects.count(), 0)

    def test_inventory_staff_cannot_process_returns(self):
        self.client.force_login(self.stock_staff)
        self.assertEqual(
            self.client.get(reverse('pos:return_create', args=[self.sale.pk])).status_code, 403
        )
