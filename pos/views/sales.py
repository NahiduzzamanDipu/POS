"""Transaction history, invoices, returns (BR-022, BR-023, BR-028, BR-029)."""

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from ..forms import ReturnForm
from ..models import Sale, SaleReturn
from ..permissions import (
    RETURN_PROCESS,
    SALE_VIEW_ALL,
    SALE_VIEW_OWN,
    SALE_VOID,
    require,
    user_can,
)
from ..services import BusinessRuleError, process_return, void_sale
from ._helpers import paginate, query_string


def _visible_sales(user):
    """BRL-11: cashiers see only their own transactions."""
    queryset = Sale.objects.select_related('customer', 'cashier')
    if user_can(user, SALE_VIEW_ALL):
        return queryset
    return queryset.filter(cashier=user)


@require(SALE_VIEW_OWN)
def sale_list(request):
    sales = _visible_sales(request.user)

    term = request.GET.get('q', '').strip()
    date_from = parse_date(request.GET.get('from', '') or '')
    date_to = parse_date(request.GET.get('to', '') or '')
    method = request.GET.get('method', '')
    status = request.GET.get('status', '')

    if term:
        sales = sales.filter(
            Q(invoice_no__icontains=term)
            | Q(customer__name__icontains=term)
            | Q(customer__phone__icontains=term)
        )
    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)
    if method:
        sales = sales.filter(payment_method=method)
    if status:
        sales = sales.filter(status=status)

    sales = sales.distinct()
    summary = sales.exclude(status=Sale.Status.VOID).aggregate(
        total=Sum('total_amount'), refunded=Sum('refunded_amount')
    )

    return render(
        request,
        'pos/sale_list.html',
        {
            'page_title': 'Transactions',
            'page_obj': paginate(request, sales),
            'search_term': term,
            'filters': request.GET,
            'querystring': query_string(request),
            'total_value': summary['total'] or 0,
            'refunded_value': summary['refunded'] or 0,
            'status_choices': Sale.Status.choices,
            'method_choices': Sale._meta.get_field('payment_method').choices,
        },
    )


@require(SALE_VIEW_OWN)
def sale_detail(request, pk):
    sale = get_object_or_404(_visible_sales(request.user), pk=pk)
    return render(
        request,
        'pos/sale_detail.html',
        {
            'page_title': sale.invoice_no,
            'sale': sale,
            'items': sale.items.select_related('product'),
            'returns': sale.returns.select_related('processed_by'),
            'can_void': user_can(request.user, SALE_VOID),
            'can_return': user_can(request.user, RETURN_PROCESS),
        },
    )


@require(SALE_VIEW_OWN)
def invoice(request, pk):
    """Printable receipt (BR-022)."""
    sale = get_object_or_404(_visible_sales(request.user), pk=pk)
    return render(
        request,
        'pos/invoice.html',
        {
            'page_title': f'Invoice {sale.invoice_no}',
            'sale': sale,
            'items': sale.items.all(),
            'hide_chrome': True,
        },
    )


@require(SALE_VOID)
@require_POST
def sale_void(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    try:
        void_sale(sale, request.user, request.POST.get('reason', ''), request=request)
        messages.success(request, f'{sale.invoice_no} was voided and stock restored.')
    except BusinessRuleError as exc:
        messages.error(request, str(exc))
    return redirect('pos:sale_detail', pk=sale.pk)


# ----------------------------------------------------------------- returns
@require(RETURN_PROCESS)
def return_list(request):
    returns = SaleReturn.objects.select_related('sale', 'processed_by')
    if not user_can(request.user, SALE_VIEW_ALL):
        returns = returns.filter(processed_by=request.user)
    return render(
        request,
        'pos/return_list.html',
        {
            'page_title': 'Returns',
            'page_obj': paginate(request, returns),
            'refund_total': returns.aggregate(total=Sum('refund_amount'))['total'] or 0,
        },
    )


@require(RETURN_PROCESS)
def return_create(request, sale_pk):
    """Use case 5. Quantities are re-validated against the invoice server-side."""
    sale = get_object_or_404(Sale.objects.select_related('customer', 'cashier'), pk=sale_pk)
    items = list(sale.items.select_related('product'))
    form = ReturnForm(request.POST or None, initial={'restock': True})

    if request.method == 'POST':
        lines = []
        for item in items:
            raw = request.POST.get(f'quantity_{item.pk}', '').strip()
            if raw and raw.isdigit() and int(raw) > 0:
                lines.append((item.pk, int(raw)))

        if not lines:
            messages.error(request, 'Enter a return quantity for at least one item.')
        elif form.is_valid():
            try:
                sale_return = process_return(
                    sale=sale,
                    lines=lines,
                    reason=form.cleaned_data['reason'],
                    user=request.user,
                    restock=form.cleaned_data.get('restock', False),
                    request=request,
                )
                messages.success(
                    request,
                    f'Return {sale_return.reference} recorded. '
                    f'Refund {sale_return.refund_amount}.',
                )
                return redirect('pos:return_detail', pk=sale_return.pk)
            except BusinessRuleError as exc:
                messages.error(request, str(exc))

    return render(
        request,
        'pos/return_form.html',
        {
            'page_title': f'Return against {sale.invoice_no}',
            'sale': sale,
            'items': items,
            'form': form,
        },
    )


@require(RETURN_PROCESS)
def return_detail(request, pk):
    sale_return = get_object_or_404(
        SaleReturn.objects.select_related('sale', 'processed_by'), pk=pk
    )
    return render(
        request,
        'pos/return_detail.html',
        {
            'page_title': sale_return.reference,
            'sale_return': sale_return,
            'items': sale_return.items.select_related('sale_item'),
        },
    )
