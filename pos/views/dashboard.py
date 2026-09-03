"""Dashboard (BRS section 21). Metrics shown depend on the signed-in role."""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from ..models import Customer, Product, Sale, SaleItem
from ..permissions import (
    INVENTORY_VIEW,
    PRODUCT_VIEW,
    REPORT_VIEW,
    SALE_VIEW_ALL,
    capabilities_for,
)
from ..services import ZERO, low_stock_products, out_of_stock_products


@login_required
def dashboard(request):
    caps = capabilities_for(request.user)
    today = timezone.localdate()

    sales_today = Sale.objects.completed().for_day(today)
    if SALE_VIEW_ALL not in caps:
        # A cashier sees only their own till.
        sales_today = sales_today.filter(cashier=request.user)

    totals = sales_today.aggregate(revenue=Sum('total_amount'), transactions=Count('id'))

    cards = [
        {
            'label': "Today's Sales",
            'value': totals['revenue'] or ZERO,
            'money': True,
        },
        {'label': 'Transactions', 'value': totals['transactions'] or 0},
    ]

    if PRODUCT_VIEW in caps:
        cards.append({'label': 'Total Products', 'value': Product.objects.active().count()})
    if INVENTORY_VIEW in caps or PRODUCT_VIEW in caps:
        cards.append(
            {
                'label': 'Low Stock',
                'value': low_stock_products().count(),
                'url_name': 'pos:report_inventory',
            }
        )
        cards.append({'label': 'Out of Stock', 'value': out_of_stock_products().count()})
    cards.append({'label': 'Customers', 'value': Customer.objects.filter(is_active=True).count()})

    recent = (
        Sale.objects.completed()
        .select_related('customer', 'cashier')
        .order_by('-created_at')
    )
    if SALE_VIEW_ALL not in caps:
        recent = recent.filter(cashier=request.user)

    context = {
        'page_title': 'Dashboard',
        'cards': cards[:5],
        'recent_sales': recent[:8],
        'low_stock': low_stock_products()[:5],
    }

    if REPORT_VIEW in caps:
        context['best_sellers'] = (
            SaleItem.objects.filter(
                sale__status__in=['COMPLETED', 'PART_RETURN'],
                product__is_active=True,          # retired products are not shown
            )
            .values('product_name')
            .annotate(units=Sum('quantity'), revenue=Sum('subtotal'))
            .order_by('-units')[:5]
        )

    return render(request, 'pos/dashboard.html', context)
