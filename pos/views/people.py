"""Customers and employees (BR-024, BR-025, BR-030)."""

from django.contrib import messages
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import CustomerForm, EmployeeForm
from ..customers import number_variants
from ..models import Customer, Sale, User
from ..permissions import (
    CUSTOMER_MANAGE,
    CUSTOMER_VIEW,
    EMPLOYEE_MANAGE,
    require,
)
from ..services import log_activity
from ._helpers import paginate, query_string

ZERO = Decimal('0.00')


# --------------------------------------------------------------- customers
def _customer_totals(queryset):
    """Annotate customers with spend derived from completed sales only.

    Aggregated in the database (no N+1), and voided sales are excluded so the
    figures match what the reports show.
    """
    completed = Q(sales__status__in=[
        Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED, Sale.Status.RETURNED
    ])
    return queryset.annotate(
        purchase_count=Count('sales', filter=completed, distinct=True),
        purchase_total=Coalesce(
            Sum('sales__total_amount', filter=completed),
            Value(ZERO, output_field=DecimalField(max_digits=14, decimal_places=2)),
        ),
    )


@require(CUSTOMER_VIEW)
def customer_list(request):
    """Customer dashboard.

    Privacy rule: staff screens show the customer number and spend only --
    never the name, email or address.
    """
    customers = _customer_totals(Customer.objects.all())

    term = request.GET.get('q', '').strip()
    if term:
        variants = number_variants(term) or [term]
        lookup = Q(phone__icontains=term)
        for variant in variants:
            lookup |= Q(phone=variant)
        customers = customers.filter(lookup)

    only_buyers = request.GET.get('buyers', '') == '1'
    if only_buyers:
        customers = customers.filter(purchase_count__gt=0)

    customers = customers.order_by('-purchase_total', 'phone')
    summary = customers.aggregate(
        total_spend=Coalesce(
            Sum('purchase_total'),
            Value(ZERO, output_field=DecimalField(max_digits=16, decimal_places=2)),
        )
    )

    return render(
        request,
        'pos/customer_list.html',
        {
            'page_title': 'Customers',
            'page_obj': paginate(request, customers),
            'search_term': term,
            'only_buyers': only_buyers,
            'total_spend': summary['total_spend'],
            'buyer_count': Customer.objects.filter(sales__isnull=False).distinct().count(),
            'querystring': query_string(request),
        },
    )


@require(CUSTOMER_VIEW)
def customer_detail(request, pk):
    """BR-025: purchase history, identified by customer number."""
    customer = get_object_or_404(Customer, pk=pk)
    sales = (
        customer.sales.select_related('cashier')
        .exclude(status=Sale.Status.VOID)
        .order_by('-created_at')
    )
    totals = sales.aggregate(
        orders=Count('id'),
        spend=Coalesce(
            Sum('total_amount'),
            Value(ZERO, output_field=DecimalField(max_digits=14, decimal_places=2)),
        ),
        discount=Coalesce(
            Sum('discount_amount'),
            Value(ZERO, output_field=DecimalField(max_digits=14, decimal_places=2)),
        ),
    )
    return render(
        request,
        'pos/customer_detail.html',
        {
            'page_title': customer.phone,
            'customer': customer,
            'sales': sales[:25],
            'purchase_count': totals['orders'],
            'purchase_total': totals['spend'],
            'discount_total': totals['discount'],
        },
    )


@require(CUSTOMER_MANAGE)
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        customer = form.save()
        log_activity(request.user, 'CUSTOMER_CREATED', 'Customer', customer.pk, request=request)
        messages.success(request, f'Customer "{customer.name}" was added.')
        return redirect(request.POST.get('next') or 'pos:customer_list')
    return render(
        request,
        'pos/simple_form.html',
        {'page_title': 'Add Customer', 'form': form, 'cancel_url': 'pos:customer_list'},
    )


@require(CUSTOMER_MANAGE)
def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_activity(request.user, 'CUSTOMER_UPDATED', 'Customer', customer.pk, request=request)
        messages.success(request, f'Customer "{customer.name}" was updated.')
        return redirect('pos:customer_detail', pk=customer.pk)
    return render(
        request,
        'pos/simple_form.html',
        {
            'page_title': f'Edit {customer.name}',
            'form': form,
            'cancel_url': 'pos:customer_list',
        },
    )


# --------------------------------------------------------------- employees
@require(EMPLOYEE_MANAGE)
def employee_list(request):
    employees = User.objects.annotate(sale_count=Count('sales')).order_by(
        'first_name', 'username'
    )
    role = request.GET.get('role', '')
    status = request.GET.get('status', '')
    if role:
        employees = employees.filter(role=role)
    if status == 'active':
        employees = employees.filter(is_active=True)
    elif status == 'disabled':
        employees = employees.filter(is_active=False, last_login__isnull=False)
    elif status == 'pending':
        # Self-registered and never signed in: waiting for approval.
        employees = employees.filter(is_active=False, last_login__isnull=True)

    return render(
        request,
        'pos/employee_list.html',
        {
            'page_title': 'Employees',
            'page_obj': paginate(request, employees),
            'selected_role': role,
            'selected_status': status,
            'pending_count': User.objects.filter(
                is_active=False, last_login__isnull=True
            ).count(),
            'querystring': query_string(request),
        },
    )


@require(EMPLOYEE_MANAGE)
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        employee = form.save()
        log_activity(
            request.user, 'EMPLOYEE_CREATED', 'User', employee.pk,
            f'{employee.username} as {employee.get_role_display()}', request=request,
        )
        messages.success(request, f'Employee "{employee.display_name}" was created.')
        return redirect('pos:employee_list')
    return render(
        request,
        'pos/simple_form.html',
        {'page_title': 'Add Employee', 'form': form, 'cancel_url': 'pos:employee_list'},
    )


@require(EMPLOYEE_MANAGE)
def employee_update(request, pk):
    employee = get_object_or_404(User, pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_activity(request.user, 'EMPLOYEE_UPDATED', 'User', employee.pk, request=request)
        messages.success(request, f'Employee "{employee.display_name}" was updated.')
        return redirect('pos:employee_list')
    return render(
        request,
        'pos/simple_form.html',
        {
            'page_title': f'Edit {employee.display_name}',
            'form': form,
            'cancel_url': 'pos:employee_list',
        },
    )


@require(EMPLOYEE_MANAGE)
@require_POST
def employee_toggle(request, pk):
    employee = get_object_or_404(User, pk=pk)
    if employee == request.user:
        messages.error(request, 'You cannot deactivate your own account.')
        return redirect('pos:employee_list')
    employee.is_active = not employee.is_active
    employee.save(update_fields=['is_active'])
    state = 'activated' if employee.is_active else 'deactivated'
    log_activity(request.user, f'EMPLOYEE_{state.upper()}', 'User', employee.pk, request=request)
    messages.success(request, f'"{employee.display_name}" was {state}.')
    return redirect('pos:employee_list')
