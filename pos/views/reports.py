"""Reporting (BR-032 .. BR-036, use case 6). Every report supports CSV export."""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .. import charts, reporting
from ..customers import normalise_customer_number, number_variants
from ..forms_reports import (
    CustomerReportForm,
    DailyReportForm,
    MonthlyReportForm,
    YearlyReportForm,
)
from ..models import Category, Product, Sale, SaleItem, StockMovement
from ..permissions import REPORT_VIEW, require
from ._helpers import csv_response, paginate

ZERO = Decimal('0.00')


def _date_range(request, default_days=0):
    """Read ``from``/``to`` from the query string, defaulting to a recent window."""
    today = timezone.localdate()
    start = parse_date(request.GET.get('from', '') or '') or today - timedelta(days=default_days)
    end = parse_date(request.GET.get('to', '') or '') or today
    if start > end:
        start, end = end, start
    return start, end


def _sales_in(start, end):
    return (
        Sale.objects.completed()
        .filter(created_at__date__gte=start, created_at__date__lte=end)
    )


@require(REPORT_VIEW)
def report_index(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    return render(
        request,
        'pos/reports.html',
        {
            'page_title': 'Reports',
            'today': reporting.sales_summary(reporting.sales_between(today, today)),
            'month': reporting.sales_summary(reporting.sales_between(month_start, today)),
            'year': reporting.sales_summary(reporting.sales_between(year_start, today)),
            'chart': charts.build_grouped_chart(
                _month_rows(reporting.sales_between(year_start, today), today.year),
                label_key='month',
                series=[('revenue', 'Sales'), ('net', 'Net sales')],
                title=f'Sales by month, {today.year}',
            ),
        },
    )


def _month_rows(sales, year):
    """Twelve rows, one per month, so quiet months still appear on the chart."""
    from datetime import date as _date

    by_month = {row['month'].month: row for row in reporting.sales_by_month(sales)}
    rows = []
    for index in range(1, 13):
        row = by_month.get(index, {})
        revenue = row.get('revenue', ZERO)
        refunded = row.get('refunded', ZERO)
        rows.append({
            'month': _date(year, index, 1).strftime('%b'),
            'count': row.get('count', 0),
            'revenue': revenue,
            'discount': row.get('discount', ZERO),
            'refunded': refunded,
            'net': revenue - refunded,
        })
    return rows


@require(REPORT_VIEW)
def daily_report(request):
    """Sales for one day, with the payment-method breakdown."""
    form = DailyReportForm(request.GET or None)
    day = timezone.localdate()
    if request.GET and form.is_valid():
        day = form.cleaned_data['date']
    elif not request.GET:
        form = DailyReportForm(initial={'date': day})

    sales = reporting.sales_between(day, day)
    totals = reporting.sales_summary(sales)
    returns = reporting.returns_summary(day, day)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'daily-sales-{day}.csv',
            ['Invoice', 'Time', 'Customer Number', 'Cashier', 'Subtotal',
             'Product Discount', 'Customer Discount', 'Tax', 'Total', 'Payment'],
            [
                [
                    sale.invoice_no,
                    timezone.localtime(sale.created_at).strftime('%H:%M'),
                    sale.customer_label,
                    sale.cashier.display_name,
                    sale.subtotal,
                    sale.product_discount_amount,
                    sale.customer_discount_amount,
                    sale.tax_amount,
                    sale.total_amount,
                    sale.get_payment_method_display(),
                ]
                for sale in sales.select_related('cashier')
            ],
        )

    return render(
        request,
        'pos/report_daily.html',
        {
            'page_title': 'Daily Report',
            'form': form,
            'day': day,
            'totals': totals,
            'returns': returns,
            'by_method': reporting.payment_breakdown(sales),
            'top_products': reporting.top_products(sales, 8),
            'by_cashier': reporting.sales_by_cashier(sales),
            'chart': charts.build_grouped_chart(
                [
                    {'product': charts.shorten(row['product_name']),
                     'revenue': row['revenue'], 'units': row['units']}
                    for row in reporting.top_products(sales, 8)
                ],
                label_key='product',
                series=[('revenue', 'Revenue')],
                title=f'Revenue by product, {day:%d %b %Y}',
            ),
            'page_obj': paginate(request, sales.select_related('cashier'), 25),
        },
    )


