"""Data model for the O'dell Tech Shopping POS system.

Entity set follows BRS section 24 (Data Requirements). Money is always
Decimal(12,2); stock is only ever mutated through ``pos.services.inventory``
so that BRL-8 / BRL-9 (every movement is recorded) cannot be bypassed.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Max
from django.utils import timezone

TWO_PLACES = Decimal('0.01')
ZERO = Decimal('0.00')

MONEY = {'max_digits': 12, 'decimal_places': 2}

phone_validator = RegexValidator(
    r'^[0-9+\-\s()]{6,20}$',
    'Enter a valid phone number (digits, spaces, +, -, and brackets only).',
)


class Role(models.TextChoices):
    """BR-002: the four access levels defined by the BRS."""

    ADMIN = 'ADMIN', 'Administrator'
    MANAGER = 'MANAGER', 'Manager'
    CASHIER = 'CASHIER', 'Cashier'
    INVENTORY = 'INVENTORY', 'Inventory Staff'


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# --------------------------------------------------------------------------
# Users / employees (BR-001, BR-002, BR-030)
# --------------------------------------------------------------------------
class User(AbstractUser):
    """Employee account. One table serves both auth and BR-030 employee data."""

    EMPLOYEE_ID_PREFIX = 'EMP-'
    EMPLOYEE_ID_WIDTH = 3

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CASHIER)
    employee_id = models.CharField(
        max_length=20, unique=True, null=True, blank=True, editable=False,
        help_text='Generated automatically; never entered by hand.',
    )
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    position = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ['first_name', 'username']

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def role_label(self):
        return self.get_role_display()

    def has_role(self, *roles):
        return self.role in roles

    @property
    def is_admin(self):
        return self.role == Role.ADMIN or self.is_superuser

    @classmethod
    def next_employee_id(cls):
        """The next free employee number, continuing the existing sequence.

        ``select_for_update`` locks the matching rows so two registrations
        racing each other cannot both read the same highest number. The unique
        constraint on the column is the final backstop.
        """
        prefix = cls.EMPLOYEE_ID_PREFIX
        with transaction.atomic():
            highest = 0
            rows = (
                cls.objects.select_for_update()
                .filter(employee_id__startswith=prefix)
                .values_list('employee_id', flat=True)
            )
            for value in rows:
                suffix = value[len(prefix):]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
            return f'{prefix}{highest + 1:0{cls.EMPLOYEE_ID_WIDTH}d}'

    def save(self, *args, **kwargs):
        """Assign an employee ID on first save. Existing IDs are never changed."""
        if not self.employee_id:
            for _attempt in range(5):
                self.employee_id = self.next_employee_id()
                try:
                    with transaction.atomic():
                        return super().save(*args, **kwargs)
                except IntegrityError:
                    # Another registration took that number; try the next one.
                    self.employee_id = None
                    if 'force_insert' in kwargs:
                        raise
            raise IntegrityError('Could not allocate a unique employee ID.')
        return super().save(*args, **kwargs)


# --------------------------------------------------------------------------
# Catalogue (BR-003 .. BR-006, BR-011)
# --------------------------------------------------------------------------
class Category(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name


class Supplier(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def low_stock(self):
        return self.active().filter(
            stock_quantity__gt=0, stock_quantity__lte=models.F('min_stock_level')
        )

    def out_of_stock(self):
        return self.active().filter(stock_quantity__lte=0)

    def search(self, term):
        term = (term or '').strip()
        if not term:
            return self
        lookup = (
            models.Q(name__icontains=term)
            | models.Q(sku__icontains=term)
            | models.Q(barcode__icontains=term)
            | models.Q(brand__icontains=term)
            | models.Q(category__name__icontains=term)
        )
        if term.isdigit():
            lookup |= models.Q(pk=int(term))
        return self.filter(lookup)


class Product(TimeStampedModel):
    """BR-003. ``stock_quantity`` is the single source of truth for on-hand stock."""

    class Unit(models.TextChoices):
        PIECE = 'pc', 'Piece'
        KILOGRAM = 'kg', 'Kilogram'
        GRAM = 'g', 'Gram'
        LITRE = 'ltr', 'Litre'
        PACK = 'pack', 'Pack'
        BOX = 'box', 'Box'
        DOZEN = 'dzn', 'Dozen'

    name = models.CharField(max_length=150, db_index=True)
    sku = models.CharField(max_length=40, unique=True)
    barcode = models.CharField(max_length=60, unique=True, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='products'
    )
    brand = models.CharField(max_length=80, blank=True)
    image_url = models.URLField(
        max_length=500,
        blank=True,
        help_text='Link to the product photo. Shown beside the name on every screen.',
    )
    cost_price = models.DecimalField(validators=[MinValueValidator(ZERO)], **MONEY)
    selling_price = models.DecimalField(validators=[MinValueValidator(ZERO)], **MONEY)
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal('100'))],
        help_text='Discount applied to this product on every sale, as a percentage.',
    )
    stock_quantity = models.IntegerField(default=0)
    min_stock_level = models.PositiveIntegerField(default=5)
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.PIECE)
    is_active = models.BooleanField(default=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['is_active', 'stock_quantity'])]
        constraints = [
            models.CheckConstraint(
                check=models.Q(selling_price__gte=0) & models.Q(cost_price__gte=0),
                name='product_prices_non_negative',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.sku})'

    @property
    def stock_status(self):
        if self.stock_quantity <= 0:
            return 'out'
        if self.stock_quantity <= self.min_stock_level:
            return 'low'
        return 'ok'

    @property
    def stock_status_label(self):
        return {'out': 'Out of Stock', 'low': 'Low Stock', 'ok': 'In Stock'}[self.stock_status]

    @property
    def has_image(self):
        return bool(self.image_url)

    @property
    def initials(self):
        """Fallback tile for a product with no photo."""
        parts = [word for word in self.name.split() if word[:1].isalnum()]
        return ''.join(word[0] for word in parts[:2]).upper() or '?'

    @property
    def discount_amount(self):
        """Money off one unit at this product's own discount rate."""
        return (self.selling_price * self.discount_percent / Decimal('100')).quantize(TWO_PLACES)

    @property
    def final_price(self):
        """Unit price the customer actually pays, before any customer discount."""
        return (self.selling_price - self.discount_amount).quantize(TWO_PLACES)

    @property
    def stock_value(self):
        return (self.cost_price * self.stock_quantity).quantize(TWO_PLACES)

    @property
    def is_sellable(self):
        return self.is_active and self.stock_quantity > 0


