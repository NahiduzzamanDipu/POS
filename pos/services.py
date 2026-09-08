"""Business logic for stock, sales, returns and purchases.

Every rule that the BRS states as mandatory lives here rather than in a view,
so the POS screen, the admin and the tests all enforce it identically.
"""

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .customers import (
    get_or_create_customer,
    is_existing_customer,
    validate_customer_number,
)
from .models import (
    ActivityLog,
    Payment,
    PaymentMethod,
    Product,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    SaleReturn,
    SaleReturnItem,
    StockMovement,
    StoreSetting,
)

logger = logging.getLogger('pos')

TWO_PLACES = Decimal('0.01')
ZERO = Decimal('0.00')
HUNDRED = Decimal('100')


class BusinessRuleError(Exception):
    """A request that violates a documented business rule."""


def money(value):
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Audit trail (BR-031)
# --------------------------------------------------------------------------
def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_activity(user, action, entity='', entity_id='', description='', request=None):
    return ActivityLog.objects.create(
        user=user if user and user.is_authenticated else None,
        action=action,
        entity=entity,
        entity_id=str(entity_id or ''),
        description=description[:255],
        ip_address=client_ip(request),
    )


# --------------------------------------------------------------------------
# Inventory (BR-007 .. BR-010, BRL-8)
# --------------------------------------------------------------------------
def adjust_stock(product, quantity_change, reason, user=None, reference='', note='', allow_negative=None):
    """Apply a stock delta and record the movement. Always call inside a transaction.

    ``product`` must already be locked with ``select_for_update`` when the
    caller is racing other writers (see :func:`create_sale`).
    """
    quantity_change = int(quantity_change)
    if quantity_change == 0:
        raise BusinessRuleError('Stock change cannot be zero.')

    if allow_negative is None:
        allow_negative = StoreSetting.load().allow_negative_stock

    new_balance = product.stock_quantity + quantity_change
    if new_balance < 0 and not allow_negative:
        raise BusinessRuleError(
            f'{product.name}: only {product.stock_quantity} {product.get_unit_display().lower()} '
            f'in stock, cannot remove {abs(quantity_change)}.'
        )

    Product.objects.filter(pk=product.pk).update(
        stock_quantity=F('stock_quantity') + quantity_change
    )
    product.stock_quantity = new_balance

    return StockMovement.objects.create(
        product=product,
        reason=reason,
        quantity_change=quantity_change,
        balance_after=new_balance,
        reference=reference,
        note=note,
        created_by=user if user and user.is_authenticated else None,
    )


def low_stock_products():
    return Product.objects.low_stock().select_related('category')


def out_of_stock_products():
    return Product.objects.out_of_stock().select_related('category')


# --------------------------------------------------------------------------
# Pricing (BR-015 .. BR-018)
# --------------------------------------------------------------------------
@dataclass
class CartLine:
    """One cart line, priced at the product's own discount rate."""

    product: Product
    quantity: int
    unit_price: Decimal = ZERO
    discount_percent: Decimal = ZERO
    gross: Decimal = ZERO
    discount_amount: Decimal = ZERO

    @property
    def subtotal(self):
        return money(self.gross - self.discount_amount)

    @property
    def unit_final_price(self):
        return money(self.unit_price - (self.unit_price * self.discount_percent / HUNDRED))


@dataclass
class CartTotals:
    lines: list = field(default_factory=list)
    subtotal: Decimal = ZERO                    # sum of price x quantity
    product_discount: Decimal = ZERO            # sum of per-product discounts
    customer_discount: Decimal = ZERO           # existing-customer loyalty discount
    customer_discount_percent: Decimal = ZERO
    discount_amount: Decimal = ZERO             # product + customer, combined
    net_before_tax: Decimal = ZERO
    tax_rate: Decimal = ZERO
    tax_amount: Decimal = ZERO
    total: Decimal = ZERO
    existing_customer: bool = False


