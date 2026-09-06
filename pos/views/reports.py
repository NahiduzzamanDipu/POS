"""The report centre (BR-032 .. BR-036, use case 6).

Seven reports, one shared shape: an inclusive From/To range, a headline strip,
a chart built from the same rows as the table, and a CSV export.

The old Daily / Monthly / Yearly reports are gone as separate destinations --
they were three views of one question. Their URLs still resolve and redirect
into the Sales Report with the matching range pre-filled, so old bookmarks and
links keep working.
"""

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
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import urlencode

from .. import charts, reporting
from ..customers import normalise_customer_number, number_variants
from ..forms_reports import CustomerReportForm, DateRangeForm
from ..models import Category, PaymentMethod, Product, Sale, SaleItem, StockMovement
from ..permissions import REPORT_VIEW, require
from ._helpers import csv_response, paginate

ZERO = Decimal('0.00')

PAYMENT_ICONS = {
    'CASH': 'i-cash',
    'CARD': 'i-card',
    'MOBILE': 'i-mobile',
    'BANK': 'i-bank',
}


def _date_range(request, *, default_start=None, default_days=None):
    """Read ``from``/``to`` from the query string as an inclusive range.

    Falls back to ``default_start`` (or ``default_days`` back from today), so
    a report always opens on something useful rather than on nothing.
    """
    today = timezone.localdate()
    if default_start is None:
        default_start = (
            today - timedelta(days=default_days) if default_days is not None
            else today.replace(day=1)
        )
    start = parse_date(request.GET.get('from', '') or '') or default_start
    end = parse_date(request.GET.get('to', '') or '') or today
    if start > end:
        start, end = end, start
    return start, end


def _range_form(request, start, end):
    """A bound-looking From/To form, whether or not the user submitted one."""
    if request.GET.get('from') or request.GET.get('to'):
        form = DateRangeForm(request.GET)
        form.is_valid()
        return form
    return DateRangeForm(initial={'from_date': start, 'to_date': end})


def _export_href(request):
    """The current query string with ``export=csv`` bolted on."""
    params = request.GET.copy()
    params['export'] = 'csv'
    return f'?{params.urlencode()}'


def _payment_rows(sales):
    """Payment-method breakdown, decorated for display."""
    labels = dict(PaymentMethod.choices)
    return [
        {
            'method': row['payment_method'],
            'label': labels.get(row['payment_method'], row['payment_method']),
            'icon': PAYMENT_ICONS.get(row['payment_method'], 'i-wallet'),
            'count': row['count'],
            'amount': row['amount'],
        }
        for row in reporting.payment_breakdown(sales)
    ]


# --------------------------------------------------------------- the centre
@require(REPORT_VIEW)
def report_index(request):
    """Landing page: headline trading figures plus a card per report."""
    today = timezone.localdate()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    year_sales = reporting.sales_between(year_start, today)

    return render(
        request,
        'pos/reports.html',
        {
            'page_title': 'Reports',
            'today': reporting.sales_summary(reporting.sales_between(today, today)),
            'month': reporting.sales_summary(reporting.sales_between(month_start, today)),
            'year': reporting.sales_summary(year_sales),
            'chart': charts.build_grouped_chart(
                _month_rows(year_sales, today.year),
                label_key='month',
                series=[('revenue', 'Sales'), ('net', 'Net sales')],
                title=f'Sales by month, {today.year}',
            ),
        },
    )


def _month_rows(sales, year):
    """Twelve rows, one per month, so quiet months still appear on the chart."""
    by_month = {row['month'].month: row for row in reporting.sales_by_month(sales)}
    rows = []
    for index in range(1, 13):
        row = by_month.get(index, {})
        revenue = row.get('revenue', ZERO)
        refunded = row.get('refunded', ZERO)
        rows.append({
            'month': date(year, index, 1).strftime('%b'),
            'count': row.get('count', 0),
            'revenue': revenue,
            'discount': row.get('discount', ZERO),
            'refunded': refunded,
            'net': revenue - refunded,
        })
    return rows