@require(REPORT_VIEW)
def monthly_report(request):
    """BR-033: one month, with best sellers, categories and staff."""
    today = timezone.localdate()
    form = MonthlyReportForm(request.GET or None)
    month, year = today.month, today.year
    if request.GET and form.is_valid():
        month = form.cleaned_data['month']
        year = form.cleaned_data['year']
    elif not request.GET:
        form = MonthlyReportForm(initial={'month': month, 'year': year})

    start = date(year, month, 1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    sales = reporting.sales_between(start, end)
    totals = reporting.sales_summary(sales)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'monthly-sales-{year}-{month:02d}.csv',
            ['Date', 'Transactions', 'Revenue'],
            [[row['created_at__date'], row['count'], row['revenue']]
             for row in reporting.sales_by_day(sales)],
        )

    return render(
        request,
        'pos/report_monthly.html',
        {
            'page_title': 'Monthly Report',
            'form': form,
            'start': start,
            'end': end,
            'totals': totals,
            'returns': reporting.returns_summary(start, end),
            'by_method': reporting.payment_breakdown(sales),
            'by_day': reporting.sales_by_day(sales),
            'best_sellers': reporting.top_products(sales, 10),
            'by_category': reporting.sales_by_category(sales),
            'by_cashier': reporting.sales_by_cashier(sales),
            'chart': charts.build_grouped_chart(
                [
                    {'category': charts.shorten(row['product__category__name'] or 'Uncategorised'),
                     'revenue': row['revenue'], 'units': row['units']}
                    for row in reporting.sales_by_category(sales)
                ],
                label_key='category',
                series=[('revenue', 'Revenue')],
                title=f'Sales by category, {start:%B %Y}',
            ),
        },
    )


@require(REPORT_VIEW)
def yearly_report(request):
    """A full year, broken down month by month."""
    today = timezone.localdate()
    form = YearlyReportForm(request.GET or None)
    year = today.year
    if request.GET and form.is_valid():
        year = form.cleaned_data['year']
    elif not request.GET:
        form = YearlyReportForm(initial={'year': year})

    start = date(year, 1, 1)
    end = date(year, 12, 31)
    sales = reporting.sales_between(start, end)
    totals = reporting.sales_summary(sales)

    # Fill every month so the table has 12 rows even in a quiet year.
    by_month = {row['month'].month: row for row in reporting.sales_by_month(sales)}
    months = []
    for index in range(1, 13):
        row = by_month.get(index, {})
        revenue = row.get('revenue', ZERO)
        refunded = row.get('refunded', ZERO)
        months.append({
            'number': index,
            'name': date(year, index, 1).strftime('%B'),
            'count': row.get('count', 0),
            'revenue': revenue,
            'discount': row.get('discount', ZERO),
            'refunded': refunded,
            'net': revenue - refunded,
        })

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'yearly-sales-{year}.csv',
            ['Month', 'Transactions', 'Sales', 'Discount', 'Returns', 'Net Sales'],
            [[m['name'], m['count'], m['revenue'], m['discount'], m['refunded'], m['net']]
             for m in months],
        )

    return render(
        request,
        'pos/report_yearly.html',
        {
            'page_title': 'Yearly Report',
            'form': form,
            'year': year,
            'totals': totals,
            'returns': reporting.returns_summary(start, end),
            'months': months,
            'by_method': reporting.payment_breakdown(sales),
            'best_sellers': reporting.top_products(sales, 10),
            'chart': charts.build_grouped_chart(
                [{'month': m['name'][:3], 'revenue': m['revenue'], 'net': m['net']}
                 for m in months],
                label_key='month',
                series=[('revenue', 'Sales'), ('net', 'Net sales')],
                title=f'Sales by month, {year}',
            ),
        },
    )


