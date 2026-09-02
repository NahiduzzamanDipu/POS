"""Products, categories, suppliers and discounts (BR-003 .. BR-006, BR-011, BR-026)."""

from decimal import Decimal

from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from ..forms import CategoryForm, ProductForm, ProductImportForm, SupplierForm
from ..imports import (
    COLUMNS,
    MAX_ROWS,
    REQUIRED,
    ImportError_,
    ParsedRow,
    commit_rows,
    parse_rows,
    read_table,
    summarise,
    template_csv,
)
from ..models import Category, Product, Supplier
from ..permissions import (
    CATEGORY_MANAGE,
    PRODUCT_MANAGE,
    PRODUCT_PRICE,
    PRODUCT_VIEW,
    SUPPLIER_MANAGE,
    require,
    user_can,
)
from ..services import BusinessRuleError, log_activity
from ._helpers import csv_response, paginate, query_string


# ---------------------------------------------------------------- products
def _filtered_products(params):
    """The product queryset for a set of list filters.

    Shared by the listing and the bulk action so that "apply to everything
    matching this filter" acts on exactly the rows the user was looking at.
    """
    term = (params.get('q') or '').strip()
    category_id = params.get('category') or ''
    status = params.get('status') or ''

    # Archived products are never listed unless they are asked for by name.
    # They only exist so that historical invoices keep resolving; they cannot
    # be sold, and they should not clutter the catalogue.
    if status == 'archived':
        products = Product.objects.filter(is_active=False)
    else:
        products = Product.objects.active()
    products = products.select_related('category', 'supplier')

    if term:
        products = products.search(term)
    if category_id.isdigit():
        products = products.filter(category_id=int(category_id))
    if status == 'low':
        products = products.low_stock()
    elif status == 'out':
        products = products.out_of_stock()
    return products.distinct()


@require(PRODUCT_VIEW)
def product_list(request):
    term = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '')
    status = request.GET.get('status', '')
    products = _filtered_products(request.GET)

    return render(
        request,
        'pos/product_list.html',
        {
            'page_title': 'Products',
            'page_obj': paginate(request, products),
            'match_count': products.count(),
            'categories': Category.objects.filter(is_active=True),
            'search_term': term,
            'selected_category': category_id,
            'selected_status': status,
            'archived_count': Product.objects.filter(is_active=False).count(),
            'querystring': query_string(request),
        },
    )


@require(PRODUCT_MANAGE)
@require_POST
def product_bulk_action(request):
    """Activate or deactivate many products at once.

    Two scopes: the rows the user ticked, or every product matching the
    filters currently applied to the list. The filtered scope is re-evaluated
    here from the same helper the listing uses, so the submitted page number
    or a stale tick cannot widen it.
    """
    action = request.POST.get('action', '')
    if action not in {'activate', 'deactivate'}:
        messages.error(request, 'Choose whether to activate or deactivate.')
        return redirect('pos:product_list')

    if request.POST.get('scope') == 'filtered':
        products = _filtered_products(request.POST)
        scope_label = 'matching the current filter'
    else:
        ids = [pk for pk in request.POST.getlist('selected') if pk.isdigit()]
        if not ids:
            messages.error(request, 'Select at least one product first.')
            return redirect(f"{reverse('pos:product_list')}{_filter_query(request.POST)}")
        products = Product.objects.filter(pk__in=ids)
        scope_label = 'selected'

    activate = action == 'activate'
    # Only touch rows that would actually change, so the count is honest.
    changing = products.filter(is_active=not activate)
    names = list(changing.values_list('name', flat=True)[:5])
    count = changing.count()

    if not count:
        messages.info(
            request,
            f'Nothing to do: every product {scope_label} is already '
            f'{"active" if activate else "archived"}.',
        )
    else:
        changing.update(is_active=activate)
        log_activity(
            request.user,
            'PRODUCTS_ACTIVATED' if activate else 'PRODUCTS_DEACTIVATED',
            'Product',
            description=f'{count} product(s) {scope_label}: ' + ', '.join(names),
            request=request,
        )
        messages.success(
            request,
            f'{count} product{"" if count == 1 else "s"} '
            f'{"activated" if activate else "archived"}.',
        )

    return redirect(f"{reverse('pos:product_list')}{_filter_query(request.POST)}")


