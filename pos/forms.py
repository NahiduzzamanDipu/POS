"""Forms. All validation that matters is enforced here, not in JavaScript."""

import re
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .customers import number_variants, validate_customer_number

from .models import (
    Category,
    Customer,
    PaymentMethod,
    Product,
    StockMovement,
    StoreSetting,
    Supplier,
    User,
)

ZERO = Decimal('0.00')


class StyledFormMixin:
    """Apply the design system's input classes without repeating widget attrs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                continue
            css = 'select' if isinstance(widget, forms.Select) else 'input'
            existing = widget.attrs.get('class', '')
            widget.attrs['class'] = f'{existing} {css}'.strip()
            if field.required:
                widget.attrs.setdefault('required', 'required')


class LoginForm(StyledFormMixin, AuthenticationForm):
    """Sign in with an email address or a phone number.

    The field keeps Django's ``username`` name so ``AuthenticationForm`` (and
    the auth backends behind it) work untouched -- what changes is only how the
    typed identifier is resolved to an account, in ``clean_username``. The role
    is never asked for: it is read from the account once the password checks
    out (BR-002).
    """

    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': 'No account matches that email or phone, or the password is wrong.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields['username']
        field.label = 'Email or Phone'
        field.widget.attrs.update({
            'placeholder': 'you@example.com or 01XXXXXXXXX',
            'autofocus': True,
            'autocomplete': 'username',
            'autocapitalize': 'none',
            'spellcheck': 'false',
        })
        self.fields['password'].widget.attrs.update({
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password',
        })

    def clean_username(self):
        """Turn whatever was typed into the account's stored username.

        Returns the raw value when nothing matches, so a wrong address fails as
        an ordinary bad login rather than telling a stranger which addresses
        exist.
        """
        raw = (self.cleaned_data.get('username') or '').strip()
        if not raw:
            return raw

        if '@' in raw:
            matches = list(User.objects.filter(email__iexact=raw)[:2])
        else:
            lookup = Q(phone__iexact=raw)
            for variant in number_variants(raw):
                lookup |= Q(phone=variant)
            matches = list(User.objects.filter(lookup)[:2])
            if not matches:
                # Last resort so that no existing account is locked out. The
                # login screen never mentions usernames.
                matches = list(User.objects.filter(username__iexact=raw)[:2])

        if len(matches) > 1:
            raise ValidationError(
                'That phone number is registered to more than one account. '
                'Please sign in with your email address instead.'
            )
        return matches[0].username if matches else raw


class CategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'description', 'is_active']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class SupplierForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'contact_person', 'phone', 'email', 'address', 'is_active']
        widgets = {'address': forms.Textarea(attrs={'rows': 3})}


class ProductForm(StyledFormMixin, forms.ModelForm):
    """BR-003 / BR-004. Price fields are disabled for roles without BRL-4 rights."""

    class Meta:
        model = Product
        fields = [
            'name', 'sku', 'barcode', 'category', 'supplier', 'brand', 'image_url',
            'cost_price', 'selling_price', 'discount_percent',
            'min_stock_level', 'unit', 'is_active',
        ]

    def __init__(self, *args, can_edit_prices=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_edit_prices = can_edit_prices
        self.fields['category'].queryset = Category.objects.filter(is_active=True)
        self.fields['supplier'].queryset = Supplier.objects.filter(is_active=True)
        self.fields['barcode'].required = False
        self.fields['discount_percent'].label = 'Discount (%)'
        self.fields['image_url'].label = 'Image link'
        self.fields['image_url'].widget.attrs['placeholder'] = 'https://...'
        self.fields['discount_percent'].required = False
        if not can_edit_prices:
            # A discount is a price change, so it needs the same authority (BRL-4).
            for name in ('cost_price', 'selling_price', 'discount_percent'):
                self.fields[name].disabled = True
                self.fields[name].help_text = 'Only a Manager or Administrator may change prices.'

    def clean_sku(self):
        return self.cleaned_data['sku'].strip().upper()

    def clean_discount_percent(self):
        # A blank cell means "no discount", not "invalid".
        return self.cleaned_data.get('discount_percent') or ZERO

    def clean_barcode(self):
        # A model field with null=True cleans blanks to None, so normalise first.
        return (self.cleaned_data.get('barcode') or '').strip() or None

    def clean(self):
        cleaned = super().clean()
        cost = cleaned.get('cost_price')
        price = cleaned.get('selling_price')
        if cost is not None and price is not None and price < cost:
            self.add_error(
                'selling_price', 'Selling price is below cost price - this sale would lose money.'
            )
        discount = cleaned.get('discount_percent')
        if discount is not None and (discount < 0 or discount > 100):
            self.add_error('discount_percent', 'Discount must be between 0 and 100%.')
        elif cost is not None and price is not None and discount:
            final = price - (price * discount / Decimal('100'))
            if final < cost:
                self.add_error(
                    'discount_percent',
                    f'A {discount}% discount drops the price to {final:.2f}, below the '
                    f'{cost:.2f} cost.',
                )
        return cleaned


class CustomerForm(StyledFormMixin, forms.ModelForm):
    """The customer number is the identifier; everything else is optional."""

    class Meta:
        model = Customer
        fields = ['phone', 'name', 'email', 'address', 'is_active']
        labels = {'phone': 'Customer Number'}
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'phone': forms.TextInput(attrs={'placeholder': '01XXXXXXXXX', 'inputmode': 'numeric'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = False
        self.fields['name'].help_text = 'Optional - never shown on staff screens or invoices.'

    def clean_phone(self):
        number = validate_customer_number(self.cleaned_data.get('phone'), required=True)
        clash = Customer.objects.filter(phone__in=number_variants(number))
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError('A customer with this number already exists.')
        return number


def unique_username_for(email, fallback=''):
    """A stable internal username derived from the employee's email address.

    Usernames became an implementation detail of Django's auth tables once
    people started signing in with an email or phone, so one is generated
    rather than asked for. It is never shown in the interface.
    """
    base = re.sub(r'[^a-z0-9._-]', '', (email or '').split('@')[0].lower())
    if not base:
        base = re.sub(r'[^a-z0-9._-]', '', (fallback or '').lower()) or 'staff'
    candidate, suffix = base, 1
    while User.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        candidate = '{0}{1}'.format(base, suffix)
    return candidate


class EmployeeForm(StyledFormMixin, forms.ModelForm):
    """BR-030. Passwords go through Django's hashers, never stored raw.

    The email address doubles as the sign-in identifier, so it is required and
    unique. ``username`` is generated from it and is deliberately not a field.
    """

    password1 = forms.CharField(
        label='Password', widget=forms.PasswordInput, required=False,
        help_text='Leave blank to keep the current password.',
    )
    password2 = forms.CharField(
        label='Confirm password', widget=forms.PasswordInput, required=False
    )

    class Meta:
        model = User
        # username and employee_id are both absent on purpose: one is derived
        # from the email, the other is generated server-side (User.save).
        fields = [
            'first_name', 'last_name', 'email', 'phone',
            'position', 'role', 'is_active',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        self.fields['email'].help_text = 'Used to sign in. Must be unique.'
        self.fields['first_name'].required = True
        self.fields['phone'].help_text = 'Optional second way to sign in.'
        if self.instance.pk is None:
            self.fields['password1'].required = True
            self.fields['password2'].required = True
            self.fields['password1'].help_text = 'At least 8 characters.'

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        clash = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError('Another account already uses this email address.')
        return email

    def clean_phone(self):
        """A blank phone is fine; a shared one is not, since it can sign in."""
        phone = (self.cleaned_data.get('phone') or '').strip()
        if not phone:
            return phone
        lookup = Q(phone__iexact=phone)
        for variant in number_variants(phone):
            lookup |= Q(phone=variant)
        clash = User.objects.filter(lookup)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError(
                'Another account already uses this phone number. Sign-in would '
                'be ambiguous, so each number must belong to one account.'
            )
        return phone

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 or p2:
            if p1 != p2:
                self.add_error('password2', 'The two password fields do not match.')
            elif len(p1) < 8:
                self.add_error('password1', 'Password must be at least 8 characters.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if not user.username:
            user.username = unique_username_for(
                self.cleaned_data.get('email'), self.cleaned_data.get('first_name')
            )
        password = self.cleaned_data.get('password1')
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


class StockAdjustmentForm(StyledFormMixin, forms.Form):
    """BR-007: manual stock in/out with a mandatory audit reason."""

    product = forms.ModelChoiceField(queryset=Product.objects.none())
    reason = forms.ChoiceField(
        choices=[
            (StockMovement.Reason.ADJUSTMENT, 'Manual adjustment'),
            (StockMovement.Reason.DAMAGE, 'Damaged / written off'),
            (StockMovement.Reason.OPENING, 'Opening stock'),
        ]
    )
    quantity_change = forms.IntegerField(
        label='Quantity change',
        help_text='Use a positive number to add stock, negative to remove it.',
    )
    note = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.active().select_related('category')

    def clean_quantity_change(self):
        value = self.cleaned_data['quantity_change']
        if value == 0:
            raise ValidationError('Quantity change cannot be zero.')
        return value

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        change = cleaned.get('quantity_change')
        if product and change and change < 0:
            store = StoreSetting.load()
            if not store.allow_negative_stock and product.stock_quantity + change < 0:
                self.add_error(
                    'quantity_change',
                    f'Only {product.stock_quantity} in stock; cannot remove {abs(change)}.',
                )
        return cleaned


class StoreSettingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = StoreSetting
        fields = [
            'business_name', 'address', 'phone', 'email', 'currency_symbol',
            'tax_rate', 'invoice_prefix', 'existing_customer_discount_percent',
            'allow_negative_stock', 'receipt_footer',
        ]
        labels = {
            'existing_customer_discount_percent': 'Existing customer discount (%)',
        }

    def clean_tax_rate(self):
        rate = self.cleaned_data['tax_rate']
        if rate < 0 or rate > 100:
            raise ValidationError('Tax rate must be between 0 and 100.')
        return rate

    def clean_invoice_prefix(self):
        return self.cleaned_data['invoice_prefix'].strip().upper()

    def clean_existing_customer_discount_percent(self):
        rate = self.cleaned_data['existing_customer_discount_percent']
        if rate < 0 or rate > 100:
            raise ValidationError('The customer discount must be between 0 and 100.')
        return rate


class ReturnForm(StyledFormMixin, forms.Form):
    """BR-028. Per-item quantities are validated in the view against the sale."""

    reason = forms.CharField(
        max_length=255, widget=forms.Textarea(attrs={'rows': 2}),
        help_text='Required for the return record.',
    )
    restock = forms.BooleanField(
        required=False, initial=True, label='Return items to sellable stock'
    )


class PurchaseForm(StyledFormMixin, forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    purchase_date = forms.DateField(
        initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'})
    )
    note = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.filter(is_active=True)

    def clean_purchase_date(self):
        date = self.cleaned_data['purchase_date']
        if date > timezone.localdate():
            raise ValidationError('Purchase date cannot be in the future.')
        return date


class CheckoutForm(forms.Form):
    """Server-side guard for the POS checkout payload (BR-013, BR-020).

    There is deliberately no discount input: product discounts come from the
    product record and the loyalty discount is decided by the server from sales
    history. Nothing about pricing is accepted from the browser.
    """

    cart = forms.CharField()
    customer_number = forms.CharField(max_length=20, required=False)
    payment_method = forms.ChoiceField(choices=PaymentMethod.choices)
    amount_paid = forms.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=ZERO)
    note = forms.CharField(max_length=255, required=False)
    txn_token = forms.CharField(max_length=64)

    def clean_cart(self):
        """Parse the ``id:qty,id:qty`` cart string into validated pairs."""
        raw = self.cleaned_data['cart'].strip()
        if not raw:
            raise ValidationError('The cart is empty.')
        items = []
        for chunk in raw.split(','):
            if not chunk.strip():
                continue
            try:
                product_id, quantity = chunk.split(':')
                product_id, quantity = int(product_id), int(quantity)
            except ValueError:
                raise ValidationError('The cart could not be read. Please rebuild it.')
            if quantity < 1:
                raise ValidationError('Every cart line needs a quantity of at least 1.')
            items.append((product_id, quantity))
        if not items:
            raise ValidationError('The cart is empty.')
        return items

    def clean_customer_number(self):
        return validate_customer_number(self.cleaned_data.get('customer_number'), required=False)



class ProductImportForm(StyledFormMixin, forms.Form):
    """Step 1 of Product Entry Automation: choose a file and the import rules."""

    file = forms.FileField(
        label='Product file',
        help_text='CSV or Excel (.xlsx), up to 2000 rows.',
        widget=forms.ClearableFileInput(attrs={'accept': '.csv,.xlsx,.xlsm,.txt'}),
    )
    create_missing = forms.BooleanField(
        label='Create categories and suppliers that do not exist yet',
        required=False,
        initial=True,
        help_text=(
            'On by default: a new catalogue almost always brings new categories, '
            'and without this every one of those rows fails.'
        ),
    )
    update_existing = forms.BooleanField(
        label='Update products that already have this SKU',
        required=False,
        initial=True,
        help_text='Unticked, a row whose SKU already exists is reported as an error.',
    )

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError('The file is larger than 5 MB.')
        if not upload.name.lower().endswith(('.csv', '.txt', '.xlsx', '.xlsm')):
            raise ValidationError('Upload a .csv or .xlsx file.')
        return upload
