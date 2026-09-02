"""POS sales screen (BR-013 .. BR-021, use case 2).

Workflow: search products -> cart -> enter customer number -> the server
detects an existing customer and applies the loyalty discount -> payment ->
invoice. The browser previews numbers; the server decides them.
"""

import uuid

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .. import customers as customer_service
from ..forms import CheckoutForm
from ..models import Product, StoreSetting
from ..permissions import POS_SELL, require
from ..services import BusinessRuleError, create_sale, price_cart

TXN_TOKEN_KEY = 'pos_txn_token'
COMPLETED_TOKENS_KEY = 'pos_completed_tokens'


def _issue_txn_token(request):
    """One-shot token that makes a double-submitted checkout idempotent."""
    token = uuid.uuid4().hex
    request.session[TXN_TOKEN_KEY] = token
    return token


@require(POS_SELL)
def pos_terminal(request):
    store = StoreSetting.load()
    products = Product.objects.active().select_related('category').order_by('name')
    term = request.GET.get('q', '').strip()
    if term:
        products = products.search(term)

    return render(
        request,
        'pos/terminal.html',
        {
            'page_title': 'New Sale',
            'products': products[:60],
            'search_term': term,
            'tax_rate': store.tax_rate,
            'loyalty_percent': store.existing_customer_discount_percent,
            'txn_token': _issue_txn_token(request),
            'payment_methods': [
                ('CASH', 'Cash'), ('CARD', 'Card'), ('MOBILE', 'Mobile'), ('BANK', 'Bank'),
            ],
        },
    )


@require(POS_SELL)
@require_GET
def product_lookup(request):
    """Type-ahead / barcode lookup for the sales screen (BR-005)."""
    term = request.GET.get('q', '').strip()
    queryset = Product.objects.active().select_related('category')
    if term:
        queryset = queryset.search(term)
    results = [
        {
            'id': p.id,
            'name': p.name,
            'sku': p.sku,
            'barcode': p.barcode or '',
            'category': p.category.name,
            'price': str(p.selling_price),
            'discount_percent': str(p.discount_percent),
            'final_price': str(p.final_price),
            'image_url': p.image_url,
            'initials': p.initials,
            'stock': p.stock_quantity,
            'unit': p.get_unit_display(),
            'status': p.stock_status,
        }
        for p in queryset.order_by('name')[:40]
    ]
    return JsonResponse({'results': results})


@require(POS_SELL)
@require_GET
def customer_lookup(request):
    """Tell the till whether this number is an existing customer.

    Informational only -- :func:`~pos.services.create_sale` decides again from
    the database when the sale is submitted.
    """
    result = customer_service.lookup(request.GET.get('number', ''))
    return JsonResponse(
        {
            'number': result['number'],
            'valid': result['valid'],
            'existing': result['existing'],
            'orders': result['orders'],
            'discount_percent': str(result['discount_percent']),
        }
    )


@require(POS_SELL)
@require_GET
def cart_quote(request):
    """Authoritative totals for the current cart -- the browser never decides money."""
    raw = request.GET.get('cart', '')
    pairs = []
    for chunk in raw.split(','):
        if not chunk.strip():
            continue
        try:
            product_id, quantity = chunk.split(':')
            pairs.append((int(product_id), int(quantity)))
        except ValueError:
            return JsonResponse({'error': 'Malformed cart.'}, status=400)

    store = StoreSetting.load()
    number = customer_service.normalise_customer_number(request.GET.get('customer_number', ''))
    existing = customer_service.is_existing_customer(number) if number else False
    loyalty = store.existing_customer_discount_percent if existing else '0.00'

    if not pairs:
        zero = '0.00'
        return JsonResponse(
            {
                'subtotal': zero, 'product_discount': zero, 'customer_discount': zero,
                'discount': zero, 'tax': zero, 'total': zero,
                'existing_customer': existing,
                'customer_discount_percent': str(loyalty),
                'lines': [],
            }
        )

    products = {p.pk: p for p in Product.objects.active().filter(pk__in=[p for p, _ in pairs])}
    items = [(products[pid], qty) for pid, qty in pairs if pid in products]
    if not items:
        return JsonResponse({'error': 'No valid products in the cart.'}, status=400)

    try:
        totals = price_cart(items, existing_customer=existing, store=store)
    except (BusinessRuleError, ArithmeticError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    return JsonResponse(
        {
            'subtotal': str(totals.subtotal),
            'product_discount': str(totals.product_discount),
            'customer_discount': str(totals.customer_discount),
            'customer_discount_percent': str(totals.customer_discount_percent),
            'discount': str(totals.discount_amount),
            'net_before_tax': str(totals.net_before_tax),
            'tax': str(totals.tax_amount),
            'tax_rate': str(totals.tax_rate),
            'total': str(totals.total),
            'existing_customer': totals.existing_customer,
            'lines': [
                {
                    'id': line.product.id,
                    'name': line.product.name,
                    'quantity': line.quantity,
                    'unit_price': str(line.unit_price),
                    'discount_percent': str(line.discount_percent),
                    'discount': str(line.discount_amount),
                    'subtotal': str(line.subtotal),
                    'stock': line.product.stock_quantity,
                }
                for line in totals.lines
            ],
        }
    )


@require(POS_SELL)
@require_POST
def checkout(request):
    """Complete the sale (BR-021) and hand the cashier the receipt."""
    form = CheckoutForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error[0])
        return redirect('pos:pos_terminal')

    data = form.cleaned_data
    token = data['txn_token']
    completed = request.session.get(COMPLETED_TOKENS_KEY, {})

    # A refresh or double-click replays the same token: show the original sale
    # rather than charging the customer twice.
    if token in completed:
        messages.info(request, 'That sale was already completed.')
        return redirect('pos:invoice', pk=completed[token])
    if request.session.get(TXN_TOKEN_KEY) != token:
        messages.error(request, 'This sale has expired. Please rebuild the cart.')
        return redirect('pos:pos_terminal')

    try:
        sale = create_sale(
            cashier=request.user,
            items=data['cart'],
            customer_number=data.get('customer_number', ''),
            payment_method=data['payment_method'],
            amount_paid=data.get('amount_paid'),
            note=data.get('note', ''),
            request=request,
        )
    except BusinessRuleError as exc:
        messages.error(request, str(exc))
        return redirect('pos:pos_terminal')

    completed[token] = sale.pk
    # Keep the map small; only recent tokens can realistically be replayed.
    request.session[COMPLETED_TOKENS_KEY] = dict(list(completed.items())[-10:])
    request.session.pop(TXN_TOKEN_KEY, None)

    messages.success(request, f'Sale {sale.invoice_no} completed.')
    return redirect('pos:invoice', pk=sale.pk)
