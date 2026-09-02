"""Inventory and purchases (BR-007 .. BR-012, use case 4)."""

from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import PurchaseForm, StockAdjustmentForm
from ..models import Product, Purchase, StockMovement
from ..permissions import INVENTORY_ADJUST, INVENTORY_VIEW, PURCHASE_MANAGE, require
from ..services import BusinessRuleError, adjust_stock, log_activity, receive_purchase
from ._helpers import paginate, query_string


@require(INVENTORY_VIEW)
def inventory_overview(request):
    products = Product.objects.active().select_related('category')

    term = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if term:
        products = products.search(term)
    if status == 'low':
        products = products.low_stock()
    elif status == 'out':
        products = products.out_of_stock()

    valuation = Product.objects.active().aggregate(
        value=Sum(
            ExpressionWrapper(
                F('cost_price') * F('stock_quantity'),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        ),
        units=Sum('stock_quantity'),
    )

    return render(
        request,
        'pos/inventory.html',
        {
            'page_title': 'Inventory',
            'page_obj': paginate(request, products.distinct()),
            'search_term': term,
            'selected_status': status,
            'querystring': query_string(request),
            'stock_value': valuation['value'] or Decimal('0.00'),
            'total_units': valuation['units'] or 0,
            'low_count': Product.objects.low_stock().count(),
            'out_count': Product.objects.out_of_stock().count(),
        },
    )


@require(INVENTORY_ADJUST)
def stock_adjust(request):
    form = StockAdjustmentForm(request.POST or None, initial={'product': request.GET.get('product')})
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=data['product'].pk)
                movement = adjust_stock(
                    product,
                    data['quantity_change'],
                    data['reason'],
                    user=request.user,
                    note=data['note'],
                )
            log_activity(
                request.user,
                'STOCK_ADJUSTED',
                'Product',
                product.pk,
                f'{data["quantity_change"]:+d} -> {movement.balance_after}',
                request=request,
            )
            messages.success(
                request,
                f'{product.name}: stock is now {movement.balance_after} '
                f'{product.get_unit_display().lower()}.',
            )
            return redirect('pos:inventory')
        except BusinessRuleError as exc:
            messages.error(request, str(exc))

    return render(
        request,
        'pos/stock_adjust.html',
        {'page_title': 'Stock Adjustment', 'form': form},
    )


@require(INVENTORY_VIEW)
def stock_movements(request):
    # Movements of archived products stay in the database for audit, but are
    # not listed here -- retired products are not shown anywhere in the app.
    movements = StockMovement.objects.filter(
        product__is_active=True
    ).select_related('product', 'created_by')
    reason = request.GET.get('reason', '')
    product_id = request.GET.get('product', '')
    if reason:
        movements = movements.filter(reason=reason)
    if product_id.isdigit():
        movements = movements.filter(product_id=int(product_id))

    return render(
        request,
        'pos/stock_movements.html',
        {
            'page_title': 'Stock Movements',
            'page_obj': paginate(request, movements, 30),
            'reasons': StockMovement.Reason.choices,
            'selected_reason': reason,
            'querystring': query_string(request),
        },
    )


# --------------------------------------------------------------- purchases
@require(PURCHASE_MANAGE)
def purchase_list(request):
    purchases = Purchase.objects.select_related('supplier', 'created_by')
    return render(
        request,
        'pos/purchase_list.html',
        {
            'page_title': 'Purchases',
            'page_obj': paginate(request, purchases),
            'total_value': purchases.aggregate(total=Sum('total_amount'))['total'] or 0,
        },
    )


@require(PURCHASE_MANAGE)
def purchase_create(request):
    """Record received stock. Lines arrive as parallel POST arrays."""
    form = PurchaseForm(request.POST or None)

    if request.method == 'POST':
        product_ids = request.POST.getlist('product')
        quantities = request.POST.getlist('quantity')
        costs = request.POST.getlist('unit_cost')
        lines = [
            (pid, qty, cost)
            for pid, qty, cost in zip(product_ids, quantities, costs)
            if pid and qty and qty.isdigit() and int(qty) > 0
        ]

        if form.is_valid() and lines:
            try:
                purchase = receive_purchase(
                    supplier=form.cleaned_data['supplier'],
                    lines=lines,
                    user=request.user,
                    purchase_date=form.cleaned_data['purchase_date'],
                    note=form.cleaned_data['note'],
                    request=request,
                )
                messages.success(
                    request,
                    f'Purchase {purchase.reference} recorded and stock updated.',
                )
                return redirect('pos:purchase_detail', pk=purchase.pk)
            except (BusinessRuleError, Product.DoesNotExist, ValueError) as exc:
                messages.error(request, str(exc))
        elif not lines:
            messages.error(request, 'Add at least one product line with a quantity.')

    return render(
        request,
        'pos/purchase_form.html',
        {
            'page_title': 'Record Purchase',
            'form': form,
            'products': Product.objects.active().select_related('category').order_by('name'),
        },
    )


@require(PURCHASE_MANAGE)
def purchase_detail(request, pk):
    purchase = get_object_or_404(
        Purchase.objects.select_related('supplier', 'created_by'), pk=pk
    )
    return render(
        request,
        'pos/purchase_detail.html',
        {
            'page_title': purchase.reference,
            'purchase': purchase,
            'items': purchase.items.select_related('product'),
        },
    )
