"""Populate the database with a realistic demo dataset.

Idempotent: re-running updates the same records rather than duplicating them.
Sales are only generated when the database has none, so the command is safe to
re-run against a store that has started trading.

    python manage.py seed_demo
    python manage.py seed_demo --sales 60
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from pos.models import (
    Category,
    Customer,
    PaymentMethod,
    Product,
    Role,
    Sale,
    StockMovement,
    StoreSetting,
    Supplier,
    User,
)
from pos.customers import get_or_create_customer
from pos.services import adjust_stock, create_sale, money

DEMO_PASSWORD = 'Pos@12345'

USERS = [
    ('admin', "O'dell", 'tech', Role.ADMIN, 'EMP-001', 'Store Owner'),
    ('manager', 'Rafiq', 'Islam', Role.MANAGER, 'EMP-002', 'Branch Manager'),
    ('cashier', 'Sadia', 'Rahman', Role.CASHIER, 'EMP-003', 'Cashier'),
    ('cashier2', 'Tanvir', 'Hasan', Role.CASHIER, 'EMP-004', 'Cashier'),
    ('stock', 'Mizanur', 'Khan', Role.INVENTORY, 'EMP-005', 'Inventory Staff'),
]

CATEGORIES = [
    ('Grocery', 'Everyday food and household staples.'),
    ('Electronics', 'Small consumer electronics and accessories.'),
    ('Clothing', 'Garments and apparel.'),
    ('Cosmetics', 'Personal care and beauty products.'),
    ('Stationery', 'Office and school supplies.'),
]

SUPPLIERS = [
    ('Dhaka Wholesale Ltd', 'Kamal Uddin', '01711000001', 'sales@dhakawholesale.com'),
    ('Chittagong Traders', 'Nasrin Akter', '01711000002', 'info@ctgtraders.com'),
    ('Padma Distribution', 'Sohel Rana', '01711000003', 'contact@padmadist.com'),
]

# name, category, brand, cost, price, discount %, stock, min level, unit
PRODUCTS = [
    ('Rice 5kg', 'Grocery', 'Chashi', '520.00', '650.00', '5.00', 120, 20, 'pack'),
    ('Milk 1L', 'Grocery', 'Aarong', '85.00', '120.00', '0.00', 80, 15, 'ltr'),
    ('Coffee 200g', 'Grocery', 'Nescafe', '260.00', '350.00', '10.00', 45, 10, 'pack'),
    ('Sugar 1kg', 'Grocery', 'Fresh', '95.00', '135.00', '0.00', 60, 15, 'kg'),
    ('Soybean Oil 2L', 'Grocery', 'Rupchanda', '340.00', '420.00', '5.00', 38, 10, 'ltr'),
    ('Tea Bags 100pc', 'Grocery', 'Ispahani', '180.00', '250.00', '0.00', 8, 12, 'box'),
    ('Soap Bar', 'Cosmetics', 'Lux', '48.00', '80.00', '0.00', 150, 25, 'pc'),
    ('Shampoo 400ml', 'Cosmetics', 'Sunsilk', '310.00', '420.00', '8.00', 26, 10, 'pc'),
    ('Face Wash 100ml', 'Cosmetics', 'Himalaya', '210.00', '295.00', '0.00', 4, 8, 'pc'),
    ('USB Cable Type-C', 'Electronics', 'Anker', '250.00', '450.00', '10.00', 40, 10, 'pc'),
    ('Power Bank 10000mAh', 'Electronics', 'Xiaomi', '1450.00', '1990.00', '5.00', 12, 5, 'pc'),
    ('LED Bulb 12W', 'Electronics', 'Philips', '160.00', '245.00', '0.00', 70, 15, 'pc'),
    ('Earphones', 'Electronics', 'Realme', '520.00', '790.00', '0.00', 0, 6, 'pc'),
    ('Cotton T-Shirt', 'Clothing', 'Yellow', '390.00', '650.00', '15.00', 55, 12, 'pc'),
    ('Denim Jeans', 'Clothing', 'Ecstasy', '1150.00', '1750.00', '10.00', 22, 8, 'pc'),
    ('Ballpoint Pen', 'Stationery', 'Matador', '8.00', '15.00', '0.00', 400, 50, 'pc'),
    ('A4 Notebook', 'Stationery', 'Olympia', '65.00', '110.00', '5.00', 90, 20, 'pc'),
    ('Sticky Notes', 'Stationery', 'Deli', '55.00', '95.00', '0.00', 3, 10, 'pack'),
]

CUSTOMERS = [
    ('Rahim Uddin', '01811000001', 'rahim@example.com', 'Mirpur, Dhaka'),
    ('Karim Hossain', '01811000002', 'karim@example.com', 'Dhanmondi, Dhaka'),
    ('Fatema Begum', '01811000003', '', 'Uttara, Dhaka'),
    ('Jamal Sheikh', '01811000004', 'jamal@example.com', 'Gulshan, Dhaka'),
    ('Ayesha Siddika', '01811000005', '', 'Bashundhara, Dhaka'),
]


class Command(BaseCommand):
    help = 'Create demo users, catalogue, customers and sample transactions.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sales', type=int, default=40,
            help='Number of sample sales to generate (default 40).',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260825)

        store = self._store()
        users = self._users()
        categories = self._categories()
        suppliers = self._suppliers()
        products = self._products(categories, suppliers, users['stock'])
        self._customers()

        created = self._sales(options['sales'], users, products)

        self.stdout.write(self.style.SUCCESS(
            f'\nSeed complete for "{store.business_name}".'
        ))
        self.stdout.write(
            f'  {len(categories)} categories, {len(suppliers)} suppliers, '
            f'{len(products)} products, {Customer.objects.count()} customers, '
            f'{created} sales generated.'
        )
        self.stdout.write('\n  Sign in with any of these accounts:')
        for username, first, _last, role, _emp, _pos in USERS:
            self.stdout.write(f'    {username:<9} / {DEMO_PASSWORD}   ({role.label})')

    # ------------------------------------------------------------------
    def _store(self):
        store = StoreSetting.load()
        store.business_name = "O'dell Tech Shopping"
        store.address = '132 Mirpur Road, Dhanmondi, Dhaka 1205'
        store.phone = '+880 2 9876543'
        store.email = 'hello@odelltech.example'
        store.tax_rate = Decimal('5.00')
        store.existing_customer_discount_percent = Decimal('2.00')
        store.save()
        return store

    def _users(self):
        created = {}
        for username, first, last, role, employee_id, position in USERS:
            user, is_new = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first,
                    'last_name': last,
                    'role': role,
                    'employee_id': employee_id,
                    'position': position,
                    'email': f'{username}@odelltech.example',
                    'phone': '01700000000',
                },
            )
            if is_new:
                user.set_password(DEMO_PASSWORD)
            # Refresh the profile too, so re-seeding corrects an existing account.
            user.first_name = first
            user.last_name = last
            user.position = position
            user.employee_id = employee_id
            user.role = role
            user.is_staff = role == Role.ADMIN
            user.is_superuser = role == Role.ADMIN
            user.save()
            created[username] = user
        return created

    def _categories(self):
        return {
            name: Category.objects.get_or_create(
                name=name, defaults={'description': description}
            )[0]
            for name, description in CATEGORIES
        }

    def _suppliers(self):
        suppliers = []
        for name, contact, phone, email in SUPPLIERS:
            supplier, _ = Supplier.objects.get_or_create(
                name=name,
                defaults={
                    'contact_person': contact,
                    'phone': phone,
                    'email': email,
                    'address': 'Dhaka, Bangladesh',
                },
            )
            suppliers.append(supplier)
        return suppliers

    def _products(self, categories, suppliers, stock_user):
        products = []
        for index, (name, category, brand, cost, price, discount, stock, minimum, unit) in enumerate(
            PRODUCTS
        ):
            sku = f'SKU-{1000 + index}'
            product, is_new = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    'name': name,
                    'category': categories[category],
                    'supplier': suppliers[index % len(suppliers)],
                    'brand': brand,
                    'cost_price': Decimal(cost),
                    'selling_price': Decimal(price),
                    'discount_percent': Decimal(discount),
                    'min_stock_level': minimum,
                    'unit': unit,
                    'barcode': f'880{1000000 + index}',
                },
            )
            if not is_new and product.discount_percent != Decimal(discount):
                product.discount_percent = Decimal(discount)
                product.save(update_fields=['discount_percent', 'updated_at'])

            # Opening stock goes through the service so the movement is recorded.
            if is_new and stock:
                adjust_stock(
                    product, stock, StockMovement.Reason.OPENING,
                    user=stock_user, note='Demo opening stock', allow_negative=False,
                )
            products.append(product)
        return products

    def _customers(self):
        for _name, phone, _email, _address in CUSTOMERS:
            # Only the number is stored: the POS never collects personal details.
            get_or_create_customer(phone)

    def _sales(self, count, users, products):
        if Sale.objects.exists():
            self.stdout.write(
                self.style.WARNING('Sales already exist - skipping sample transactions.')
            )
            return 0

        cashiers = [users['cashier'], users['cashier2'], users['manager']]
        customers = list(Customer.objects.all())
        sellable = [p for p in products if p.stock_quantity > 3]
        methods = [PaymentMethod.CASH] * 5 + [PaymentMethod.CARD] * 3 + [PaymentMethod.MOBILE] * 2
        now = timezone.now()
        created = 0

        for index in range(count):
            basket = random.sample(sellable, random.randint(1, 4))
            items = []
            for product in basket:
                product.refresh_from_db()
                if product.stock_quantity < 2:
                    continue
                items.append((product.pk, random.randint(1, min(3, product.stock_quantity))))
            if not items:
                continue

            method = random.choice(methods)
            estimate = sum(
                Product.objects.get(pk=pid).selling_price * qty for pid, qty in items
            )
            # Most sales carry a customer number so the loyalty rule has data.
            number = random.choice(customers).phone if random.random() < 0.7 else ''
            try:
                sale = create_sale(
                    cashier=random.choice(cashiers),
                    items=items,
                    customer_number=number,
                    payment_method=method,
                    amount_paid=(
                        money(estimate * Decimal('1.3'))
                        if method == PaymentMethod.CASH else None
                    ),
                )
            except Exception as exc:  # a random basket may exhaust stock
                self.stdout.write(self.style.WARNING(f'  skipped a sale: {exc}'))
                continue

            # Spread the history over the last 45 days so reports have shape,
            # keeping a slice on today so the dashboard has something to show.
            days_ago = 0 if random.random() < 0.2 else random.randint(1, 45)
            hours_ago = random.randint(0, min(6, max(0, now.hour - 1))) if days_ago == 0                 else random.randint(0, 10)
            sale.created_at = now - timedelta(days=days_ago, hours=hours_ago)
            sale.save(update_fields=['created_at'])
            created += 1

        return created