@require(REPORT_VIEW)
def inventory_report(request):
    """BR-034: stock levels, movement and valuation."""
    products = Product.objects.active().select_related('category')

    status = request.GET.get('status', '')
    category_id = request.GET.get('category', '')
    term = request.GET.get('q', '').strip()

    if status == 'low':
        products = products.low_stock()
    elif status == 'out':
        products = products.out_of_stock()
    if category_id.isdigit():
        products = products.filter(category_id=int(category_id))
    if term:
        products = products.search(term)
    products = products.distinct()

    cost_value = ExpressionWrapper(
        F('cost_price') * F('stock_quantity'),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )
    retail_value = ExpressionWrapper(
        F('selling_price') * F('stock_quantity'),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )

    valuation = Product.objects.active().aggregate(
        cost_value=Sum(cost_value),
        retail_value=Sum(retail_value),
        units=Sum('stock_quantity'),
    )

    # One grouped query for the chart, rather than one per category.
    by_category = list(
        Product.objects.active()
        .values('category__name')
        .annotate(
            stock=Sum('stock_quantity'),
            cost_value=Sum(cost_value),
            retail_value=Sum(retail_value),
        )
        .order_by('-cost_value')
    )

    if request.GET.get('export') == 'csv':
        return csv_response(
            'inventory.csv',
            ['SKU', 'Product', 'Category', 'Stock', 'Min level', 'Cost', 'Price',
             'Stock value', 'Status'],
            [
                [p.sku, p.name, p.category.name, p.stock_quantity, p.min_stock_level,
                 p.cost_price, p.selling_price, p.stock_value, p.stock_status_label]
                for p in products
            ],
        )

    return render(
        request,
        'pos/report_inventory.html',
        {
            'page_title': 'Inventory Report',
            'page_obj': paginate(request, products, 25),
            'valuation': valuation,
            'by_category': by_category,
            'categories': Category.objects.filter(is_active=True),
            'selected_status': status,
            'selected_category': category_id,
            'search_term': term,
            'low_count': Product.objects.low_stock().count(),
            'out_count': Product.objects.out_of_stock().count(),
            # Archived products are retired from the catalogue, so their
            # movements do not belong in a current-inventory report either.
            'recent_movements': StockMovement.objects.filter(
                product__is_active=True
            ).select_related('product', 'created_by')[:15],
            'chart': charts.build_grouped_chart(
                [
                    {'category': charts.shorten(row['category__name'] or 'Uncategorised'),
                     'cost_value': row['cost_value'] or ZERO,
                     'retail_value': row['retail_value'] or ZERO}
                    for row in by_category
                ],
                label_key='category',
                series=[('cost_value', 'Stock value (cost)'),
                        ('retail_value', 'Stock value (retail)')],
                title='Inventory value by category',
            ),
        },
    )