def price_cart(items, *, existing_customer=False, store=None):
    """Price a cart. The browser never does this -- only the server.

    Calculation order (documented so it cannot drift):

        product selling price
          -> product-level discount        (Product.discount_percent)
          -> product final price
          -> cart subtotal
          -> existing-customer discount    (StoreSetting rate, once, on the
                                            already product-discounted amount)
          -> tax                           (on the net amount)
          -> final total

    The customer discount is applied exactly once, after product discounts,
    and never compounds with them.
    """
    store = store or StoreSetting.load()
    lines = []
    subtotal = ZERO
    product_discount = ZERO

    for product, quantity in items:
        quantity = int(quantity)
        if quantity < 1:
            raise BusinessRuleError(f'{product.name}: quantity must be at least 1.')

        unit_price = money(product.selling_price)
        percent = Decimal(product.discount_percent or ZERO)
        gross = money(unit_price * quantity)
        line_discount = money(gross * percent / HUNDRED)

        lines.append(
            CartLine(
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                discount_percent=percent,
                gross=gross,
                discount_amount=line_discount,
            )
        )
        subtotal += gross
        product_discount += line_discount

    subtotal = money(subtotal)
    product_discount = money(product_discount)
    after_product = money(subtotal - product_discount)

    percent = (
        Decimal(store.existing_customer_discount_percent) if existing_customer else ZERO
    )
    customer_discount = money(after_product * percent / HUNDRED)

    net_before_tax = money(after_product - customer_discount)
    tax_rate = Decimal(store.tax_rate)
    tax_amount = money(net_before_tax * tax_rate / HUNDRED)

    return CartTotals(
        lines=lines,
        subtotal=subtotal,
        product_discount=product_discount,
        customer_discount=customer_discount,
        customer_discount_percent=percent,
        discount_amount=money(product_discount + customer_discount),
        net_before_tax=net_before_tax,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        total=money(net_before_tax + tax_amount),
        existing_customer=existing_customer,
    )


# --------------------------------------------------------------------------
# Sales (BR-013, BR-021, BR-022, BRL-3, BRL-8)
# --------------------------------------------------------------------------
@transaction.atomic
def create_sale(
    *,
    cashier,
    items,
    customer_number='',
    payment_method=PaymentMethod.CASH,
    amount_paid=None,
    note='',
    request=None,
):
    """Record a complete sale: validate, price, take payment, move stock, invoice.

    ``items`` is an iterable of ``(product_id, quantity)``. ``customer_number``
    is the only customer input the till collects.

    Loyalty eligibility is decided **here**, from the database, no matter what
    the browser displayed or submitted.
    """
    store = StoreSetting.load()
    normalised = _normalise_items(items)
    if not normalised:
        raise BusinessRuleError('Add at least one product to the cart.')

    number = validate_customer_number(customer_number, required=False)

    # Lock every product row for the life of the transaction so two terminals
    # cannot both sell the last unit (BRL-3).
    products = {
        product.pk: product
        for product in Product.objects.select_for_update().filter(pk__in=normalised)
    }

    cart_items = []
    for product_id, quantity in normalised.items():
        product = products.get(product_id)
        if product is None:
            raise BusinessRuleError('One of the selected products no longer exists.')
        if not product.is_active:
            raise BusinessRuleError(f'{product.name} is not available for sale.')
        if not store.allow_negative_stock and product.stock_quantity < quantity:
            raise BusinessRuleError(
                f'{product.name}: only {product.stock_quantity} in stock, {quantity} requested.'
            )
        cart_items.append((product, quantity))

    # Re-check loyalty against real sales history, ignoring anything the client sent.
    existing_customer = is_existing_customer(number) if number else False
    totals = price_cart(cart_items, existing_customer=existing_customer, store=store)

    amount_paid = money(amount_paid if amount_paid is not None else totals.total)
    if payment_method == PaymentMethod.CASH:
        if amount_paid < totals.total:
            raise BusinessRuleError(
                f'Cash received ({amount_paid}) is less than the total ({totals.total}).'
            )
        change_due = money(amount_paid - totals.total)
    else:
        # Non-cash tenders are captured for the exact amount (BR-021).
        amount_paid = totals.total
        change_due = ZERO

    # The customer record is created only once the sale is actually valid.
    customer = get_or_create_customer(number) if number else None

    sale = Sale.objects.create(
        invoice_no=Sale.next_invoice_no(store.invoice_prefix),
        customer=customer,
        customer_number=number,
        cashier=cashier,
        subtotal=totals.subtotal,
        product_discount_amount=totals.product_discount,
        customer_discount_amount=totals.customer_discount,
        customer_discount_percent=totals.customer_discount_percent,
        discount_amount=totals.discount_amount,
        tax_amount=totals.tax_amount,
        tax_rate=totals.tax_rate,
        total_amount=totals.total,
        amount_paid=amount_paid,
        change_due=change_due,
        payment_method=payment_method,
        note=note[:255],
        status=Sale.Status.COMPLETED,
    )

    for line in totals.lines:
        SaleItem.objects.create(
            sale=sale,
            product=line.product,
            product_name=line.product.name,
            quantity=line.quantity,
            unit_price=line.unit_price,
            # Snapshot the cost as well as the price. Without this, a later
            # edit to the product's cost_price would silently rewrite the
            # margin on every sale already made.
            unit_cost=line.product.cost_price,
            discount_percent=line.discount_percent,
            discount_amount=line.discount_amount,
            subtotal=line.subtotal,
        )
        adjust_stock(
            line.product,
            -line.quantity,
            StockMovement.Reason.SALE,
            user=cashier,
            reference=sale.invoice_no,
            allow_negative=store.allow_negative_stock,
        )

    Payment.objects.create(
        sale=sale,
        method=payment_method,
        amount=totals.total,
        status=Payment.Status.PAID,
        paid_at=timezone.now(),
    )

    log_activity(
        cashier,
        'SALE_COMPLETED',
        'Sale',
        sale.pk,
        f'{sale.invoice_no} total {totals.total}'
        + (f' customer {number}' if number else ' walk-in'),
        request=request,
    )
    logger.info('Sale %s completed by %s for %s', sale.invoice_no, cashier, totals.total)
    return sale