# ------------------------------------------------------------ A sales report
@require(REPORT_VIEW)
def sales_report(request):
    """BR-032/033: trading over any inclusive From/To range."""
    start, end = _date_range(request)
    form = _range_form(request, start, end)
    sales = reporting.sales_between(start, end)
    totals = reporting.sales_summary(sales)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'sales-{start}-to-{end}.csv',
            ['Invoice', 'Date', 'Time', 'Customer Number', 'Employee', 'Subtotal',
             'Product Discount', 'Customer Discount', 'Tax', 'Total', 'Payment', 'Status'],
            [
                [
                    sale.invoice_no,
                    timezone.localtime(sale.created_at).strftime('%Y-%m-%d'),
                    timezone.localtime(sale.created_at).strftime('%H:%M'),
                    sale.customer_label,
                    sale.cashier.display_name,
                    sale.subtotal,
                    sale.product_discount_amount,
                    sale.customer_discount_amount,
                    sale.tax_amount,
                    sale.total_amount,
                    sale.get_payment_method_display(),
                    sale.get_status_display(),
                ]
                for sale in sales.select_related('cashier')
            ],
        )

    transactions = totals['transactions'] or 0
    average = (totals['revenue'] / transactions).quantize(Decimal('0.01')) if transactions else ZERO

    return render(
        request,
        'pos/report_sales.html',
        {
            'page_title': 'Sales Report',
            'form': form,
            'start': start,
            'end': end,
            'days': (end - start).days + 1,
            'totals': totals,
            'average': average,
            'returns': reporting.returns_summary(start, end),
            'by_method': _payment_rows(sales),
            'top_products': reporting.top_products(sales, 8),
            'by_cashier': reporting.sales_by_cashier(sales),
            'export_href': _export_href(request),
            'chart': charts.build_line_chart(
                reporting.sales_trend(start, end),
                label_key='label',
                series=[('revenue', 'Sales')],
                title='Sales trend',
            ),
            'method_donut': charts.build_donut(
                _payment_rows(sales),
                label_key='label',
                value_key='amount',
                centre_label='Total',
            ),
            'page_obj': paginate(request, sales.select_related('cashier'), 25),
        },
    )


def _redirect_to_sales(start, end):
    query = urlencode({'from': start.isoformat(), 'to': end.isoformat()})
    return redirect(f"{reverse('pos:report_sales')}?{query}")


@require(REPORT_VIEW)
def daily_report(request):
    """Superseded by the Sales Report; kept so old links still land somewhere."""
    day = parse_date(request.GET.get('date', '') or '') or timezone.localdate()
    return _redirect_to_sales(day, day)


@require(REPORT_VIEW)
def monthly_report(request):
    today = timezone.localdate()
    try:
        month = int(request.GET.get('month') or today.month)
        year = int(request.GET.get('year') or today.year)
        start = date(year, month, 1)
    except (TypeError, ValueError):
        start = today.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return _redirect_to_sales(start, end)


@require(REPORT_VIEW)
def yearly_report(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year') or today.year)
    except (TypeError, ValueError):
        year = today.year
    return _redirect_to_sales(date(year, 1, 1), date(year, 12, 31))


# -------------------------------------------------------- B inventory report
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

    healthy = (
        Product.objects.active().count()
        - Product.objects.low_stock().count()
        - Product.objects.out_of_stock().count()
    )

    return render(
        request,
        'pos/report_inventory.html',
        {
            'page_title': 'Inventory Report',
            'page_obj': paginate(request, products, 25),
            'match_count': products.count(),
            'valuation': valuation,
            'by_category': by_category,
            'categories': Category.objects.filter(is_active=True),
            'selected_status': status,
            'selected_category': category_id,
            'search_term': term,
            'low_count': Product.objects.low_stock().count(),
            'out_count': Product.objects.out_of_stock().count(),
            'ok_count': max(healthy, 0),
            'export_href': _export_href(request),
            # Archived products are retired from the catalogue, so their
            # movements do not belong in a current-inventory report either.
            'recent_movements': StockMovement.objects.filter(
                product__is_active=True
            ).select_related('product', 'created_by')[:12],
            'status_donut': charts.build_donut(
                [
                    {'label': 'In stock', 'value': max(healthy, 0)},
                    {'label': 'Low stock', 'value': Product.objects.low_stock().count()},
                    {'label': 'Out of stock', 'value': Product.objects.out_of_stock().count()},
                ],
                label_key='label',
                value_key='value',
                centre_label='Products',
            ),
            'chart': charts.build_grouped_chart(
                [
                    {'category': charts.shorten(row['category__name'] or 'Uncategorised'),
                     'cost_value': row['cost_value'] or ZERO,
                     'retail_value': row['retail_value'] or ZERO}
                    for row in by_category[:12]
                ],
                label_key='category',
                series=[('cost_value', 'Stock value (cost)'),
                        ('retail_value', 'Stock value (retail)')],
                title='Inventory value by category',
            ),
        },
    )


# ------------------------------------------------------------------ G profits
@require(REPORT_VIEW)
def profit_report(request):
    """Margin over an inclusive From/To range, from what was actually sold.

    Profit is computed from ``SaleItem.unit_cost`` -- the cost captured when
    each sale was made -- never from the product's present ``cost_price`` and
    never from current stock. Repricing a product afterwards therefore cannot
    change the margin already booked.

    Lines recorded before that snapshot existed carry no cost. They are
    reported as "cost unknown" and left out of the profit figures rather than
    valued at a cost the business never paid, and the template states how much
    revenue that covers so the totals are not mistaken for the whole period.
    """
    start, end = _date_range(request)
    form = _range_form(request, start, end)
    sales = reporting.sales_between(start, end)
    rows = reporting.profit_rows(sales)
    totals = reporting.profit_summary(rows)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'profit-{start}-to-{end}.csv',
            ['Product', 'Units Sold', 'Units Costed', 'Avg Cost Price',
             'Avg Selling Price', 'Revenue', 'Total Cost', 'Profit',
             'Margin %', 'Cost Data'],
            [
                [
                    row['product_name'],
                    row['units'],
                    row['units_costed'],
                    row['unit_cost'] if row['unit_cost'] is not None else '',
                    row['unit_price'],
                    row['revenue'],
                    row['cost'] if row['units_costed'] else '',
                    row['profit'] if row['profit'] is not None else '',
                    row['margin'] if row['margin'] is not None else '',
                    'complete' if row['complete'] else
                    ('partial' if row['units_costed'] else 'unknown'),
                ]
                for row in rows
            ],
        )

    page = paginate(request, rows, per_page=50)

    return render(
        request,
        'pos/report_profit.html',
        {
            'page_title': 'Profits',
            'form': form,
            'start': start,
            'end': end,
            'days': (end - start).days + 1,
            'rows': page,
            'page_obj': page,
            'totals': totals,
            'transactions': sales.count(),
        },
    )


