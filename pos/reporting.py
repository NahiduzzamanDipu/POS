"""Report aggregation.

Everything here is computed by the database -- no queryset is pulled into
Python to be summed in a loop. Only non-void sales count towards revenue.
"""

from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth

from .models import Sale, SaleItem, SaleReturn

ZERO = Decimal('0.00')
COUNTED_STATUSES = [
    Sale.Status.COMPLETED,
    Sale.Status.PARTIALLY_RETURNED,
    Sale.Status.RETURNED,
]


def _money(expression):
    return Coalesce(
        expression, Value(ZERO, output_field=DecimalField(max_digits=16, decimal_places=2))
    )


def sales_between(start, end):
    """Non-void sales whose local date falls in ``[start, end]``, inclusive."""
    return Sale.objects.filter(
        created_at__date__gte=start, created_at__date__lte=end
    ).exclude(status=Sale.Status.VOID)


def sales_summary(sales):
    """Headline figures for a set of sales."""
    totals = sales.aggregate(
        transactions=Count('id'),
        gross=_money(Sum('subtotal')),
        product_discount=_money(Sum('product_discount_amount')),
        customer_discount=_money(Sum('customer_discount_amount')),
        discount=_money(Sum('discount_amount')),
        tax=_money(Sum('tax_amount')),
        revenue=_money(Sum('total_amount')),
        paid=_money(Sum('amount_paid')),
        change=_money(Sum('change_due')),
        refunded=_money(Sum('refunded_amount')),
    )
    items = SaleItem.objects.filter(sale__in=sales).aggregate(
        units=Coalesce(Sum('quantity'), Value(0)),
    )
    totals['items'] = items['units']
    totals['net'] = totals['revenue'] - totals['refunded']
    # Cash tendered above the total is handed back, so it is not takings.
    totals['collected'] = totals['paid'] - totals['change']
    totals['due'] = max(totals['revenue'] - totals['collected'], ZERO)
    return totals


def payment_breakdown(sales):
    return (
        sales.values('payment_method')
        .annotate(count=Count('id'), amount=_money(Sum('total_amount')))
        .order_by('-amount')
    )


def top_products(sales, limit=10, *, active_only=True):
    """Best sellers from the live catalogue.

    Archived products are excluded: they cannot be restocked or reordered, so
    listing them as "best sellers" is not actionable. Their revenue still
    counts in every money total, which comes from Sale rather than SaleItem.
    """
    items = SaleItem.objects.filter(sale__in=sales)
    if active_only:
        items = items.filter(product__is_active=True)
    return (
        items
        .values('product_name')
        .annotate(units=Coalesce(Sum('quantity'), Value(0)), revenue=_money(Sum('subtotal')))
        .order_by('-units')[:limit]
    )


def sales_by_category(sales, *, active_only=True):
    items = SaleItem.objects.filter(sale__in=sales)
    if active_only:
        items = items.filter(product__is_active=True)
    return (
        items
        .values('product__category__name')
        .annotate(units=Coalesce(Sum('quantity'), Value(0)), revenue=_money(Sum('subtotal')))
        .order_by('-revenue')
    )


def sales_by_cashier(sales):
    return (
        sales.values('cashier__username', 'cashier__first_name', 'cashier__last_name')
        .annotate(count=Count('id'), revenue=_money(Sum('total_amount')))
        .order_by('-revenue')
    )


def sales_by_day(sales):
    return (
        sales.values('created_at__date')
        .annotate(count=Count('id'), revenue=_money(Sum('total_amount')))
        .order_by('created_at__date')
    )


def sales_by_month(sales):
    return (
        sales.annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(
            count=Count('id'),
            revenue=_money(Sum('total_amount')),
            discount=_money(Sum('discount_amount')),
            refunded=_money(Sum('refunded_amount')),
        )
        .order_by('month')
    )


def returns_between(start, end):
    return SaleReturn.objects.filter(
        created_at__date__gte=start, created_at__date__lte=end
    )


def returns_summary(start, end):
    return returns_between(start, end).aggregate(
        count=Count('id'), refunded=_money(Sum('refund_amount'))
    )


def customer_rows(start, end):
    """Per-customer-number totals for the date range.

    Grouped on the number snapshotted onto the sale, so a sale is attributed to
    the number that was used at the till even if the Customer row changed.
    Names and emails are deliberately not selected.
    """
    sales = sales_between(start, end).exclude(customer_number='')

    rows = list(
        sales.values('customer_number')
        .annotate(
            orders=Count('id'),
            gross=_money(Sum('subtotal')),
            product_discount=_money(Sum('product_discount_amount')),
            customer_discount=_money(Sum('customer_discount_amount')),
            discount=_money(Sum('discount_amount')),
            tax=_money(Sum('tax_amount')),
            total=_money(Sum('total_amount')),
            paid=_money(Sum('amount_paid')),
            change=_money(Sum('change_due')),
            refunded=_money(Sum('refunded_amount')),
        )
        .order_by('-total')
    )

    # Item counts in one extra grouped query rather than one query per customer.
    item_counts = {
        row['sale__customer_number']: row['units']
        for row in SaleItem.objects.filter(sale__in=sales)
        .values('sale__customer_number')
        .annotate(units=Coalesce(Sum('quantity'), Value(0)))
    }

    for row in rows:
        row['items'] = item_counts.get(row['customer_number'], 0)
        row['collected'] = row['paid'] - row['change']
        row['due'] = max(row['total'] - row['collected'], ZERO)
        row['net'] = row['total'] - row['refunded']
    return rows


def customer_totals(rows):
    """Footer totals for the customer report."""
    keys = ['orders', 'items', 'gross', 'discount', 'total', 'refunded', 'net', 'due']
    totals = {key: 0 if key in ('orders', 'items') else ZERO for key in keys}
    for row in rows:
        for key in keys:
            totals[key] += row[key]
    totals['customers'] = len(rows)
    return totals