def _normalise_items(items):
    """Merge duplicate product ids and drop non-positive quantities."""
    merged = {}
    for product_id, quantity in items:
        quantity = int(quantity)
        if quantity < 1:
            raise BusinessRuleError('Quantity must be a whole number of at least 1.')
        merged[int(product_id)] = merged.get(int(product_id), 0) + quantity
    return merged


@transaction.atomic
def void_sale(sale, user, reason='', request=None):
    """BRL-6/BRL-7: never delete a sale - void it and put the stock back."""
    if sale.status == Sale.Status.VOID:
        raise BusinessRuleError('This sale is already void.')
    if sale.returns.exists():
        raise BusinessRuleError('This sale has returns recorded and cannot be voided.')

    for item in sale.items.select_related('product'):
        product = Product.objects.select_for_update().get(pk=item.product_id)
        adjust_stock(
            product,
            item.quantity,
            StockMovement.Reason.ADJUSTMENT,
            user=user,
            reference=sale.invoice_no,
            note='Sale voided',
        )

    sale.status = Sale.Status.VOID
    sale.voided_at = timezone.now()
    sale.voided_by = user
    sale.note = (f'{sale.note} | Void: {reason}').strip(' |')[:255]
    sale.save(update_fields=['status', 'voided_at', 'voided_by', 'note'])
    sale.payments.update(status=Payment.Status.REFUNDED)

    log_activity(user, 'SALE_VOIDED', 'Sale', sale.pk, reason, request=request)
    return sale