@require(REPORT_VIEW)
def product_report(request):
    """BR-035: units, revenue, discount and transaction count per product."""
    start, end = _date_range(request, default_days=30)
    sales = reporting.sales_between(start, end)

    category_id = request.GET.get('category', '')
    # Retired products are excluded: they cannot be restocked or sold, so
    # ranking them alongside the live catalogue is misleading. Their money is
    # still counted in the sales reports, which are about revenue, not products.
    items = SaleItem.objects.filter(sale__in=sales, product__is_active=True)
    if category_id.isdigit():
        items = items.filter(product__category_id=int(category_id))

    rows = list(
        items.values('product_id', 'product_name')
        .annotate(
            units=Coalesce(Sum('quantity'), Value(0)),
            gross=Coalesce(
                Sum(ExpressionWrapper(
                    F('unit_price') * F('quantity'),
                    output_field=DecimalField(max_digits=16, decimal_places=2),
                )),
                Value(ZERO, output_field=DecimalField(max_digits=16, decimal_places=2)),
            ),
            discount=Coalesce(
                Sum('discount_amount'),
                Value(ZERO, output_field=DecimalField(max_digits=16, decimal_places=2)),
            ),
            revenue=Coalesce(
                Sum('subtotal'),
                Value(ZERO, output_field=DecimalField(max_digits=16, decimal_places=2)),
            ),
            transactions=Count('sale', distinct=True),
        )
        .order_by('-units')
    )

    sold_ids = {row['product_id'] for row in rows}
    never_sold = (
        Product.objects.active().exclude(pk__in=sold_ids).select_related('category')
    )

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'product-performance-{start}-to-{end}.csv',
            ['Product', 'Units sold', 'Transactions', 'Gross', 'Product discount',
             'Net revenue'],
            [
                [r['product_name'], r['units'], r['transactions'], r['gross'],
                 r['discount'], r['revenue']]
                for r in rows
            ],
        )

    return render(
        request,
        'pos/report_products.html',
        {
            'page_title': 'Product Performance',
            'start': start,
            'end': end,
            'rows': rows,
            'page_obj': paginate(request, rows, 25),
            'best': rows[:10],
            'worst': rows[-10:][::-1] if len(rows) > 10 else [],
            'never_sold': never_sold[:15],
            'by_category': reporting.sales_by_category(sales, active_only=True),
            'categories': Category.objects.filter(is_active=True),
            'selected_category': category_id,
            'totals': {
                'units': sum(r['units'] for r in rows),
                'revenue': sum((r['revenue'] for r in rows), ZERO),
                'discount': sum((r['discount'] for r in rows), ZERO),
                'products': len(rows),
            },
            'chart': charts.build_grouped_chart(
                [
                    {'product': charts.shorten(r['product_name']),
                     'revenue': r['revenue'], 'discount': r['discount']}
                    for r in rows[:10]
                ],
                label_key='product',
                series=[('revenue', 'Net revenue'), ('discount', 'Product discount')],
                title='Top products by revenue',
            ),
        },
    )


@require(REPORT_VIEW)
def customer_report(request):
    """BR-036 across an inclusive From/To range.

    Privacy: grouped and displayed by customer number only -- never by name.
    """
    form = CustomerReportForm(request.GET or None)
    today = timezone.localdate()
    start, end = today.replace(day=1), today
    number = ''

    if request.GET:
        if form.is_valid():
            start = form.cleaned_data['from_date']
            end = form.cleaned_data['to_date']
            number = normalise_customer_number(form.cleaned_data.get('customer_number'))
        else:
            return render(
                request,
                'pos/report_customers.html',
                {'page_title': 'Customer Report', 'form': form, 'rows': None},
            )
    else:
        form = CustomerReportForm(initial={'from_date': start, 'to_date': end})

    rows = reporting.customer_rows(start, end)
    if number:
        variants = set(number_variants(number))
        rows = [row for row in rows if row['customer_number'] in variants]
    totals = reporting.customer_totals(rows)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'customer-report-{start}-to-{end}.csv',
            ['Customer Number', 'Orders', 'Items', 'Total Shopping', 'Total Discount',
             'Tax', 'Total Paid', 'Total Due', 'Refunds', 'Net Purchase'],
            [
                [r['customer_number'], r['orders'], r['items'], r['total'], r['discount'],
                 r['tax'], r['collected'], r['due'], r['refunded'], r['net']]
                for r in rows
            ],
        )

    return render(
        request,
        'pos/report_customers.html',
        {
            'page_title': 'Customer Report',
            'form': form,
            'start': start,
            'end': end,
            'rows': rows,
            'page_obj': paginate(request, rows, 25),
            'totals': totals,
            'filtered_number': number,
            'chart': charts.build_grouped_chart(
                [{'number': r['customer_number'], 'total': r['total'],
                  'discount': r['discount']} for r in rows[:12]],
                label_key='number',
                series=[('total', 'Total shopping'), ('discount', 'Total discount')],
                title='Top customers by spend',
            ),
        },
    )
