"""Dashboard (BRS section 21).

Answers one question on sight: how is the business trading today? Summary
tiles, charts and today's transactions -- no catalogue management, which lives
under Products.

Everything shown is aggregated from the database; nothing here is a constant.
What a given user sees still depends on their capabilities, so a cashier gets
their own till rather than the whole shop.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from .. import charts, reporting
from ..models import Customer, Product, Sale, SaleItem
from ..permissions import (
    INVENTORY_VIEW,
    PRODUCT_VIEW,
    REPORT_VIEW,
    SALE_VIEW_ALL,
    capabilities_for,
)
from ..services import ZERO, low_stock_products, out_of_stock_products

TREND_DAYS = 14


@login_required
def dashboard(request):
    caps = capabilities_for(request.user)
    today = timezone.localdate()
    sees_everything = SALE_VIEW_ALL in caps

    def scoped(queryset):
        """A cashier's dashboard is their own till, not the whole shop."""
        return queryset if sees_everything else queryset.filter(cashier=request.user)

    sales_today = scoped(Sale.objects.completed().for_day(today))
    totals_today = sales_today.aggregate(
        revenue=Sum('total_amount'), transactions=Count('id')
    )
    revenue_today = totals_today['revenue'] or ZERO
    count_today = totals_today['transactions'] or 0

    yesterday = today - timedelta(days=1)
    revenue_yesterday = (
        scoped(Sale.objects.completed().for_day(yesterday))
        .aggregate(revenue=Sum('total_amount'))['revenue'] or ZERO
    )
    change = _percent_change(revenue_today, revenue_yesterday)

    items_today = SaleItem.objects.filter(sale__in=sales_today).aggregate(
        units=Sum('quantity')
    )['units'] or 0

    cards = [
        {
            'label': 'Today Sale',
            'value': revenue_today,
            'money': True,
            'icon': 'i-wallet',
            'tone': '',
            'meta': _change_label(change, 'vs yesterday'),
            'trend': change,
        },
        {
            'label': 'Transactions',
            'value': count_today,
            'icon': 'i-receipt',
            'tone': 'info',
            'meta': f'{items_today} item{"" if items_today == 1 else "s"} sold today',
        },
    ]

    if PRODUCT_VIEW in caps:
        stock_units = Product.objects.active().aggregate(units=Sum('stock_quantity'))
        cards.append({
            'label': 'Product Quantity',
            'value': stock_units['units'] or 0,
            'icon': 'i-box',
            'tone': 'neutral',
            'meta': f'{Product.objects.active().count()} active products',
            'url': 'pos:product_list',
        })

    low_count = out_count = 0
    if INVENTORY_VIEW in caps or PRODUCT_VIEW in caps:
        low_count = low_stock_products().count()
        out_count = out_of_stock_products().count()
        cards.append({
            'label': 'Low Stock',
            'value': low_count,
            'icon': 'i-alert-triangle',
            'tone': 'warning',
            'meta': 'At or below the minimum level',
            'url': 'pos:report_inventory',
            'query': '?status=low',
        })
        cards.append({
            'label': 'Out of Stock',
            'value': out_count,
            'icon': 'i-alert-circle',
            'tone': 'danger',
            'meta': 'Cannot be sold until restocked',
            'url': 'pos:report_inventory',
            'query': '?status=out',
        })
    else:
        cards.append({
            'label': 'Customers',
            'value': Customer.objects.filter(is_active=True).count(),
            'icon': 'i-users',
            'tone': 'neutral',
            'meta': 'Active customer numbers',
        })

    todays_sales = (
        sales_today.select_related('customer', 'cashier').order_by('-created_at')
    )

    context = {
        'page_title': 'Dashboard',
        'cards': cards[:5],
        'todays_sales': todays_sales[:10],
        'todays_count': count_today,
        'low_stock': low_stock_products().select_related('category')[:6],
        'low_count': low_count,
        'out_count': out_count,
        'sees_everything': sees_everything,
    }

    # --- charts, all from the same queries the tiles are built on ----------
    trend_start = today - timedelta(days=TREND_DAYS - 1)
    trend_sales = scoped(reporting.sales_between(trend_start, today))
    by_day = {row['created_at__date']: row['revenue'] for row in reporting.sales_by_day(trend_sales)}
    trend_rows = []
    day = trend_start
    while day <= today:
        trend_rows.append({'label': day.strftime('%d %b'), 'revenue': by_day.get(day, ZERO)})
        day += timedelta(days=1)

    context['trend_chart'] = charts.build_line_chart(
        trend_rows,
        label_key='label',
        series=[('revenue', 'Sales')],
        title=f'Sales, last {TREND_DAYS} days',
    )

    hourly = _hourly_rows(sales_today)
    context['today_chart'] = charts.build_grouped_chart(
        hourly,
        label_key='hour',
        series=[('revenue', 'Sales')],
        title="Today's sales by hour",
    )

    if REPORT_VIEW in caps:
        best = list(
            SaleItem.objects.filter(
                sale__status__in=['COMPLETED', 'PART_RETURN'],
                product__is_active=True,          # retired products are not shown
            )
            .values('product_name')
            .annotate(units=Sum('quantity'), revenue=Sum('subtotal'))
            .order_by('-units')[:5]
        )
        top_units = best[0]['units'] if best else 0
        for row in best:
            row['share'] = round(row['units'] * 100 / top_units, 1) if top_units else 0
        context['best_sellers'] = best
        context['best_seller'] = best[0] if best else None
        context['best_chart'] = charts.build_donut(
            best, label_key='product_name', value_key='units', centre_label='Units',
        )

    if INVENTORY_VIEW in caps or PRODUCT_VIEW in caps:
        healthy = max(Product.objects.active().count() - low_count - out_count, 0)
        context['stock_chart'] = charts.build_donut(
            [
                {'label': 'In stock', 'value': healthy},
                {'label': 'Low stock', 'value': low_count},
                {'label': 'Out of stock', 'value': out_count},
            ],
            label_key='label',
            value_key='value',
            centre_label='Products',
        )

    return render(request, 'pos/dashboard.html', context)


def _percent_change(current, previous):
    """Movement against the previous period, or None when there is no baseline."""
    if not previous:
        return None
    return round(float(current - previous) * 100 / float(previous), 1)


def _change_label(change, suffix):
    if change is None:
        return 'No sales yesterday to compare'
    direction = 'up' if change >= 0 else 'down'
    return f'{abs(change)}% {direction} {suffix}'


def _hourly_rows(sales):
    """Today's takings bucketed by trading hour, in one pass over the rows.

    Grouping by hour differs per database backend, and today's sale count is
    small by definition, so this is done in Python deliberately.
    """
    buckets = {}
    for created_at, total in sales.values_list('created_at', 'total_amount'):
        hour = timezone.localtime(created_at).hour
        buckets[hour] = buckets.get(hour, ZERO) + (total or ZERO)
    if not buckets:
        return []
    start, end = min(buckets), max(buckets)
    return [
        {'hour': f'{hour:02d}:00', 'revenue': buckets.get(hour, Decimal('0.00'))}
        for hour in range(start, end + 1)
    ]