class Customer(TimeStampedModel):
    """Identified by phone number alone -- the POS never asks for a name."""

    name = models.CharField(max_length=120, db_index=True, blank=True, default='')
    phone = models.CharField(
        max_length=20, unique=True, validators=[phone_validator],
        verbose_name='customer number',
    )
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.phone})' if self.name else self.phone

    @property
    def display_label(self):
        """What staff screens show: the number, never the personal details."""
        return self.phone


# --------------------------------------------------------------------------
# Discounts (BR-026, BR-027)
# --------------------------------------------------------------------------
class Discount(TimeStampedModel):
    class Type(models.TextChoices):
        PERCENT = 'PERCENT', 'Percentage'
        FIXED = 'FIXED', 'Fixed amount'

    class Scope(models.TextChoices):
        ORDER = 'ORDER', 'Whole sale'
        PRODUCT = 'PRODUCT', 'Specific product'
        CATEGORY = 'CATEGORY', 'Specific category'

    name = models.CharField(max_length=100, unique=True)
    discount_type = models.CharField(max_length=10, choices=Type.choices, default=Type.PERCENT)
    value = models.DecimalField(validators=[MinValueValidator(ZERO)], **MONEY)
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.ORDER)
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, null=True, blank=True, related_name='discounts'
    )
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, null=True, blank=True, related_name='discounts'
    )
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    requires_approval = models.BooleanField(
        default=False, help_text='Only a Manager or Administrator may apply this discount.'
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_live(self):
        today = timezone.localdate()
        if not self.is_active:
            return False
        if self.starts_on and today < self.starts_on:
            return False
        if self.ends_on and today > self.ends_on:
            return False
        return True

    def amount_for(self, base):
        """Discount value for ``base``, never exceeding it."""
        base = Decimal(base)
        if self.discount_type == self.Type.PERCENT:
            amount = base * self.value / Decimal('100')
        else:
            amount = self.value
        return min(max(amount, ZERO), base).quantize(TWO_PLACES)


# --------------------------------------------------------------------------
# Purchases (BR-012)
# --------------------------------------------------------------------------
class Purchase(TimeStampedModel):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Received'
        CANCELLED = 'CANCELLED', 'Cancelled'

    reference = models.CharField(max_length=30, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchases')
    purchase_date = models.DateField(default=timezone.localdate)
    total_amount = models.DecimalField(default=ZERO, **MONEY)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchases'
    )

    class Meta:
        ordering = ['-purchase_date', '-id']

    def __str__(self):
        return self.reference

    @classmethod
    def next_reference(cls):
        last_id = cls.objects.aggregate(value=Max('id'))['value'] or 0
        return f'PO-{1000 + last_id + 1}'


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='purchase_items')
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField(validators=[MinValueValidator(ZERO)], **MONEY)
    subtotal = models.DecimalField(default=ZERO, **MONEY)

    def __str__(self):
        return f'{self.product} x{self.quantity}'

    def save(self, *args, **kwargs):
        self.subtotal = (self.unit_cost * self.quantity).quantize(TWO_PLACES)
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------
# Sales (BR-013 .. BR-023)
# --------------------------------------------------------------------------
class PaymentMethod(models.TextChoices):
    CASH = 'CASH', 'Cash'
    CARD = 'CARD', 'Card'
    MOBILE = 'MOBILE', 'Mobile'
    BANK = 'BANK', 'Bank'