# ----------------------------------------------------- C product performance
@require(REPORT_VIEW)
def product_report(request):
    """BR-035: units, revenue, discount and transaction count per product."""
    start, end = _date_range(request, default_days=30)
    form = _range_form(request, start, end)
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
    never_sold = Product.objects.active().exclude(pk__in=sold_ids).select_related('category')

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

    # Share of the best seller, so the table can show a comparable bar.
    best_units = max((r['units'] for r in rows), default=0)
    for row in rows:
        row['share'] = round(row['units'] * 100 / best_units, 1) if best_units else 0

    return render(
        request,
        'pos/report_products.html',
        {
            'page_title': 'Product Performance',
            'form': form,
            'start': start,
            'end': end,
            'rows': rows,
            'page_obj': paginate(request, rows, 25),
            'best': rows[:10],
            'worst': rows[-10:][::-1] if len(rows) > 10 else [],
            'never_sold': never_sold[:12],
            'never_sold_count': never_sold.count(),
            'by_category': reporting.sales_by_category(sales, active_only=True),
            'categories': Category.objects.filter(is_active=True),
            'selected_category': category_id,
            'export_href': _export_href(request),
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


# ------------------------------------------------------- D supplier report
@require(REPORT_VIEW)
def supplier_report(request):
    """Who supplies the catalogue, what we buy from them, and how it sells."""
    start, end = _date_range(request)
    form = _range_form(request, start, end)

    rows = reporting.supplier_rows(start, end)
    term = request.GET.get('q', '').strip()
    if term:
        needle = term.lower()
        rows = [r for r in rows if needle in r['supplier'].name.lower()]
    if request.GET.get('status') == 'active':
        rows = [r for r in rows if r['supplier'].is_active]
    elif request.GET.get('status') == 'inactive':
        rows = [r for r in rows if not r['supplier'].is_active]

    totals = reporting.supplier_totals(rows)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'supplier-report-{start}-to-{end}.csv',
            ['Supplier', 'Contact', 'Phone', 'Products', 'Stock units', 'Stock value',
             'Purchase orders', 'Purchase value', 'Units sold', 'Revenue', 'Status'],
            [
                [r['supplier'].name, r['supplier'].contact_person, r['supplier'].phone,
                 r['products'], r['units'], r['stock_value'], r['orders'], r['spend'],
                 r['sold_units'], r['revenue'],
                 'Active' if r['supplier'].is_active else 'Inactive']
                for r in rows
            ],
        )

    best = max((r['revenue'] for r in rows), default=ZERO)
    for row in rows:
        row['share'] = round(float(row['revenue']) * 100 / float(best), 1) if best else 0

    return render(
        request,
        'pos/report_suppliers.html',
        {
            'page_title': 'Supplier Report',
            'form': form,
            'start': start,
            'end': end,
            'rows': rows,
            'page_obj': paginate(request, rows, 25),
            'totals': totals,
            'search_term': term,
            'selected_status': request.GET.get('status', ''),
            'export_href': _export_href(request),
            'chart': charts.build_grouped_chart(
                [
                    {'supplier': charts.shorten(r['supplier'].name),
                     'revenue': r['revenue'], 'stock_value': r['stock_value']}
                    for r in rows[:10]
                ],
                label_key='supplier',
                series=[('revenue', 'Revenue from their products'),
                        ('stock_value', 'Stock value held')],
                title='Suppliers by revenue',
            ),
        },
    )


# ------------------------------------------------------------- E cash flow
@require(REPORT_VIEW)
def cashflow_report(request):
    """Money in against money out, over an inclusive range."""
    start, end = _date_range(request)
    form = _range_form(request, start, end)

    flow = reporting.cash_flow(start, end)
    labels = dict(PaymentMethod.choices)
    for row in flow['by_method']:
        row['label'] = labels.get(row['method'], row['method'])
        row['icon'] = PAYMENT_ICONS.get(row['method'], 'i-wallet')

    daily = reporting.cash_flow_by_day(start, end)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'cash-flow-{start}-to-{end}.csv',
            ['Date', 'Money in', 'Money out', 'Net'],
            [[r['day'], r['inflow'], r['outflow'], r['inflow'] - r['outflow']]
             for r in daily],
        )

    return render(
        request,
        'pos/report_cashflow.html',
        {
            'page_title': 'Cash Flow',
            'form': form,
            'start': start,
            'end': end,
            'flow': flow,
            'daily': daily,
            'export_href': _export_href(request),
            'chart': charts.build_line_chart(
                [
                    {'label': r['day'].strftime('%d %b'),
                     'inflow': r['inflow'], 'outflow': r['outflow']}
                    for r in daily
                ],
                label_key='label',
                series=[('inflow', 'Money in'), ('outflow', 'Money out')],
                title='Daily cash movement',
            ),
            'method_donut': charts.build_donut(
                flow['by_method'],
                label_key='label',
                value_key='collected',
                centre_label='Collected',
            ),
        },
    )


