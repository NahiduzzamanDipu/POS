"""Report aggregation.

Everything here is computed by the database -- no queryset is pulled into
Python to be summed in a loop. Only non-void sales count towards revenue.
"""

from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, IntegerField, Sum, Value, When
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


def profit_rows(sales):
    """Per-product margin for the given sales, newest-selling first.

    Revenue is ``subtotal`` -- the line total after its discount -- so a
    discount given at the till reduces the margin it actually reduced.

    Cost comes from ``SaleItem.unit_cost``, the figure captured when the sale
    was made, NOT from the product's current ``cost_price``. Repricing a
    product must not rewrite the margin on sales already completed.

    Lines predating that snapshot have ``unit_cost = NULL``. They are reported
    separately as "cost unknown" rather than being valued at today's cost,
    which was never what the business paid. ``unknown_units`` and
    ``unknown_revenue`` carry that shortfall so the template can disclose it.
    """
    items = SaleItem.objects.filter(sale__in=sales)

    rows = (
        items
        .values('product_name')
        .annotate(
            units=Coalesce(Sum('quantity'), Value(0)),
            revenue=_money(Sum('subtotal')),
            # Cost only where it was captured; the ELSE 0 deliberately does not
            # guess a cost, and the paired units_costed says how much of the
            # quantity that total actually covers.
            cost=_money(Sum(
                Case(
                    When(unit_cost__isnull=False, then=F('unit_cost') * F('quantity')),
                    default=Value(ZERO),
                    output_field=DecimalField(max_digits=16, decimal_places=2),
                )
            )),
            units_costed=Coalesce(
                Sum(Case(When(unit_cost__isnull=False, then=F('quantity')),
                         default=Value(0), output_field=IntegerField())),
                Value(0),
            ),
            revenue_costed=_money(Sum(
                Case(
                    When(unit_cost__isnull=False, then=F('subtotal')),
                    default=Value(ZERO),
                    output_field=DecimalField(max_digits=16, decimal_places=2),
                )
            )),
        )
        .order_by('-units')
    )

    out = []
    for row in rows:
        units = row['units'] or 0
        costed = row['units_costed'] or 0
        # Margin is only meaningful over the part of the line we know the cost
        # of, so profit and margin are computed against revenue_costed.
        revenue_costed = row['revenue_costed']
        profit = revenue_costed - row['cost'] if costed else None
        margin = (
            (profit / revenue_costed * 100).quantize(Decimal('0.01'))
            if profit is not None and revenue_costed else None
        )
        row.update({
            'unknown_units': units - costed,
            'complete': costed == units and units > 0,
            'unit_cost': (row['cost'] / costed).quantize(Decimal('0.01')) if costed else None,
            'unit_price': (row['revenue'] / units).quantize(Decimal('0.01')) if units else ZERO,
            'profit': profit,
            'margin': margin,
        })
        out.append(row)
    return out


def profit_summary(rows):
    """Headline margin figures, counting only lines with a captured cost."""
    revenue = sum((r['revenue'] for r in rows), ZERO)
    revenue_costed = sum((r['revenue_costed'] for r in rows), ZERO)
    cost = sum((r['cost'] for r in rows), ZERO)
    units = sum((r['units'] or 0) for r in rows)
    units_costed = sum((r['units_costed'] or 0) for r in rows)
    profit = revenue_costed - cost

    return {
        'revenue': revenue,
        'revenue_costed': revenue_costed,
        # Revenue we cannot attribute a cost to. Shown in the UI so the gross
        # profit figure is never read as covering the whole period.
        'revenue_unknown': revenue - revenue_costed,
        'cost': cost,
        'profit': profit,
        # Guard against a zero-revenue range rather than dividing by it.
        'margin': (profit / revenue_costed * 100).quantize(Decimal('0.01'))
                  if revenue_costed else None,
        'units': units,
        'units_costed': units_costed,
        'units_unknown': units - units_costed,
        'products': len(rows),
        'complete': units_costed == units,
    }


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