class SaleQuerySet(models.QuerySet):
    def completed(self):
        return self.exclude(status=Sale.Status.VOID)

    def for_day(self, day):
        return self.filter(created_at__date=day)


class Sale(models.Model):
    """A completed transaction. BRL-6: sales are never deleted, only voided."""

    class Status(models.TextChoices):
        COMPLETED = 'COMPLETED', 'Completed'
        PARTIALLY_RETURNED = 'PART_RETURN', 'Partially returned'
        RETURNED = 'RETURNED', 'Returned'
        VOID = 'VOID', 'Void'

    invoice_no = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, null=True, blank=True, related_name='sales'
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sales'
    )
    customer_number = models.CharField(
        max_length=20, blank=True, db_index=True,
        help_text='Snapshot of the customer number, so old invoices stay correct.',
    )
    subtotal = models.DecimalField(default=ZERO, **MONEY)
    # discount_amount stays the combined total for backward compatibility;
    # the two components below are what the reports break out.
    product_discount_amount = models.DecimalField(default=ZERO, **MONEY)
    customer_discount_amount = models.DecimalField(default=ZERO, **MONEY)
    customer_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=ZERO
    )
    discount_amount = models.DecimalField(default=ZERO, **MONEY)
    tax_amount = models.DecimalField(default=ZERO, **MONEY)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(default=ZERO, **MONEY)
    amount_paid = models.DecimalField(default=ZERO, **MONEY)
    change_due = models.DecimalField(default=ZERO, **MONEY)
    refunded_amount = models.DecimalField(default=ZERO, **MONEY)
    payment_method = models.CharField(
        max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.COMPLETED)
    discount = models.ForeignKey(
        Discount, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales'
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='voided_sales',
    )

    objects = SaleQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['-created_at', 'cashier'])]

    def __str__(self):
        return self.invoice_no

    @property
    def customer_label(self):
        """Customer number for display. Never the name (privacy rule)."""
        return self.customer_number or (self.customer.phone if self.customer else 'Walk-in')

    @property
    def is_existing_customer_sale(self):
        return self.customer_discount_amount > ZERO

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def net_amount(self):
        return (self.total_amount - self.refunded_amount).quantize(TWO_PLACES)

    @property
    def is_returnable(self):
        return self.status in {self.Status.COMPLETED, self.Status.PARTIALLY_RETURNED}

    @classmethod
    def next_invoice_no(cls, prefix='INV'):
        last_id = cls.objects.aggregate(value=Max('id'))['value'] or 0
        return f'{prefix}-{1000 + last_id + 1}'


class SaleItem(models.Model):
    """Line item. Name/price are snapshotted so history survives product edits."""

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='sale_items')
    product_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
        help_text=(
            'What this unit cost the business, captured at the moment of sale. '
            'Profit reporting uses this rather than the current '
            'cost_price on the product, so editing a price later cannot '
            'rewrite past margins. '
            'NULL means the sale predates this field: those lines are reported '
            'as "cost unknown" and excluded from profit rather than being '
            'valued at a cost that was never paid.'
        ),
        **MONEY,
    )
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(default=ZERO, **MONEY)
    subtotal = models.DecimalField(default=ZERO, **MONEY)
    returned_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.product_name} x{self.quantity}'

    @property
    def line_total(self):
        return (self.unit_price * self.quantity - self.discount_amount).quantize(TWO_PLACES)

    @property
    def has_cost(self):
        """False for lines sold before unit_cost existed."""
        return self.unit_cost is not None

    @property
    def line_cost(self):
        """Cost of goods for this line, or None when it was never captured."""
        if self.unit_cost is None:
            return None
        return (self.unit_cost * self.quantity).quantize(TWO_PLACES)

    @property
    def line_profit(self):
        """Revenue after discount minus cost of goods, or None when unknown.

        Uses line_total, not unit_price * quantity, so a discount given at the
        till reduces the profit it actually reduced.
        """
        cost = self.line_cost
        if cost is None:
            return None
        return (self.line_total - cost).quantize(TWO_PLACES)

    @property
    def returnable_quantity(self):
        return self.quantity - self.returned_quantity

    @property
    def unit_refund_value(self):
        """Per-unit refund, net of this line's share of the sale-level discount."""
        if not self.quantity:
            return ZERO
        return (self.subtotal / self.quantity).quantize(TWO_PLACES)


