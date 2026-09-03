"""Product Entry Automation: bulk product creation from CSV or Excel.

Parsing and validation are deliberately separate from committing, so the user
always sees exactly what will happen before anything is written. Nothing is
saved until :func:`commit_rows` runs, and that runs in a single transaction.
"""

import csv
import io
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import Category, Product, StockMovement, Supplier
from .services import adjust_stock, log_activity

ZERO = Decimal('0.00')
MAX_ROWS = 2000

COLUMNS = [
    'name', 'sku', 'barcode', 'category', 'supplier', 'brand', 'image_url',
    'cost_price', 'selling_price', 'list_price', 'final_price',
    'discount_percent', 'stock_quantity', 'min_stock_level', 'unit',
    'is_active',
]

# Only the name, a category and *some* price are genuinely required. An SKU is
# generated when the file has none, and cost may legitimately be unknown for a
# catalogue exported from a storefront.
REQUIRED = ['name', 'category']

TEMPLATE_COLUMNS = [
    'name', 'sku', 'barcode', 'category', 'supplier', 'brand', 'image_url',
    'cost_price', 'selling_price', 'discount_percent', 'stock_quantity',
    'min_stock_level', 'unit', 'is_active',
]

TEMPLATE_SAMPLE = [
    ['Basmati Rice 5kg', 'SKU-2001', '8801234567890', 'Grocery', 'Dhaka Wholesale Ltd',
     'Chashi', 'https://example.com/rice.jpg', '520.00', '650.00', '5', '120', '20',
     'pack', 'yes'],
    ['Green Tea 100pc', 'SKU-2002', '', 'Grocery', '', 'Ispahani', '',
     '180.00', '250.00', '0', '40', '12', 'box', 'yes'],
]

UNIT_CODES = {code for code, _label in Product.Unit.choices}
UNIT_BY_LABEL = {label.lower(): code for code, label in Product.Unit.choices}

TRUTHY = {'1', 'true', 'yes', 'y', 'active'}
FALSY = {'0', 'false', 'no', 'n', 'inactive'}


class ImportError_(Exception):
    """The file itself could not be read (wrong format, no header, too big)."""


class ParsedRow:
    """One spreadsheet line, validated and ready to preview."""

    def __init__(self, number, raw):
        self.number = number
        self.raw = raw
        self.errors = []
        self.warnings = []
        self.data = {}
        self.action = 'new'          # new | update | skip
        self.existing = None
        self.generated_sku = False

    @property
    def is_valid(self):
        return not self.errors

    @property
    def name(self):
        return self.raw.get('name', '')

    @property
    def sku(self):
        return self.raw.get('sku', '')

    @property
    def message(self):
        return '; '.join(self.errors)

    @property
    def warning_message(self):
        return '; '.join(self.warnings)

    @property
    def has_warning(self):
        return bool(self.warnings) and not self.errors

    @property
    def final_price(self):
        """What the till will charge, i.e. list price less the product discount."""
        price = self.data.get('selling_price')
        if price is None:
            return None
        discount = self.data.get('discount_percent') or Decimal('0')
        return (price - (price * discount / Decimal('100'))).quantize(Decimal('0.01'))


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def read_table(uploaded_file):
    """Return ``(header, rows)`` of raw strings from a .csv or .xlsx upload."""
    name = (uploaded_file.name or '').lower()
    if name.endswith(('.xlsx', '.xlsm')):
        return _read_excel(uploaded_file)
    if name.endswith('.csv') or name.endswith('.txt'):
        return _read_csv(uploaded_file)
    raise ImportError_('Upload a .csv or .xlsx file.')