# ---------------------------------------------------------------------------
# Report centre aggregations
#
# Added for the redesigned Reports section. Each returns plain rows built by
# the database, so the templates only format what they are handed.
# ---------------------------------------------------------------------------
def collection_rows(sales):
    """Per-employee collection: what each user rang up, and what they took.

    ``collected`` is cash tendered minus change handed back, so it is takings
    rather than the note a customer waved at the till.
    """
    rows = list(
        sales.values(
            'cashier_id',
            'cashier__first_name',
            'cashier__last_name',
            'cashier__employee_id',
            'cashier__role',
            'cashier__email',
        )
        .annotate(
            transactions=Count('id'),
            gross=_money(Sum('subtotal')),
            discount=_money(Sum('discount_amount')),
            tax=_money(Sum('tax_amount')),
            total=_money(Sum('total_amount')),
            paid=_money(Sum('amount_paid')),
            change=_money(Sum('change_due')),
            refunded=_money(Sum('refunded_amount')),
        )
        .order_by('-total')
    )

    item_counts = {
        row['sale__cashier_id']: row['units']
        for row in SaleItem.objects.filter(sale__in=sales)
        .values('sale__cashier_id')
        .annotate(units=Coalesce(Sum('quantity'), Value(0)))
    }

    for row in rows:
        first = (row['cashier__first_name'] or '').strip()
        last = (row['cashier__last_name'] or '').strip()
        row['name'] = f'{first} {last}'.strip() or (row['cashier__employee_id'] or 'Unknown')
        row['employee_id'] = row['cashier__employee_id'] or '-'
        row['email'] = row['cashier__email'] or ''
        row['role'] = row['cashier__role'] or ''
        row['items'] = item_counts.get(row['cashier_id'], 0)
        row['collected'] = row['paid'] - row['change']
        row['net'] = row['total'] - row['refunded']
        row['average'] = (
            (row['total'] / row['transactions']).quantize(Decimal('0.01'))
            if row['transactions'] else ZERO
        )
    return rows


def collection_totals(rows):
    keys = ['transactions', 'items', 'gross', 'discount', 'tax', 'total',
            'collected', 'refunded', 'net']
    totals = {k: 0 if k in ('transactions', 'items') else ZERO for k in keys}
    for row in rows:
        for key in keys:
            totals[key] += row[key]
    totals['people'] = len(rows)
    return totals


def supplier_rows(start, end):
    """Per-supplier view: what they stock us with, and how it trades.

    Three grouped queries rather than one per supplier -- catalogue and stock,
    purchases received in the window, and sales of their products in it.
    """
    from django.db.models import DecimalField as _Dec
    from django.db.models import ExpressionWrapper, F

    from .models import Product, Purchase, Supplier

    stock_value = ExpressionWrapper(
        F('cost_price') * F('stock_quantity'),
        output_field=_Dec(max_digits=16, decimal_places=2),
    )

    catalogue = {
        row['supplier_id']: row
        for row in Product.objects.active()
        .exclude(supplier__isnull=True)
        .values('supplier_id')
        .annotate(
            products=Count('id'),
            units=Coalesce(Sum('stock_quantity'), Value(0)),
            stock_value=_money(Sum(stock_value)),
        )
    }

    purchases = {
        row['supplier_id']: row
        for row in Purchase.objects.filter(
            status=Purchase.Status.RECEIVED,
            purchase_date__gte=start,
            purchase_date__lte=end,
        )
        .values('supplier_id')
        .annotate(orders=Count('id'), spend=_money(Sum('total_amount')))
    }

    sold = {
        row['product__supplier_id']: row
        for row in SaleItem.objects.filter(
            sale__in=sales_between(start, end), product__supplier__isnull=False
        )
        .values('product__supplier_id')
        .annotate(
            sold_units=Coalesce(Sum('quantity'), Value(0)),
            revenue=_money(Sum('subtotal')),
        )
    }

    rows = []
    for supplier in Supplier.objects.all():
        cat = catalogue.get(supplier.pk, {})
        buy = purchases.get(supplier.pk, {})
        sell = sold.get(supplier.pk, {})
        rows.append({
            'supplier': supplier,
            'products': cat.get('products', 0),
            'units': cat.get('units', 0),
            'stock_value': cat.get('stock_value', ZERO),
            'orders': buy.get('orders', 0),
            'spend': buy.get('spend', ZERO),
            'sold_units': sell.get('sold_units', 0),
            'revenue': sell.get('revenue', ZERO),
        })
    rows.sort(key=lambda r: (r['revenue'], r['stock_value']), reverse=True)
    return rows


