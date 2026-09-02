"""Customer-number handling for the POS counter.

The customer number (a Bangladesh mobile number) is the only identifier the
till asks for. Everything else -- whether they are an existing customer, what
discount they get -- is derived from the database, never from the browser.
"""

import re
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import Customer, Sale, StoreSetting

ZERO = Decimal('0.00')

# Local form: 11 digits starting 01, e.g. 01712345678
LOCAL_PATTERN = re.compile(r'^01[3-9]\d{8}$')


def normalise_customer_number(raw):
    """Reduce any accepted spelling of a number to the canonical local form.

    ``+8801712345678``, ``8801712345678``, ``01712345678`` and
    ``017-1234 5678`` all become ``01712345678``.
    """
    digits = re.sub(r'\D', '', raw or '')
    if not digits:
        return ''
    if digits.startswith('00880'):
        digits = '0' + digits[5:]
    elif digits.startswith('880') and len(digits) == 13:
        digits = '0' + digits[3:]
    elif len(digits) == 10 and digits.startswith('1'):
        digits = '0' + digits
    return digits


def validate_customer_number(raw, *, required=True):
    """Return the canonical number, or raise ``ValidationError``."""
    number = normalise_customer_number(raw)
    if not number:
        if required:
            raise ValidationError('Enter the customer number.')
        return ''
    if not LOCAL_PATTERN.match(number):
        raise ValidationError(
            'Enter a valid Bangladesh mobile number, for example 01712345678.'
        )
    return number


def number_variants(number):
    """Every stored spelling equivalent to ``number``.

    Older records were saved as ``+880...``; matching all forms is what stops a
    second Customer row being created for someone who already exists.
    """
    number = normalise_customer_number(number)
    if not number:
        return []
    tail = number[1:]                       # drop the leading 0
    return [number, f'+880{tail}', f'880{tail}', f'00880{tail}']


def find_customer(number):
    """The Customer for this number under any stored spelling, or None."""
    variants = number_variants(number)
    if not variants:
        return None
    return Customer.objects.filter(phone__in=variants).first()


def completed_sales_for(number):
    """Valid, non-void sales belonging to this customer number.

    Matches on the snapshotted ``customer_number`` as well as the relation, so
    a sale still counts even if the Customer row was edited afterwards.
    """
    variants = number_variants(number)
    if not variants:
        return Sale.objects.none()
    from django.db.models import Q

    return Sale.objects.filter(
        Q(customer_number__in=variants) | Q(customer__phone__in=variants)
    ).exclude(status=Sale.Status.VOID)


def is_existing_customer(number):
    """True when this number has at least one completed purchase.

    Deliberately based on real sales history, not merely on a Customer row
    existing -- a row created by a cancelled sale must not earn loyalty.
    """
    return completed_sales_for(number).exists()


def customer_discount_percent(number, *, store=None):
    """The loyalty rate this number earns right now: the configured rate or zero."""
    store = store or StoreSetting.load()
    if not number:
        return ZERO
    return Decimal(store.existing_customer_discount_percent) if is_existing_customer(number) else ZERO


def lookup(number, *, store=None):
    """Everything the till needs to show about a number, in one call."""
    store = store or StoreSetting.load()
    normalised = normalise_customer_number(number)
    valid = bool(LOCAL_PATTERN.match(normalised)) if normalised else False
    existing = is_existing_customer(normalised) if valid else False
    return {
        'number': normalised,
        'valid': valid,
        'existing': existing,
        'discount_percent': (
            Decimal(store.existing_customer_discount_percent) if existing else ZERO
        ),
        'orders': completed_sales_for(normalised).count() if valid else 0,
    }


def get_or_create_customer(number):
    """Find the customer for this number, creating a bare record if needed.

    Only the number is stored: the POS never collects a name or an email.
    """
    number = normalise_customer_number(number)
    if not number:
        return None
    customer = find_customer(number)
    if customer is not None:
        return customer
    customer, _ = Customer.objects.get_or_create(
        phone=number, defaults={'name': '', 'is_active': True}
    )
    return customer