# --------------------------------------------------------------------------
# Returns and refunds (BR-028, BR-029, BRL-9)
# --------------------------------------------------------------------------
@transaction.atomic
def process_return(*, sale, lines, reason, user, restock=True, request=None):
    """Return items against a sale.

    ``lines`` is an iterable of ``(sale_item_id, quantity)``. Refunds include the
    tax originally charged on those units.
    """
    if not sale.is_returnable:
        raise BusinessRuleError(f'Sale {sale.invoice_no} is {sale.get_status_display().lower()} '
                                'and cannot be returned against.')

    requested = {}
    for item_id, quantity in lines:
        quantity = int(quantity)
        if quantity <= 0:
            continue
        requested[int(item_id)] = requested.get(int(item_id), 0) + quantity
    if not requested:
        raise BusinessRuleError('Select at least one item to return.')

    sale_items = {
        item.pk: item
        for item in SaleItem.objects.select_for_update()
        .select_related('product')
        .filter(pk__in=requested, sale=sale)
    }
    if len(sale_items) != len(requested):
        raise BusinessRuleError('One of the selected items does not belong to this invoice.')

    tax_multiplier = Decimal('1') + (Decimal(sale.tax_rate) / HUNDRED)
    sale_return = SaleReturn.objects.create(
        reference=SaleReturn.next_reference(),
        sale=sale,
        reason=reason[:255],
        restock=restock,
        processed_by=user,
    )

    refund_total = ZERO
    for item_id, quantity in requested.items():
        item = sale_items[item_id]
        if quantity > item.returnable_quantity:
            raise BusinessRuleError(
                f'{item.product_name}: {item.returnable_quantity} of {item.quantity} '
                f'remain returnable, {quantity} requested.'
            )
        refund = money(item.unit_refund_value * quantity * tax_multiplier)
        refund_total += refund

        SaleReturnItem.objects.create(
            sale_return=sale_return,
            sale_item=item,
            quantity=quantity,
            refund_amount=refund,
        )
        item.returned_quantity += quantity
        item.save(update_fields=['returned_quantity'])

        if restock:
            product = Product.objects.select_for_update().get(pk=item.product_id)
            adjust_stock(
                product,
                quantity,
                StockMovement.Reason.RETURN,
                user=user,
                reference=sale_return.reference,
            )
        else:
            product = Product.objects.select_for_update().get(pk=item.product_id)
            StockMovement.objects.create(
                product=product,
                reason=StockMovement.Reason.DAMAGE,
                quantity_change=0,
                balance_after=product.stock_quantity,
                reference=sale_return.reference,
                note='Returned unsaleable - not restocked',
                created_by=user,
            )

    refund_total = money(min(refund_total, sale.total_amount - sale.refunded_amount))
    sale_return.refund_amount = refund_total
    sale_return.save(update_fields=['refund_amount'])

    sale.refunded_amount = money(sale.refunded_amount + refund_total)
    fully_returned = all(item.returnable_quantity == 0 for item in sale.items.all())
    sale.status = Sale.Status.RETURNED if fully_returned else Sale.Status.PARTIALLY_RETURNED
    sale.save(update_fields=['refunded_amount', 'status'])

    log_activity(
        user,
        'RETURN_PROCESSED',
        'SaleReturn',
        sale_return.pk,
        f'{sale_return.reference} against {sale.invoice_no} refund {refund_total}',
        request=request,
    )
    return sale_return


# --------------------------------------------------------------------------
# Purchases (BR-012)
# --------------------------------------------------------------------------
@transaction.atomic
def receive_purchase(*, supplier, lines, user, purchase_date=None, note='', request=None):
    """Record stock received from a supplier and increase inventory."""
    cleaned = [(int(pid), int(qty), Decimal(cost)) for pid, qty, cost in lines if int(qty) > 0]
    if not cleaned:
        raise BusinessRuleError('Add at least one product line with a quantity.')

    purchase = Purchase.objects.create(
        reference=Purchase.next_reference(),
        supplier=supplier,
        purchase_date=purchase_date or timezone.localdate(),
        created_by=user,
        note=note[:255],
    )

    total = ZERO
    for product_id, quantity, unit_cost in cleaned:
        product = Product.objects.select_for_update().get(pk=product_id)
        item = PurchaseItem.objects.create(
            purchase=purchase,
            product=product,
            quantity=quantity,
            unit_cost=money(unit_cost),
        )
        total += item.subtotal
        adjust_stock(
            product,
            quantity,
            StockMovement.Reason.PURCHASE,
            user=user,
            reference=purchase.reference,
        )

    purchase.total_amount = money(total)
    purchase.save(update_fields=['total_amount'])

    log_activity(
        user,
        'PURCHASE_RECEIVED',
        'Purchase',
        purchase.pk,
        f'{purchase.reference} from {supplier.name} total {purchase.total_amount}',
        request=request,
    )
    return purchase