def _filter_query(params):
    """Rebuild the list's filter query string so the user lands back where they were."""
    keep = {k: params.get(k) for k in ('q', 'category', 'status') if params.get(k)}
    return f'?{urlencode(keep)}' if keep else ''


@require(PRODUCT_VIEW)
def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.select_related('category', 'supplier'), pk=pk
    )
    return render(
        request,
        'pos/product_detail.html',
        {
            'page_title': product.name,
            'product': product,
            'movements': product.movements.select_related('created_by')[:20],
        },
    )


@require(PRODUCT_MANAGE)
def product_create(request):
    can_price = user_can(request.user, PRODUCT_PRICE)
    form = ProductForm(request.POST or None, can_edit_prices=can_price)
    if request.method == 'POST' and form.is_valid():
        product = form.save()
        log_activity(request.user, 'PRODUCT_CREATED', 'Product', product.pk,
                     product.name, request=request)
        messages.success(request, f'Product "{product.name}" was added.')
        return redirect('pos:product_list')
    return render(
        request,
        'pos/product_form.html',
        {'page_title': 'Add Product', 'form': form, 'is_new': True},
    )


@require(PRODUCT_MANAGE)
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    can_price = user_can(request.user, PRODUCT_PRICE)
    form = ProductForm(request.POST or None, instance=product, can_edit_prices=can_price)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_activity(request.user, 'PRODUCT_UPDATED', 'Product', product.pk,
                     product.name, request=request)
        messages.success(request, f'Product "{product.name}" was updated.')
        return redirect('pos:product_list')
    return render(
        request,
        'pos/product_form.html',
        {'page_title': f'Edit {product.name}', 'form': form, 'product': product},
    )


@require(PRODUCT_MANAGE)
@require_POST
def product_toggle(request, pk):
    """BR-004: products are deactivated, never deleted, so sales history survives."""
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active', 'updated_at'])
    state = 'activated' if product.is_active else 'deactivated'
    log_activity(request.user, f'PRODUCT_{state.upper()}', 'Product', product.pk, request=request)
    messages.success(request, f'"{product.name}" was {state}.')
    return redirect(request.POST.get('next') or 'pos:product_list')


# -------------------------------------------------------------- categories
@require(CATEGORY_MANAGE)
def category_list(request):
    categories = Category.objects.annotate(product_count=Count('products'))
    return render(
        request,
        'pos/category_list.html',
        {'page_title': 'Categories', 'page_obj': paginate(request, categories)},
    )


@require(CATEGORY_MANAGE)
def category_create(request):
    form = CategoryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        category = form.save()
        log_activity(request.user, 'CATEGORY_CREATED', 'Category', category.pk, request=request)
        messages.success(request, f'Category "{category.name}" was added.')
        return redirect('pos:category_list')
    return render(
        request,
        'pos/simple_form.html',
        {'page_title': 'Add Category', 'form': form, 'cancel_url': 'pos:category_list'},
    )


@require(CATEGORY_MANAGE)
def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_activity(request.user, 'CATEGORY_UPDATED', 'Category', category.pk, request=request)
        messages.success(request, f'Category "{category.name}" was updated.')
        return redirect('pos:category_list')
    return render(
        request,
        'pos/simple_form.html',
        {
            'page_title': f'Edit {category.name}',
            'form': form,
            'cancel_url': 'pos:category_list',
        },
    )


