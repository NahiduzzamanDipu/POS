"""Small helpers so each test reads as its scenario, not its setup."""

from decimal import Decimal

from pos.models import Category, Customer, Product, Role, StockMovement, StoreSetting, User
from pos.services import adjust_stock

PASSWORD = 'Test@12345'


def make_user(username='tester', role=Role.CASHIER, **extra):
    user = User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name=username.title(),
        role=role,
        **extra,
    )
    return user


def make_category(name='Grocery'):
    return Category.objects.get_or_create(name=name)[0]


def make_product(
    name='Rice 5kg',
    sku='SKU-TEST-1',
    price='650.00',
    cost='520.00',
    stock=50,
    min_level=5,
    category=None,
    discount_percent='0.00',
    **extra,
):
    product = Product.objects.create(
        name=name,
        sku=sku,
        category=category or make_category(),
        cost_price=Decimal(cost),
        selling_price=Decimal(price),
        discount_percent=Decimal(discount_percent),
        min_stock_level=min_level,
        **extra,
    )
    if stock:
        adjust_stock(
            product, stock, StockMovement.Reason.OPENING, allow_negative=False
        )
    return product


def make_customer(name='', phone='01811000001'):
    return Customer.objects.create(name=name, phone=phone)


def set_tax(rate='5.00', **extra):
    store = StoreSetting.load()
    store.tax_rate = Decimal(rate)
    for key, value in extra.items():
        setattr(store, key, value)
    store.save()
    return store


def store_name():
    """The configured business name, so branding tests are not hard-coded."""
    return StoreSetting.load().business_name
