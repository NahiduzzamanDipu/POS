"""Role-based access control (BR-002, BRL-11).

Capabilities are named strings granted per role. Views declare the capability
they need; the sidebar is built from the same map so a user is never shown a
link they cannot open.
"""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from .models import Role

# Capability vocabulary -----------------------------------------------------
POS_SELL = 'pos.sell'
SALE_VIEW_OWN = 'sale.view_own'
SALE_VIEW_ALL = 'sale.view_all'
SALE_VOID = 'sale.void'
RETURN_PROCESS = 'return.process'
PRODUCT_VIEW = 'product.view'
PRODUCT_MANAGE = 'product.manage'
PRODUCT_PRICE = 'product.price'          # BRL-4: price changes are restricted
CATEGORY_MANAGE = 'category.manage'
INVENTORY_VIEW = 'inventory.view'
INVENTORY_ADJUST = 'inventory.adjust'
SUPPLIER_MANAGE = 'supplier.manage'
PURCHASE_MANAGE = 'purchase.manage'
CUSTOMER_VIEW = 'customer.view'
CUSTOMER_MANAGE = 'customer.manage'
DISCOUNT_MANAGE = 'discount.manage'
DISCOUNT_RESTRICTED = 'discount.restricted'   # BR-027
EMPLOYEE_MANAGE = 'employee.manage'
REPORT_VIEW = 'report.view'
SETTINGS_MANAGE = 'settings.manage'
ACTIVITY_VIEW = 'activity.view'

_MANAGER = {
    POS_SELL,
    SALE_VIEW_OWN,
    SALE_VIEW_ALL,
    SALE_VOID,
    RETURN_PROCESS,
    PRODUCT_VIEW,
    PRODUCT_MANAGE,
    PRODUCT_PRICE,
    CATEGORY_MANAGE,
    INVENTORY_VIEW,
    INVENTORY_ADJUST,
    SUPPLIER_MANAGE,
    PURCHASE_MANAGE,
    CUSTOMER_VIEW,
    CUSTOMER_MANAGE,
    DISCOUNT_MANAGE,
    DISCOUNT_RESTRICTED,
    REPORT_VIEW,
    ACTIVITY_VIEW,
}

_CASHIER = {
    POS_SELL,
    SALE_VIEW_OWN,
    RETURN_PROCESS,
    PRODUCT_VIEW,
    CUSTOMER_VIEW,
    CUSTOMER_MANAGE,
}

_INVENTORY = {
    PRODUCT_VIEW,
    PRODUCT_MANAGE,
    CATEGORY_MANAGE,
    INVENTORY_VIEW,
    INVENTORY_ADJUST,
    SUPPLIER_MANAGE,
    PURCHASE_MANAGE,
    REPORT_VIEW,
}

ROLE_CAPABILITIES = {
    Role.ADMIN: _MANAGER | {EMPLOYEE_MANAGE, SETTINGS_MANAGE},
    Role.MANAGER: _MANAGER | {EMPLOYEE_MANAGE},
    Role.CASHIER: _CASHIER,
    Role.INVENTORY: _INVENTORY,
}


def capabilities_for(user):
    if not user or not user.is_authenticated:
        return frozenset()
    if user.is_superuser:
        return frozenset().union(*ROLE_CAPABILITIES.values())
    return frozenset(ROLE_CAPABILITIES.get(user.role, frozenset()))


def user_can(user, capability):
    return capability in capabilities_for(user)


def require(capability):
    """View decorator enforcing a single capability."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path(), reverse('pos:login'))
            if not user_can(request.user, capability):
                raise PermissionDenied(
                    'Your role does not permit this action.'
                )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def deny(request, message):
    """Reject an action with a friendly message instead of a raw exception."""
    messages.error(request, message)
    raise PermissionDenied(message)


# Navigation ---------------------------------------------------------------
# The report centre. Daily/Monthly/Yearly are gone: one Sales Report with an
# inclusive From/To filter covers all three, so there is a single place to
# answer "how did we trade over this period?".
REPORT_LINKS = [
    ('Sales Report', 'pos:report_sales', 'i-chart'),
    ('Inventory Report', 'pos:report_inventory', 'i-layers'),
    ('Product Performance', 'pos:report_products', 'i-box'),
    ('Supplier Report', 'pos:report_suppliers', 'i-truck'),
    ('Cash Flow', 'pos:report_cashflow', 'i-wallet'),
    ('User Wise Collection', 'pos:report_collections', 'i-badge-user'),
    ('Customer Report', 'pos:report_customers', 'i-users'),
]

# (group, label, url name, capability, icon). Order matches the approved UI.
NAV_DEFINITION = [
    ('Main', 'Dashboard', 'pos:dashboard', None, 'i-dashboard'),
    ('Main', 'New Sale', 'pos:pos_terminal', POS_SELL, 'i-sale'),
    ('Catalogue', 'Products', 'pos:product_list', PRODUCT_VIEW, 'i-box'),
    ('Catalogue', 'Inventory', 'pos:inventory', INVENTORY_VIEW, 'i-layers'),
    ('Catalogue', 'Categories', 'pos:category_list', CATEGORY_MANAGE, 'i-tag'),
    ('Catalogue', 'Suppliers', 'pos:supplier_list', SUPPLIER_MANAGE, 'i-truck'),
    ('People', 'Customers', 'pos:customer_list', CUSTOMER_VIEW, 'i-users'),
    ('People', 'Employees', 'pos:employee_list', EMPLOYEE_MANAGE, 'i-badge-user'),
    ('Insights', 'Reports', 'pos:reports', REPORT_VIEW, 'i-chart'),
    ('Insights', 'Transactions', 'pos:sale_list', SALE_VIEW_OWN, 'i-receipt'),
    ('System', 'Settings', 'pos:settings', SETTINGS_MANAGE, 'i-settings'),
]


def navigation_for(user):
    """Sidebar entries this user may actually open.

    Returned flat, each carrying its ``group`` so the template can render the
    section headings with ``{% ifchanged %}`` without a second data shape.
    """
    caps = capabilities_for(user)
    items = []
    for group, label, url_name, capability, icon in NAV_DEFINITION:
        if capability is not None and capability not in caps:
            continue
        item = {
            'group': group,
            'label': label,
            'url': reverse(url_name),
            'url_name': url_name,
            'icon': icon,
        }
        if url_name == 'pos:reports':
            item['children'] = [
                {
                    'label': child_label,
                    'url': reverse(child_url),
                    'url_name': child_url,
                    'icon': child_icon,
                }
                for child_label, child_url, child_icon in REPORT_LINKS
            ]
        items.append(item)
    return items