@require(CATEGORY_MANAGE)
@require_POST
def category_toggle(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if category.is_active and category.products.filter(is_active=True).exists():
        messages.error(
            request,
            f'"{category.name}" still has active products. Move or deactivate them first.',
        )
        return redirect('pos:category_list')
    category.is_active = not category.is_active
    category.save(update_fields=['is_active', 'updated_at'])
    messages.success(
        request, f'"{category.name}" was {"activated" if category.is_active else "deactivated"}.'
    )
    return redirect('pos:category_list')


# --------------------------------------------------------------- suppliers
@require(SUPPLIER_MANAGE)
def supplier_list(request):
    suppliers = Supplier.objects.annotate(product_count=Count('products'))
    term = request.GET.get('q', '').strip()
    if term:
        suppliers = suppliers.filter(name__icontains=term)
    return render(
        request,
        'pos/supplier_list.html',
        {
            'page_title': 'Suppliers',
            'page_obj': paginate(request, suppliers),
            'search_term': term,
            'querystring': query_string(request),
        },
    )


@require(SUPPLIER_MANAGE)
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    purchases = supplier.purchases.select_related('created_by')
    return render(
        request,
        'pos/supplier_detail.html',
        {
            'page_title': supplier.name,
            'supplier': supplier,
            'products': supplier.products.select_related('category')[:25],
            'purchases': purchases[:15],
            'purchase_total': purchases.aggregate(total=Sum('total_amount'))['total'] or 0,
        },
    )


@require(SUPPLIER_MANAGE)
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        supplier = form.save()
        log_activity(request.user, 'SUPPLIER_CREATED', 'Supplier', supplier.pk, request=request)
        messages.success(request, f'Supplier "{supplier.name}" was added.')
        return redirect('pos:supplier_list')
    return render(
        request,
        'pos/simple_form.html',
        {'page_title': 'Add Supplier', 'form': form, 'cancel_url': 'pos:supplier_list'},
    )


@require(SUPPLIER_MANAGE)
def supplier_update(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_activity(request.user, 'SUPPLIER_UPDATED', 'Supplier', supplier.pk, request=request)
        messages.success(request, f'Supplier "{supplier.name}" was updated.')
        return redirect('pos:supplier_list')
    return render(
        request,
        'pos/simple_form.html',
        {
            'page_title': f'Edit {supplier.name}',
            'form': form,
            'cancel_url': 'pos:supplier_list',
        },
    )


# ------------------------------------------------- Product Entry Automation
IMPORT_SESSION_KEY = 'product_import_pending'
IMPORT_RESULT_KEY = 'product_import_result'


@require(PRODUCT_MANAGE)
def product_import(request):
    """Upload a product file and preview exactly what will happen (step 1 of 2)."""
    form = ProductImportForm(request.POST or None, request.FILES or None)
    # A GET renders an unbound form, which picks up the field defaults
    # (create_missing on). A POST reflects exactly what was ticked.
    rows = summary = None
    options = {}

    if request.method == 'POST' and form.is_valid():
        options = {
            'create_missing': form.cleaned_data['create_missing'],
            'update_existing': form.cleaned_data['update_existing'],
        }
        try:
            header, table = read_table(form.cleaned_data['file'])
            rows = parse_rows(header, table, **options)
        except ImportError_ as exc:
            messages.error(request, str(exc))
            rows = None
        else:
            summary = summarise(rows)
            # Park the validated rows so the confirm step never re-reads the
            # upload -- what the user approved is exactly what gets written.
            request.session[IMPORT_SESSION_KEY] = {
                'options': options,
                'rows': [
                    {'number': row.number, 'data': _serialise(row.data)}
                    for row in rows
                    if row.is_valid
                ],
                'errors': [
                    {'number': row.number, 'name': row.name, 'sku': row.sku,
                     'message': row.message}
                    for row in rows
                    if not row.is_valid
                ],
                'total': summary['total'],
            }
            if not summary['valid']:
                blocked_by_lookup = sum(
                    1 for row in rows if any('does not exist' in e for e in row.errors)
                )
                if blocked_by_lookup and not options['create_missing']:
                    messages.error(
                        request,
                        f'None of the {summary["total"]} rows could be imported: '
                        f'{blocked_by_lookup} reference a category or supplier that does '
                        'not exist yet. Tick "Create categories and suppliers that do not '
                        'exist yet" and upload the file again.',
                    )
                else:
                    messages.error(
                        request, 'No rows can be imported. Fix the errors and try again.'
                    )

    return render(
        request,
        'pos/product_import.html',
        {
            'page_title': 'Product Entry Automation',
            'form': form,
            'rows': rows,
            'summary': summary,
            'options': options,
            'columns': COLUMNS,
            'required_columns': REQUIRED,
            'max_rows': MAX_ROWS,
        },
    )


# Model instances cannot go in the session, so they travel as ids.
_RELATION_FIELDS = ('category', 'supplier')


def _serialise(data):
    """Session-safe copy of a parsed row.

    Every key is carried across rather than a hand-written list: an earlier
    version enumerated the fields and silently dropped ``discount_percent``
    and ``image_url``, so products imported through this page lost their
    discount and photo. Copying whatever the parser produced means a new
    column can never go missing here again.
    """
    out = {}
    for key, value in data.items():
        if key in _RELATION_FIELDS:
            out[f'{key}_id'] = value.pk if value is not None else None
        elif isinstance(value, Decimal):
            out[key] = str(value)
        else:
            out[key] = value
    return out


def _deserialise(entry):
    """Rebuild a parsed row from the session, restoring types and relations."""
    data = dict(entry)
    for key in _RELATION_FIELDS:
        pk = data.pop(f'{key}_id', None)
        model = Category if key == 'category' else Supplier
        data[key] = model.objects.filter(pk=pk).first() if pk else None
    for key in ('cost_price', 'selling_price', 'discount_percent'):
        if data.get(key) is not None:
            data[key] = Decimal(data[key])
    return data


@require(PRODUCT_MANAGE)
@require_POST
def product_import_confirm(request):
    """Write the rows the user approved (step 2 of 2)."""
    pending = request.session.get(IMPORT_SESSION_KEY)
    if not pending or not pending.get('rows'):
        messages.error(request, 'That import expired. Please upload the file again.')
        return redirect('pos:product_import')

    rows = []
    for entry in pending['rows']:
        row = ParsedRow(entry['number'], {})
        row.data = _deserialise(entry['data'])
        rows.append(row)

    try:
        result = commit_rows(
            rows,
            request.user,
            create_missing=pending['options'].get('create_missing', False),
            request=request,
        )
    except (BusinessRuleError, IntegrityError) as exc:
        messages.error(request, f'The import was rolled back: {exc}')
        return redirect('pos:product_import')

    skipped = len(pending.get('errors', []))
    request.session[IMPORT_RESULT_KEY] = {
        'total': pending.get('total', len(rows) + skipped),
        'added': result['created'],
        'updated': result['updated'],
        'stocked': result['stocked'],
        'skipped': skipped,
        'failed': 0,
        'errors': pending.get('errors', []),
    }
    request.session.pop(IMPORT_SESSION_KEY, None)
    messages.success(
        request,
        f'Import complete: {result["created"]} added, {result["updated"]} updated, '
        f'{skipped} skipped.',
    )
    return redirect('pos:product_import_result')


@require(PRODUCT_MANAGE)
def product_import_template(request):
    """Download a starter file with the expected columns."""
    response = HttpResponse(template_csv(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="product-import-template.csv"'
    return response


@require(PRODUCT_MANAGE)
def product_import_result(request):
    """Summary of the last import, with the rows that were skipped."""
    result = request.session.get(IMPORT_RESULT_KEY)
    if not result:
        messages.info(request, 'No recent import to show.')
        return redirect('pos:product_import')
    return render(
        request,
        'pos/product_import_result.html',
        {'page_title': 'Import Result', 'result': result},
    )


@require(PRODUCT_MANAGE)
def product_import_errors(request):
    """Download the skipped rows so they can be corrected and re-imported."""
    pending = request.session.get(IMPORT_SESSION_KEY) or {}
    result = request.session.get(IMPORT_RESULT_KEY) or {}
    errors = pending.get('errors') or result.get('errors') or []
    if not errors:
        messages.info(request, 'There are no failed rows to download.')
        return redirect('pos:product_import')
    return csv_response(
        'product-import-errors.csv',
        ['Row', 'Product', 'SKU', 'Problem'],
        [[e['number'], e['name'], e['sku'], e['message']] for e in errors],
    )
