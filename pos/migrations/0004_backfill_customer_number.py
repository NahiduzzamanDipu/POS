"""Backfill the new sale fields from data that already exists.

Read-and-fill only: nothing is deleted, and no existing column is overwritten
with a worse value. Historical invoices keep identifying the right customer
even if the Customer row is edited later.
"""

from decimal import Decimal

from django.db import migrations

ZERO = Decimal('0.00')


def backfill(apps, schema_editor):
    Sale = apps.get_model('pos', 'Sale')

    # 1. Snapshot the customer number onto sales that have a customer.
    to_update = []
    for sale in Sale.objects.select_related('customer').filter(
        customer__isnull=False, customer_number=''
    ):
        sale.customer_number = sale.customer.phone or ''
        to_update.append(sale)
    if to_update:
        Sale.objects.bulk_update(to_update, ['customer_number'], batch_size=200)

    # 2. Existing discounts predate the product/customer split, so attribute the
    #    whole amount to product-level discounting rather than inventing a
    #    loyalty discount that was never actually given.
    Sale.objects.filter(discount_amount__gt=ZERO, product_discount_amount=ZERO).update(
        product_discount_amount=models_f('discount_amount')
    )


def models_f(name):
    from django.db.models import F

    return F(name)


def unbackfill(apps, schema_editor):
    """Reverse leaves the data in place -- nothing here is safe to delete."""


class Migration(migrations.Migration):

    dependencies = [
        ('pos', '0003_product_discount_percent_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
