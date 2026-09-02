"""Inventory, products and purchases (BR-003 .. BR-012)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from pos.models import Product, Purchase, Role, StockMovement
from pos.services import BusinessRuleError, adjust_stock, receive_purchase

from .factories import make_category, make_product, make_user, set_tax


class StockTests(TestCase):
    def setUp(self):
        self.staff = make_user('store', Role.INVENTORY)
        self.product = make_product(stock=20, min_level=5)

    def test_adding_stock_records_a_movement_with_the_new_balance(self):
        adjust_stock(self.product, 15, StockMovement.Reason.PURCHASE, user=self.staff)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 35)
        self.assertEqual(self.product.movements.first().balance_after, 35)

    def test_removing_more_than_available_is_refused(self):
        with self.assertRaises(BusinessRuleError):
            adjust_stock(self.product, -21, StockMovement.Reason.DAMAGE, user=self.staff)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 20)

    def test_zero_change_is_refused(self):
        with self.assertRaises(BusinessRuleError):
            adjust_stock(self.product, 0, StockMovement.Reason.ADJUSTMENT)

    def test_negative_stock_is_allowed_when_the_store_permits_backorders(self):
        set_tax('5.00', allow_negative_stock=True)
        adjust_stock(self.product, -25, StockMovement.Reason.SALE, user=self.staff)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, -5)

    def test_low_stock_detection(self):
        """BR-009: at or below the minimum counts as low."""
        adjust_stock(self.product, -15, StockMovement.Reason.SALE)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)
        self.assertEqual(self.product.stock_status, 'low')
        self.assertIn(self.product, Product.objects.low_stock())

    def test_out_of_stock_detection(self):
        """BR-010."""
        adjust_stock(self.product, -20, StockMovement.Reason.SALE)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_status, 'out')
        self.assertIn(self.product, Product.objects.out_of_stock())
        self.assertNotIn(self.product, Product.objects.low_stock())

    def test_stock_adjustment_view_updates_the_balance(self):
        self.client.force_login(self.staff)
        self.client.post(
            reverse('pos:stock_adjust'),
            {
                'product': self.product.pk,
                'reason': StockMovement.Reason.ADJUSTMENT,
                'quantity_change': 7,
                'note': 'Recount',
            },
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 27)

    def test_stock_adjustment_view_rejects_an_impossible_removal(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('pos:stock_adjust'),
            {
                'product': self.product.pk,
                'reason': StockMovement.Reason.DAMAGE,
                'quantity_change': -100,
                'note': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 20)


class ProductTests(TestCase):
    def setUp(self):
        self.manager = make_user('mgr', Role.MANAGER)
        self.category = make_category()
        self.client.force_login(self.manager)

    def _payload(self, **overrides):
        payload = {
            'name': 'Coffee 200g',
            'sku': 'sku-new-1',
            'barcode': '',
            'category': self.category.pk,
            'supplier': '',
            'brand': 'Nescafe',
            'cost_price': '260.00',
            'selling_price': '350.00',
            'min_stock_level': 10,
            'unit': 'pack',
            'is_active': 'on',
        }
        payload.update(overrides)
        return payload

    def test_a_product_can_be_created(self):
        self.client.post(reverse('pos:product_create'), self._payload())
        product = Product.objects.get(sku='SKU-NEW-1')
        self.assertEqual(product.name, 'Coffee 200g')
        self.assertEqual(product.stock_quantity, 0)

    def test_sku_is_normalised_to_uppercase(self):
        self.client.post(reverse('pos:product_create'), self._payload(sku='lower-99'))
        self.assertTrue(Product.objects.filter(sku='LOWER-99').exists())

    def test_duplicate_sku_is_rejected(self):
        """BRL-1."""
        self.client.post(reverse('pos:product_create'), self._payload())
        self.client.post(reverse('pos:product_create'), self._payload(name='Copy'))
        self.assertEqual(Product.objects.filter(sku='SKU-NEW-1').count(), 1)

    def test_selling_below_cost_is_rejected(self):
        response = self.client.post(
            reverse('pos:product_create'),
            self._payload(cost_price='500.00', selling_price='100.00'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(sku='SKU-NEW-1').exists())

    def test_blank_barcodes_do_not_collide(self):
        self.client.post(reverse('pos:product_create'), self._payload(sku='A1'))
        self.client.post(reverse('pos:product_create'), self._payload(sku='A2', name='Second'))
        self.assertEqual(Product.objects.filter(barcode__isnull=True).count(), 2)

    def test_deactivating_keeps_the_product_for_history(self):
        product = make_product(sku='SKU-KEEP', stock=5)
        self.client.post(reverse('pos:product_toggle', args=[product.pk]))
        product.refresh_from_db()
        self.assertFalse(product.is_active)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_search_matches_name_sku_and_barcode(self):
        make_product(name='Rice 5kg', sku='SKU-R1', barcode='8801234', stock=1)
        self.assertEqual(Product.objects.search('rice').count(), 1)
        self.assertEqual(Product.objects.search('SKU-R1').count(), 1)
        self.assertEqual(Product.objects.search('8801234').count(), 1)
        self.assertEqual(Product.objects.search('nothing here').count(), 0)


class PurchaseTests(TestCase):
    def setUp(self):
        self.staff = make_user('store', Role.INVENTORY)
        self.product = make_product(stock=10)
        from pos.models import Supplier

        self.supplier = Supplier.objects.create(name='Dhaka Wholesale')

    def test_receiving_a_purchase_increases_stock(self):
        purchase = receive_purchase(
            supplier=self.supplier,
            lines=[(self.product.pk, 25, Decimal('500.00'))],
            user=self.staff,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 35)
        self.assertEqual(purchase.total_amount, Decimal('12500.00'))
        self.assertTrue(
            self.product.movements.filter(reason=StockMovement.Reason.PURCHASE).exists()
        )

    def test_a_purchase_needs_at_least_one_line(self):
        with self.assertRaises(BusinessRuleError):
            receive_purchase(supplier=self.supplier, lines=[], user=self.staff)

    def test_purchase_references_are_unique(self):
        first = receive_purchase(
            supplier=self.supplier, lines=[(self.product.pk, 1, Decimal('1'))], user=self.staff
        )
        second = receive_purchase(
            supplier=self.supplier, lines=[(self.product.pk, 1, Decimal('1'))], user=self.staff
        )
        self.assertNotEqual(first.reference, second.reference)
        self.assertEqual(Purchase.objects.count(), 2)