def _read_csv(uploaded_file):
    raw = uploaded_file.read()
    for encoding in ('utf-8-sig', 'utf-16', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise ImportError_('The file could not be decoded. Save it as UTF-8 CSV.')

    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    table = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not table:
        raise ImportError_('The file is empty.')
    return table[0], table[1:]


def _read_excel(uploaded_file):
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - dependency is pinned
        raise ImportError_('Excel support is unavailable. Save the file as CSV instead.')

    try:
        workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
    except Exception:
        raise ImportError_('That file could not be opened as a spreadsheet.')

    sheet = workbook.active
    table = []
    for row in sheet.iter_rows(values_only=True):
        cells = ['' if cell is None else str(cell).strip() for cell in row]
        if any(cells):
            table.append(cells)
    workbook.close()
    if not table:
        raise ImportError_('The spreadsheet is empty.')
    return table[0], table[1:]


def map_header(header):
    """Match spreadsheet headings to model fields, tolerating spacing and case."""
    aliases = {
        'product': 'name', 'product name': 'name', 'item': 'name',
        'code': 'sku', 'product code': 'sku', 'item code': 'sku',
        'bar code': 'barcode', 'ean': 'barcode', 'upc': 'barcode',
        'cost': 'cost_price', 'purchase price': 'cost_price', 'buy price': 'cost_price',
        'price': 'price', 'selling': 'selling_price', 'sell price': 'selling_price',
        # A storefront export usually carries both the pre-discount list price
        # and the already-discounted price. Keep them apart so the discount is
        # never applied twice.
        'old price': 'list_price', 'mrp': 'list_price', 'list price': 'list_price',
        'regular price': 'list_price', 'was price': 'list_price',
        'final price': 'final_price', 'sale price': 'final_price',
        'discounted price': 'final_price', 'net price': 'final_price',
        'image': 'image_url', 'image link': 'image_url', 'photo': 'image_url',
        'picture': 'image_url', 'thumbnail': 'image_url',
        'id': 'external_id', 'product id': 'external_id', 'item id': 'external_id',
        'stock': 'stock_quantity', 'quantity': 'stock_quantity', 'qty': 'stock_quantity',
        'opening stock': 'stock_quantity',
        'min stock': 'min_stock_level', 'minimum': 'min_stock_level',
        'reorder level': 'min_stock_level', 'min level': 'min_stock_level',
        'status': 'is_active', 'active': 'is_active',
        'discount': 'discount_percent', 'discount %': 'discount_percent',
        'discount percent': 'discount_percent', 'product discount': 'discount_percent',
        'offer': 'discount_percent',
    }
    mapping = {}
    extra = {'price', 'external_id'}
    for index, cell in enumerate(header):
        key = str(cell or '').strip().lower().replace('_', ' ')
        snake = key.replace(' ', '_')
        field = snake if snake in COLUMNS or snake in extra else aliases.get(key)
        if field and field not in mapping:
            mapping[field] = index
    missing = [field for field in REQUIRED if field not in mapping]
    if not ({'selling_price', 'list_price', 'price', 'final_price'} & set(mapping)):
        missing.append('selling_price (or price / old_price)')
    if missing:
        raise ImportError_(
            'These required columns are missing: ' + ', '.join(missing)
            + '. Download the template for the expected layout.'
        )
    return mapping


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _decimal(value, label, row, minimum=Decimal('0')):
    text = str(value or '').strip().replace(',', '')
    if not text:
        row.errors.append(f'{label} is required')
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        row.errors.append(f'{label} "{value}" is not a number')
        return None
    if number < minimum:
        row.errors.append(f'{label} cannot be negative')
        return None
    return number.quantize(Decimal('0.01'))


def _clean_url(value, row):
    """Keep http(s) links only; anything else is dropped with a warning."""
    url = str(value or '').strip()
    if not url:
        return ''
    if not url.lower().startswith(('http://', 'https://')):
        row.errors.append(f'Image link "{url[:40]}" is not an http(s) URL')
        return ''
    if len(url) > 500:
        row.errors.append('Image link is longer than 500 characters')
        return ''
    return url


def money_2dp(value):
    return Decimal(value).quantize(Decimal('0.01'))


def _decimal_optional(value, label, row):
    """Like :func:`_decimal` but a blank cell simply means "not set"."""
    text = str(value or '').strip().replace(',', '').rstrip('%').strip()
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        row.errors.append(f'{label} "{value}" is not a number')
        return None


def _integer(value, label, row, default=0):
    text = str(value or '').strip().replace(',', '')
    if not text:
        return default
    try:
        number = int(float(text))
    except (TypeError, ValueError):
        row.errors.append(f'{label} "{value}" is not a whole number')
        return default
    if number < 0:
        row.errors.append(f'{label} cannot be negative')
        return default
    return number


def _boolean(value, default=True):
    text = str(value or '').strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    return default


def _generated_sku(external_id, offset, existing_products, seen_skus):
    """Build an SKU for a file that has no SKU column.

    When the source system supplies its own id the SKU is derived from it and
    is therefore *stable*: importing the same file twice matches the same
    products and updates them instead of creating duplicates. Only the
    row-number fallback needs to dodge collisions, and then only against SKUs
    already used inside this file.
    """
    external = (external_id or '').strip()
    if external:
        return f'SKU-{external}'.upper()[:40]

    candidate = f'SKU-R{offset}'.upper()[:40]
    if candidate not in existing_products and candidate not in seen_skus:
        return candidate
    stem = candidate[:36]
    for suffix in range(2, 1000):
        alternative = f'{stem}-{suffix}'
        if alternative not in existing_products and alternative not in seen_skus:
            return alternative
    return candidate


def parse_rows(header, rows, *, create_missing=False, update_existing=True):
    """Validate every line against the catalogue rules and report row by row."""
    mapping = map_header(header)
    if len(rows) > MAX_ROWS:
        raise ImportError_(
            f'The file has {len(rows)} rows; the limit is {MAX_ROWS} per import. '
            'Split it into smaller files.'
        )

    known_categories = {c.name.lower(): c for c in Category.objects.all()}
    known_suppliers = {s.name.lower(): s for s in Supplier.objects.all()}
    existing_products = {p.sku.upper(): p for p in Product.objects.all()}
    taken_barcodes = {
        p.barcode: p.sku for p in Product.objects.exclude(barcode__isnull=True)
    }

    seen_skus = {}
    seen_barcodes = {}
    parsed = []

    for offset, cells in enumerate(rows, start=2):
        raw = {
            field: str(cells[index]).strip() if index < len(cells) else ''
            for field, index in mapping.items()
        }
        row = ParsedRow(offset, raw)

        name = raw.get('name', '')
        if not name:
            row.errors.append('Product name is required')
        elif len(name) > 150:
            row.errors.append('Product name is longer than 150 characters')

        # A file without an SKU column gets one generated: from the source
        # system's own id where available, otherwise from the row number. This
        # keeps a re-import of the same file matching the same products.
        sku = raw.get('sku', '').upper()
        if not sku:
            sku = _generated_sku(
                raw.get('external_id'), offset, existing_products, seen_skus
            )
            row.generated_sku = True
        if len(sku) > 40:
            row.errors.append('SKU is longer than 40 characters')
        elif sku in seen_skus:
            row.errors.append(f'SKU {sku} is repeated on row {seen_skus[sku]}')
        else:
            seen_skus[sku] = offset

        # An SKU already in the catalogue is an update, not a clash.
        existing = existing_products.get(sku)
        if existing is not None:
            if not update_existing:
                row.errors.append(f'SKU {sku} already exists')
            else:
                row.existing = existing
                row.action = 'update'

        barcode = raw.get('barcode', '') or None
        if barcode:
            if len(barcode) > 60:
                row.errors.append('Barcode is longer than 60 characters')
            elif barcode in seen_barcodes:
                row.errors.append(f'Barcode {barcode} is repeated on row {seen_barcodes[barcode]}')
            else:
                seen_barcodes[barcode] = offset
                owner = taken_barcodes.get(barcode)
                if owner and owner.upper() != sku:
                    row.errors.append(f'Barcode {barcode} already belongs to {owner}')

        category_name = raw.get('category', '')
        category = known_categories.get(category_name.lower()) if category_name else None
        if not category_name:
            row.errors.append('Category is required')
        elif category is None and not create_missing:
            row.errors.append(f'Category "{category_name}" does not exist')

        supplier_name = raw.get('supplier', '')
        supplier = known_suppliers.get(supplier_name.lower()) if supplier_name else None
        if supplier_name and supplier is None and not create_missing:
            row.errors.append(f'Supplier "{supplier_name}" does not exist')

        # Cost is optional: a storefront export rarely includes it.
        cost = _decimal_optional(raw.get('cost_price'), 'Cost price', row)
        if cost is None:
            cost = ZERO
        elif cost < ZERO:
            row.errors.append('Cost price cannot be negative')
            cost = ZERO

        # Work out the list price. When a file carries both a pre-discount
        # price and an already-discounted one, the list price is what belongs
        # in selling_price -- storing the discounted figure would apply the
        # discount a second time at the till.
        list_price = _decimal_optional(raw.get('list_price'), 'List price', row)
        plain_price = _decimal_optional(raw.get('price'), 'Price', row)
        explicit = _decimal_optional(raw.get('selling_price'), 'Selling price', row)
        final_price = _decimal_optional(raw.get('final_price'), 'Final price', row)

        price = explicit if explicit is not None else list_price
        if price is None:
            price = plain_price
        if price is None:
            price = final_price
        if price is None:
            row.errors.append('Selling price is required')
        elif price < ZERO:
            row.errors.append('Selling price cannot be negative')
            price = None

        if cost is not None and price is not None and price < cost:
            row.errors.append('Selling price is below cost price')

        unit_raw = raw.get('unit', '').strip().lower()
        unit = unit_raw if unit_raw in UNIT_CODES else UNIT_BY_LABEL.get(unit_raw, '')
        if unit_raw and not unit:
            row.errors.append(f'Unit "{raw.get("unit")}" is not recognised')
            unit = Product.Unit.PIECE
        unit = unit or Product.Unit.PIECE

        discount = _decimal_optional(raw.get('discount_percent'), 'Discount', row)
        if discount is not None and (discount < 0 or discount > 100):
            row.errors.append('Discount must be between 0 and 100%')
            discount = ZERO
        discount = discount if discount is not None else ZERO
        if cost is not None and price is not None and discount:
            final = money_2dp(price - (price * discount / Decimal('100')))
            if cost > ZERO and final < cost:
                row.errors.append(
                    f'A {discount}% discount drops the price to {final}, below the {cost} cost'
                )
            # If the file also states the discounted price, cross-check it.
            # Source systems commonly round the percentage, so a small gap is
            # reported but does not stop the row importing: the list price and
            # the discount are what the till actually charges.
            if final_price is not None and abs(final - final_price) > Decimal('0.50'):
                row.warnings.append(
                    f'file says {final_price} but {price} less {discount}% is {final}'
                )

        stock = _integer(raw.get('stock_quantity'), 'Stock quantity', row, default=0)
        minimum = _integer(raw.get('min_stock_level'), 'Minimum stock', row, default=5)

        row.data = {
            'name': name,
            'sku': sku,
            'barcode': barcode,
            'category_name': category_name,
            'category': category,
            'supplier_name': supplier_name,
            'supplier': supplier,
            'brand': raw.get('brand', '')[:80],
            'image_url': _clean_url(raw.get('image_url'), row),
            'cost_price': cost,
            'selling_price': price,
            'discount_percent': discount,
            'stock_quantity': stock,
            'min_stock_level': minimum,
            'unit': unit,
            'is_active': _boolean(raw.get('is_active'), default=True),
        }
        parsed.append(row)

    return parsed


def summarise(rows):
    return {
        'total': len(rows),
        'valid': sum(1 for r in rows if r.is_valid),
        'errors': sum(1 for r in rows if not r.is_valid),
        'warnings': sum(1 for r in rows if r.has_warning),
        'new': sum(1 for r in rows if r.is_valid and r.action == 'new'),
        'updates': sum(1 for r in rows if r.is_valid and r.action == 'update'),
    }


# --------------------------------------------------------------------------
# Committing
# --------------------------------------------------------------------------
@transaction.atomic
def commit_rows(rows, user, *, create_missing=False, request=None):
    """Write every valid row. Invalid rows are skipped, never guessed at.

    Opening stock is applied through :func:`~pos.services.adjust_stock` so the
    movement is recorded (BRL-8). Existing products keep their current stock:
    an import adjusts the catalogue, not the inventory ledger.
    """
    created = updated = 0
    stocked = 0

    for row in rows:
        if not row.is_valid:
            continue
        data = row.data

        category = data['category']
        if category is None and create_missing and data['category_name']:
            category, _ = Category.objects.get_or_create(
                name=data['category_name'],
                defaults={'description': 'Created by product import.'},
            )
        if category is None:
            continue

        supplier = data['supplier']
        if supplier is None and create_missing and data['supplier_name']:
            supplier, _ = Supplier.objects.get_or_create(name=data['supplier_name'])

        fields = {
            'name': data['name'],
            'barcode': data['barcode'],
            'category': category,
            'supplier': supplier,
            'brand': data['brand'],
            'image_url': data.get('image_url', ''),
            'cost_price': data['cost_price'],
            'selling_price': data['selling_price'],
            'discount_percent': data.get('discount_percent') or ZERO,
            'min_stock_level': data['min_stock_level'],
            'unit': data['unit'],
            'is_active': data['is_active'],
        }

        product = Product.objects.filter(sku=data['sku']).first()
        if product is None:
            product = Product.objects.create(sku=data['sku'], **fields)
            created += 1
            if data['stock_quantity']:
                adjust_stock(
                    product,
                    data['stock_quantity'],
                    StockMovement.Reason.OPENING,
                    user=user,
                    reference='IMPORT',
                    note='Opening stock from product import',
                    allow_negative=False,
                )
                stocked += 1
        else:
            for field, value in fields.items():
                setattr(product, field, value)
            product.save()
            updated += 1

    log_activity(
        user,
        'PRODUCTS_IMPORTED',
        'Product',
        description=f'{created} created, {updated} updated, {stocked} opening-stock entries',
        request=request,
    )
    return {'created': created, 'updated': updated, 'stocked': stocked}


def template_csv():
    """The downloadable starter file, matching :data:`COLUMNS` exactly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(TEMPLATE_COLUMNS)
    writer.writerows(TEMPLATE_SAMPLE)
    return buffer.getvalue()