class Payment(models.Model):
    """BR-019 / BR-021: a sale is complete only once payment is confirmed."""

    class Status(models.TextChoices):
        PAID = 'PAID', 'Paid'
        REFUNDED = 'REFUNDED', 'Refunded'
        FAILED = 'FAILED', 'Failed'

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PAID)
    reference = models.CharField(max_length=60, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f'{self.sale.invoice_no} - {self.get_method_display()}'


# --------------------------------------------------------------------------
# Returns and refunds (BR-028, BR-029)
# --------------------------------------------------------------------------
class SaleReturn(models.Model):
    reference = models.CharField(max_length=30, unique=True)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name='returns')
    reason = models.CharField(max_length=255)
    refund_amount = models.DecimalField(default=ZERO, **MONEY)
    restock = models.BooleanField(
        default=True, help_text='Return saleable stock to inventory (BRL-9).'
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='returns_processed'
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.reference

    @classmethod
    def next_reference(cls):
        last_id = cls.objects.aggregate(value=Max('id'))['value'] or 0
        return f'RET-{1000 + last_id + 1}'


class SaleReturnItem(models.Model):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name='items')
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name='return_items')
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    refund_amount = models.DecimalField(default=ZERO, **MONEY)

    def __str__(self):
        return f'{self.sale_item.product_name} x{self.quantity}'


# --------------------------------------------------------------------------
# Inventory history (BR-007)
# --------------------------------------------------------------------------
class StockMovement(models.Model):
    class Reason(models.TextChoices):
        PURCHASE = 'PURCHASE', 'Purchase received'
        SALE = 'SALE', 'Sale'
        RETURN = 'RETURN', 'Customer return'
        ADJUSTMENT = 'ADJUSTMENT', 'Manual adjustment'
        DAMAGE = 'DAMAGE', 'Damaged / written off'
        OPENING = 'OPENING', 'Opening stock'

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    reason = models.CharField(max_length=12, choices=Reason.choices)
    quantity_change = models.IntegerField(
        help_text='Positive for stock in, negative for stock out.'
    )
    balance_after = models.IntegerField()
    reference = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='movements'
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['product', '-created_at'])]

    def __str__(self):
        return f'{self.product.name} {self.quantity_change:+d} ({self.get_reason_display()})'


# --------------------------------------------------------------------------
# Settings and audit (modules 16 / 17)
# --------------------------------------------------------------------------
class StoreSetting(models.Model):
    """Single-row store configuration (BRS assumption 8: one store)."""

    business_name = models.CharField(max_length=120, default="O'dell Tech Shopping")
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    currency_symbol = models.CharField(max_length=5, default='৳')
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('5.00'),
        help_text='Default sales tax percentage (BR-017).',
    )
    invoice_prefix = models.CharField(max_length=10, default='INV')
    existing_customer_discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('2.00'),
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal('100'))],
        help_text=(
            'Applied automatically to customers with a previous completed purchase. '
            'Cashiers cannot change or override it.'
        ),
    )
    max_cashier_discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        help_text='Legacy cashier discount ceiling; kept for historical records.',
    )
    allow_negative_stock = models.BooleanField(
        default=False, help_text='BRL-3: allow selling beyond available stock (backorders).'
    )
    allow_self_registration = models.BooleanField(
        default=True,
        help_text='Let new staff create their own account from the login page.',
    )
    require_registration_approval = models.BooleanField(
        default=True,
        help_text=(
            'Self-registered accounts stay disabled until an Administrator or '
            'Manager enables them. Turn this off only on a trusted network.'
        ),
    )
    receipt_footer = models.CharField(max_length=255, default='Thank you for shopping with us!')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'store setting'

    def __str__(self):
        return self.business_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ActivityLog(models.Model):
    """BR-031 / BRL-12: who did what, when."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='activities'
    )
    action = models.CharField(max_length=60)
    entity = models.CharField(max_length=40, blank=True)
    entity_id = models.CharField(max_length=40, blank=True)
    description = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.user} {self.action} {self.entity}'