# --------------------------------------------------- F user wise collection
@require(REPORT_VIEW)
def collection_report(request):
    """What each employee rang up and collected over the range."""
    start, end = _date_range(request)
    form = _range_form(request, start, end)

    sales = reporting.sales_between(start, end)
    rows = reporting.collection_rows(sales)

    role = request.GET.get('role', '')
    if role:
        rows = [r for r in rows if r['role'] == role]

    totals = reporting.collection_totals(rows)

    if request.GET.get('export') == 'csv':
        return csv_response(
            f'user-collection-{start}-to-{end}.csv',
            ['Employee ID', 'Employee', 'Role', 'Email', 'Transactions', 'Items',
             'Gross', 'Discount', 'Tax', 'Total', 'Collected', 'Refunds', 'Net'],
            [
                [r['employee_id'], r['name'], r['role'], r['email'], r['transactions'],
                 r['items'], r['gross'], r['discount'], r['tax'], r['total'],
                 r['collected'], r['refunded'], r['net']]
                for r in rows
            ],
        )

    best = max((r['total'] for r in rows), default=ZERO)
    for row in rows:
        row['share'] = round(float(row['total']) * 100 / float(best), 1) if best else 0

    from ..models import Role

    return render(
        request,
        'pos/report_collections.html',
        {
            'page_title': 'User Wise Collection',
            'form': form,
            'start': start,
            'end': end,
            'rows': rows,
            'page_obj': paginate(request, rows, 25),
            'totals': totals,
            'roles': Role.choices,
            'selected_role': role,
            'export_href': _export_href(request),
            'chart': charts.build_grouped_chart(
                [
                    {'user': charts.shorten(r['name']),
                     'total': r['total'], 'collected': r['collected']}
                    for r in rows[:10]
                ],
                label_key='user',
                series=[('total', 'Sales value'), ('collected', 'Collected')],
                title='Collection by employee',
            ),
            'share_donut': charts.build_donut(
                rows[:6], label_key='name', value_key='total', centre_label='Sales',
            ),
        },
    )


# ------------------------------------------------------- G customer report
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
            'export_href': _export_href(request),
            'chart': charts.build_grouped_chart(
                [{'number': r['customer_number'], 'total': r['total'],
                  'discount': r['discount']} for r in rows[:12]],
                label_key='number',
                series=[('total', 'Total shopping'), ('discount', 'Total discount')],
                title='Top customers by spend',
            ),
        },
    )
