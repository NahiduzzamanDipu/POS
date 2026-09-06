from django.urls import path

from .views import auth, catalog, config, dashboard, inventory, people, reports, sales, terminal

app_name = 'pos'

urlpatterns = [
    # Authentication (BR-001)
    path('login/', auth.login_view, name='login'),
    path('logout/', auth.logout_view, name='logout'),
    path('change-password/', auth.change_password, name='change_password'),

    # Dashboard (section 21)
    path('', dashboard.dashboard, name='dashboard'),

    # POS terminal (BR-013, BR-014)
    path('pos/', terminal.pos_terminal, name='pos_terminal'),
    path('pos/checkout/', terminal.checkout, name='checkout'),
    path('pos/api/products/', terminal.product_lookup, name='product_lookup'),
    path('pos/api/quote/', terminal.cart_quote, name='cart_quote'),
    path('pos/api/customer/', terminal.customer_lookup, name='customer_lookup'),

    # Products & categories (BR-003 .. BR-006)
    path('products/', catalog.product_list, name='product_list'),
    path('products/new/', catalog.product_create, name='product_create'),
    path('products/bulk/', catalog.product_bulk_action, name='product_bulk_action'),
    path('products/import/', catalog.product_import, name='product_import'),
    path('products/import/confirm/', catalog.product_import_confirm,
         name='product_import_confirm'),
    path('products/import/template/', catalog.product_import_template,
         name='product_import_template'),
    path('products/import/result/', catalog.product_import_result,
         name='product_import_result'),
    path('products/import/errors/', catalog.product_import_errors,
         name='product_import_errors'),
    path('products/<int:pk>/', catalog.product_detail, name='product_detail'),
    path('products/<int:pk>/edit/', catalog.product_update, name='product_update'),
    path('products/<int:pk>/toggle/', catalog.product_toggle, name='product_toggle'),
    path('categories/', catalog.category_list, name='category_list'),
    path('categories/new/', catalog.category_create, name='category_create'),
    path('categories/<int:pk>/edit/', catalog.category_update, name='category_update'),
    path('categories/<int:pk>/toggle/', catalog.category_toggle, name='category_toggle'),

    # Suppliers & purchases (BR-011, BR-012)
    path('suppliers/', catalog.supplier_list, name='supplier_list'),
    path('suppliers/new/', catalog.supplier_create, name='supplier_create'),
    path('suppliers/<int:pk>/', catalog.supplier_detail, name='supplier_detail'),
    path('suppliers/<int:pk>/edit/', catalog.supplier_update, name='supplier_update'),
    path('purchases/', inventory.purchase_list, name='purchase_list'),
    path('purchases/new/', inventory.purchase_create, name='purchase_create'),
    path('purchases/<int:pk>/', inventory.purchase_detail, name='purchase_detail'),

    # Inventory (BR-007 .. BR-010)
    path('inventory/', inventory.inventory_overview, name='inventory'),
    path('inventory/adjust/', inventory.stock_adjust, name='stock_adjust'),
    path('inventory/movements/', inventory.stock_movements, name='stock_movements'),

    # Customers (BR-024, BR-025)
    path('customers/', people.customer_list, name='customer_list'),
    path('customers/new/', people.customer_create, name='customer_create'),
    path('customers/<int:pk>/', people.customer_detail, name='customer_detail'),
    path('customers/<int:pk>/edit/', people.customer_update, name='customer_update'),

    # Employees (BR-030, BR-031)
    path('employees/', people.employee_list, name='employee_list'),
    path('employees/new/', people.employee_create, name='employee_create'),
    path('employees/<int:pk>/edit/', people.employee_update, name='employee_update'),
    path('employees/<int:pk>/toggle/', people.employee_toggle, name='employee_toggle'),

    # Transactions, invoices, returns (BR-022, BR-023, BR-028)
    path('transactions/', sales.sale_list, name='sale_list'),
    path('transactions/<int:pk>/', sales.sale_detail, name='sale_detail'),
    path('transactions/<int:pk>/invoice/', sales.invoice, name='invoice'),
    path('transactions/<int:pk>/void/', sales.sale_void, name='sale_void'),
    path('returns/', sales.return_list, name='return_list'),
    path('returns/new/<int:sale_pk>/', sales.return_create, name='return_create'),
    path('returns/<int:pk>/', sales.return_detail, name='return_detail'),

    # Report centre (BR-032 .. BR-036)
    path('reports/', reports.report_index, name='reports'),
    path('reports/sales/', reports.sales_report, name='report_sales'),
    path('reports/inventory/', reports.inventory_report, name='report_inventory'),
    path('reports/products/', reports.product_report, name='report_products'),
    path('reports/suppliers/', reports.supplier_report, name='report_suppliers'),
    path('reports/cash-flow/', reports.cashflow_report, name='report_cashflow'),
    path('reports/collections/', reports.collection_report, name='report_collections'),
    path('reports/customer/', reports.customer_report, name='report_customers'),

    # Superseded by the Sales Report; these redirect into it with the matching
    # range applied, so existing links and bookmarks keep working.
    path('reports/daily/', reports.daily_report, name='report_daily'),
    path('reports/monthly/', reports.monthly_report, name='report_monthly'),
    path('reports/yearly/', reports.yearly_report, name='report_yearly'),

    # Settings & audit (modules 16, 17)
    path('settings/', config.settings_view, name='settings'),
    path('activity/', config.activity_log, name='activity_log'),
]
