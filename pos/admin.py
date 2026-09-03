"""Django admin registration -- a maintenance back door, not the primary UI."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    ActivityLog,
    Category,
    Customer,
    Discount,
    Payment,
    Product,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    SaleReturn,
    SaleReturnItem,
    StockMovement,
    StoreSetting,
    Supplier,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'get_full_name', 'role', 'employee_id', 'is_active']
    list_filter = ['role', 'is_active', 'is_staff']
    readonly_fields = ['employee_id']   # generated server-side
    fieldsets = BaseUserAdmin.fieldsets + (
        ('POS profile', {'fields': ('role', 'employee_id', 'phone', 'position')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('POS profile', {'fields': ('role', 'phone', 'position')}),
    )


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    search_fields = ['name']


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'contact_person', 'phone', 'is_active']
    search_fields = ['name', 'phone', 'email']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'sku', 'category', 'selling_price', 'discount_percent',
                    'stock_quantity', 'is_active']
    list_filter = ['category', 'is_active', 'unit']
    search_fields = ['name', 'sku', 'barcode', 'brand']
    readonly_fields = ['stock_quantity']  # stock moves only through services


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['phone', 'name', 'is_active', 'created_at']
    search_fields = ['phone', 'name']


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ['name', 'discount_type', 'value', 'scope', 'is_active']
    list_filter = ['discount_type', 'scope', 'is_active']


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in SaleItem._meta.fields if f.name != 'id']


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['invoice_no', 'created_at', 'customer', 'cashier',
                    'total_amount', 'payment_method', 'status']
    list_filter = ['status', 'payment_method', 'created_at']
    search_fields = ['invoice_no', 'customer__name', 'customer__phone']
    inlines = [SaleItemInline]
    readonly_fields = ['invoice_no', 'subtotal', 'discount_amount', 'tax_amount',
                       'total_amount', 'amount_paid', 'change_due', 'refunded_amount']

    def has_delete_permission(self, request, obj=None):
        return False  # BRL-6: completed transactions are never deleted


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ['reference', 'supplier', 'purchase_date', 'total_amount', 'status']
    list_filter = ['status', 'purchase_date']
    inlines = [PurchaseItemInline]


class SaleReturnItemInline(admin.TabularInline):
    model = SaleReturnItem
    extra = 0


@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ['reference', 'sale', 'refund_amount', 'processed_by', 'created_at']
    inlines = [SaleReturnItemInline]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ['product', 'reason', 'quantity_change', 'balance_after',
                    'reference', 'created_at']
    list_filter = ['reason', 'created_at']
    search_fields = ['product__name', 'reference']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['sale', 'method', 'amount', 'status', 'paid_at']
    list_filter = ['method', 'status']


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'action', 'entity', 'entity_id']
    list_filter = ['action', 'created_at']
    search_fields = ['description']


@admin.register(StoreSetting)
class StoreSettingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not StoreSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