def supplier_totals(rows):
    keys = ['products', 'units', 'stock_value', 'orders', 'spend', 'sold_units', 'revenue']
    totals = {
        key: 0 if key in ('products', 'units', 'orders', 'sold_units') else ZERO
        for key in keys
    }
    for row in rows:
        for key in keys:
            totals[key] += row[key]
    totals['suppliers'] = len(rows)
    totals['active'] = sum(1 for row in rows if row['supplier'].is_active)
    return totals


def cash_flow(start, end):
    """Money in and money out across an inclusive date range.

    In  -- what the till actually took (tendered, less change handed back).
    Out -- refunds paid to customers, plus stock purchases received.
    """
    from .models import Purchase

    sales = sales_between(start, end)
    summary = sales_summary(sales)

    by_method = [
        {
            'method': row['payment_method'],
            'count': row['count'],
            'amount': row['amount'],
            'collected': row['collected'],
        }
        for row in sales.values('payment_method')
        .annotate(
            count=Count('id'),
            amount=_money(Sum('total_amount')),
            collected=_money(Sum('amount_paid')) - _money(Sum('change_due')),
        )
        .order_by('-amount')
    ]

    purchases = Purchase.objects.filter(
        status=Purchase.Status.RECEIVED,
        purchase_date__gte=start,
        purchase_date__lte=end,
    ).aggregate(count=Count('id'), amount=_money(Sum('total_amount')))

    refunds = returns_summary(start, end)

    inflow = summary['collected']
    outflow = refunds['refunded'] + purchases['amount']

    return {
        'sales': summary,
        'by_method': by_method,
        'purchases': purchases,
        'refunds': refunds,
        'inflow': inflow,
        'outflow': outflow,
        'net': inflow - outflow,
    }


def cash_flow_by_day(start, end):
    """Daily in/out series for the cash-flow chart, with no gaps."""
    from datetime import timedelta

    from .models import Purchase

    collected = {
        row['created_at__date']: row['collected']
        for row in sales_between(start, end)
        .values('created_at__date')
        .annotate(collected=_money(Sum('amount_paid')) - _money(Sum('change_due')))
    }
    buys = {
        row['purchase_date']: row['amount']
        for row in Purchase.objects.filter(
            status=Purchase.Status.RECEIVED,
            purchase_date__gte=start,
            purchase_date__lte=end,
        )
        .values('purchase_date')
        .annotate(amount=_money(Sum('total_amount')))
    }
    refunds = {
        row['created_at__date']: row['amount']
        for row in returns_between(start, end)
        .values('created_at__date')
        .annotate(amount=_money(Sum('refund_amount')))
    }

    rows = []
    day = start
    while day <= end:
        inflow = collected.get(day, ZERO)
        outflow = buys.get(day, ZERO) + refunds.get(day, ZERO)
        rows.append({
            'day': day,
            'inflow': inflow,
            'outflow': outflow,
            # Carried here rather than derived in the template: outflow is a
            # positive number, so a template doing inflow + outflow would
            # report the wrong sign, and templates should not be doing
            # arithmetic on money in the first place.
            'net': inflow - outflow,
            'moved': bool(inflow or outflow),
        })
        day += timedelta(days=1)
    return rows


def sales_trend(start, end, *, max_points=31):
    """Revenue per day across the range, gap-filled, for the trend chart.

    Long ranges are grouped by month instead so the axis stays readable.
    """
    from datetime import timedelta

    span = (end - start).days + 1
    if span > max_points:
        rows = sales_by_month(sales_between(start, end))
        return [
            {'label': row['month'].strftime('%b %y'), 'revenue': row['revenue']}
            for row in rows
        ]

    by_day = {
        row['created_at__date']: row['revenue']
        for row in sales_by_day(sales_between(start, end))
    }
    rows = []
    day = start
    while day <= end:
        rows.append({'label': day.strftime('%d %b'), 'revenue': by_day.get(day, ZERO)})
        day += timedelta(days=1)
    return rows
