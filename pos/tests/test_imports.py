"""Product Entry Automation: CSV/Excel bulk import."""

import io
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from pos.imports import (
    ImportError_,
    commit_rows,
    parse_rows,
    read_table,
    summarise,
)
from pos.models import Category, Product, Role, StockMovement, Supplier

from .factories import make_category, make_product, make_user

HEADER = ('name,sku,barcode,category,supplier,brand,cost_price,selling_price,'
          'discount_percent,stock_quantity,min_stock_level,unit,is_active')


def csv_file(body, name='products.csv'):
    return SimpleUploadedFile(name, body.encode('utf-8'), content_type='text/csv')


def rows_from(body, **options):
    header, table = read_table(csv_file(body))
    return parse_rows(header, table, **options)


class ReadTableTests(TestCase):
    def test_rejects_an_unsupported_extension(self):
        with self.assertRaises(ImportError_):
            read_table(csv_file('a,b', name='products.pdf'))

    def test_rejects_an_empty_file(self):
        with self.assertRaises(ImportError_):
            read_table(csv_file(''))

    def test_missing_required_columns_are_named(self):
        with self.assertRaises(ImportError_) as ctx:
            rows_from('name,sku\nRice,SKU-1')
        message = str(ctx.exception)
        self.assertIn('category', message)
        self.assertIn('selling_price', message)

    def test_cost_and_sku_are_no_longer_required_columns(self):
        """A storefront export has neither; both are filled in for you."""
        make_category('Grocery')
        rows = rows_from('name,category,price\nRice 5kg,Grocery,650')
        self.assertTrue(rows[0].is_valid, rows[0].message)
        self.assertEqual(rows[0].data['cost_price'], Decimal('0.00'))
        self.assertTrue(rows[0].data['sku'].startswith('SKU-'))

    def test_alternative_column_names_are_understood(self):
        make_category('Grocery')
        rows = rows_from('Product,Code,Category,Cost,MRP,Qty\nRice 5kg,SKU-9,Grocery,520,650,12')
        self.assertTrue(rows[0].is_valid)
        self.assertEqual(rows[0].data['selling_price'], Decimal('650.00'))
        self.assertEqual(rows[0].data['stock_quantity'], 12)

    def test_semicolon_delimited_files_are_handled(self):
        make_category('Grocery')
        body = 'name;sku;category;cost_price;selling_price\nRice 5kg;SKU-9;Grocery;520;650'
        rows = rows_from(body)
        self.assertTrue(rows[0].is_valid)

    def test_excel_files_are_read(self):
        from openpyxl import Workbook

        make_category('Grocery')
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['name', 'sku', 'category', 'cost_price', 'selling_price'])
        sheet.append(['Rice 5kg', 'SKU-XL', 'Grocery', 520, 650])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        upload = SimpleUploadedFile(
            'products.xlsx', buffer.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        header, table = read_table(upload)
        rows = parse_rows(header, table)
        self.assertTrue(rows[0].is_valid)
        self.assertEqual(rows[0].data['sku'], 'SKU-XL')


class ValidationTests(TestCase):
    def setUp(self):
        self.category = make_category('Grocery')

    def test_a_clean_row_is_marked_new(self):
        rows = rows_from(f'{HEADER}\nRice 5kg,sku-1,880123,Grocery,,Chashi,520,650,0,100,20,pack,yes')
        row = rows[0]
        self.assertTrue(row.is_valid)
        self.assertEqual(row.action, 'new')
        self.assertEqual(row.data['sku'], 'SKU-1')     # normalised to uppercase
        self.assertEqual(row.data['unit'], 'pack')
        self.assertTrue(row.data['is_active'])

    def test_a_known_sku_becomes_an_update(self):
        make_product(sku='SKU-1', category=self.category, stock=5)
        rows = rows_from(f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,520,650,0,0,20,pc,yes')
        self.assertTrue(rows[0].is_valid)
        self.assertEqual(rows[0].action, 'update')

    def test_a_known_sku_is_an_error_when_updates_are_off(self):
        make_product(sku='SKU-1', category=self.category, stock=5)
        rows = rows_from(
            f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,520,650,0,0,20,pc,yes',
            update_existing=False,
        )
        self.assertFalse(rows[0].is_valid)
        self.assertIn('already exists', rows[0].message)

    def test_a_missing_name_is_reported(self):
        rows = rows_from(f'{HEADER}\n,,,Grocery,,,520,650,0,0,20,pc,yes')
        self.assertIn('Product name is required', rows[0].message)

    def test_a_blank_sku_is_generated_rather_than_rejected(self):
        rows = rows_from(f'{HEADER}\nRice 5kg,,,Grocery,,,520,650,0,0,20,pc,yes')
        self.assertTrue(rows[0].is_valid, rows[0].message)
        self.assertTrue(rows[0].generated_sku)
        self.assertTrue(rows[0].data['sku'].startswith('SKU-'))

    def test_unknown_category_is_an_error_unless_creation_is_allowed(self):
        body = f'{HEADER}\nRice 5kg,SKU-1,,Nowhere,,,520,650,0,0,20,pc,yes'
        self.assertIn('does not exist', rows_from(body)[0].message)
        self.assertTrue(rows_from(body, create_missing=True)[0].is_valid)

    def test_non_numeric_price_is_reported(self):
        rows = rows_from(f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,abc,650,0,0,20,pc,yes')
        self.assertIn('not a number', rows[0].message)

    def test_selling_below_cost_is_rejected(self):
        rows = rows_from(f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,900,650,0,0,20,pc,yes')
        self.assertIn('below cost price', rows[0].message)

    def test_negative_price_is_rejected(self):
        rows = rows_from(f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,-5,650,0,0,20,pc,yes')
        self.assertIn('cannot be negative', rows[0].message)

    def test_duplicate_sku_inside_the_file_is_reported(self):
        body = (
            f'{HEADER}\n'
            'Rice 5kg,SKU-1,,Grocery,,,520,650,0,0,20,pc,yes\n'
            'Rice 10kg,SKU-1,,Grocery,,,520,650,0,0,20,pc,yes'
        )
        rows = rows_from(body)
        self.assertTrue(rows[0].is_valid)
        self.assertIn('repeated on row 2', rows[1].message)

    def test_barcode_belonging_to_another_product_is_reported(self):
        make_product(sku='SKU-OLD', barcode='880999', category=self.category, stock=1)
        rows = rows_from(f'{HEADER}\nRice,SKU-1,880999,Grocery,,,520,650,0,0,20,pc,yes')
        self.assertIn('already belongs to SKU-OLD', rows[0].message)

    def test_unknown_unit_is_reported(self):
        rows = rows_from(f'{HEADER}\nRice,SKU-1,,Grocery,,,520,650,0,0,20,barrels,yes')
        self.assertIn('not recognised', rows[0].message)

    def test_unit_label_is_accepted(self):
        rows = rows_from(f'{HEADER}\nRice,SKU-1,,Grocery,,,520,650,0,0,20,Kilogram,yes')
        self.assertTrue(rows[0].is_valid)
        self.assertEqual(rows[0].data['unit'], 'kg')

    def test_row_limit_is_enforced(self):
        lines = '\n'.join(
            f'Item {i},SKU-{i},,Grocery,,,10,20,0,0,5,pc,yes' for i in range(2001)
        )
        with self.assertRaises(ImportError_) as ctx:
            rows_from(f'{HEADER}\n{lines}')
        self.assertIn('limit is 2000', str(ctx.exception))

    def test_summary_counts_each_outcome(self):
        make_product(sku='SKU-1', category=self.category, stock=5)
        body = (
            f'{HEADER}\n'
            'Rice 5kg,SKU-1,,Grocery,,,520,650,0,0,20,pc,yes\n'
            'Milk 1L,SKU-2,,Grocery,,,85,120,0,50,15,ltr,yes\n'
            ',SKU-3,,Grocery,,,85,120,0,50,15,ltr,yes'       # no name -> error
        )
        summary = summarise(rows_from(body))
        self.assertEqual(
            summary,
            {'total': 3, 'valid': 2, 'errors': 1, 'warnings': 0, 'new': 1, 'updates': 1},
        )


class CommitTests(TestCase):
    def setUp(self):
        self.user = make_user('store', Role.INVENTORY)
        self.category = make_category('Grocery')

    def test_new_products_are_created_with_recorded_opening_stock(self):
        rows = rows_from(f'{HEADER}\nRice 5kg,SKU-1,880123,Grocery,,Chashi,520,650,0,100,20,pack,yes')
        result = commit_rows(rows, self.user)

        product = Product.objects.get(sku='SKU-1')
        self.assertEqual(result, {'created': 1, 'updated': 0, 'stocked': 1})
        self.assertEqual(product.stock_quantity, 100)
        self.assertEqual(product.selling_price, Decimal('650.00'))
        movement = product.movements.get()
        self.assertEqual(movement.reason, StockMovement.Reason.OPENING)
        self.assertEqual(movement.balance_after, 100)
        self.assertEqual(movement.created_by, self.user)

    def test_existing_products_are_updated_without_touching_stock(self):
        """An import changes the catalogue, not the inventory ledger."""
        product = make_product(sku='SKU-1', price='650.00', category=self.category, stock=42)
        rows = rows_from(f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,520,700,0,999,25,pc,yes')
        result = commit_rows(rows, self.user)

        product.refresh_from_db()
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['created'], 0)
        self.assertEqual(product.selling_price, Decimal('700.00'))
        self.assertEqual(product.min_stock_level, 25)
        self.assertEqual(product.stock_quantity, 42)   # untouched by the import

    def test_invalid_rows_are_skipped_not_guessed_at(self):
        body = (
            f'{HEADER}\n'
            'Good,SKU-1,,Grocery,,,520,650,0,10,20,pc,yes\n'
            ',SKU-2,,Grocery,,,520,650,0,10,20,pc,yes'       # no name -> skipped
        )
        result = commit_rows(rows_from(body), self.user)
        self.assertEqual(result['created'], 1)
        self.assertEqual(Product.objects.count(), 1)

    def test_missing_category_and_supplier_are_created_on_request(self):
        body = f'{HEADER}\nRice,SKU-1,,Bakery,New Supplier Ltd,,520,650,0,5,10,pc,yes'
        commit_rows(rows_from(body, create_missing=True), self.user, create_missing=True)
        self.assertTrue(Category.objects.filter(name='Bakery').exists())
        self.assertTrue(Supplier.objects.filter(name='New Supplier Ltd').exists())
        self.assertEqual(Product.objects.get(sku='SKU-1').category.name, 'Bakery')

    def test_the_whole_import_is_one_transaction(self):
        """If any row fails part-way through, none of the batch is kept.

        The failure is injected rather than provoked with an over-long value,
        because SQLite does not enforce VARCHAR lengths -- this way the test
        checks the rollback on every backend.
        """
        rows = rows_from(
            f'{HEADER}\n'
            'A,SKU-1,,Grocery,,,10,20,0,5,5,pc,yes\n'
            'B,SKU-2,,Grocery,,,10,20,0,5,5,pc,yes'
        )
        with mock.patch('pos.imports.adjust_stock',
                        side_effect=[None, RuntimeError('database went away')]):
            with self.assertRaises(RuntimeError):
                commit_rows(rows, self.user)
        self.assertEqual(Product.objects.count(), 0)


class ImportViewTests(TestCase):
    def setUp(self):
        self.manager = make_user('mgr', Role.MANAGER)
        self.cashier = make_user('till', Role.CASHIER)
        make_category('Grocery')
        self.client.force_login(self.manager)

    def test_the_page_renders(self):
        self.assertEqual(self.client.get(reverse('pos:product_import')).status_code, 200)

    def test_a_cashier_cannot_import(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse('pos:product_import')).status_code, 403)

    def test_template_downloads_as_csv(self):
        response = self.client.get(reverse('pos:product_import_template'))
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('name,sku', response.content.decode())

    def test_upload_previews_without_saving_anything(self):
        response = self.client.post(
            reverse('pos:product_import'),
            {
                'file': csv_file(f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,520,650,0,10,20,pc,yes'),
                'update_existing': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['valid'], 1)
        self.assertEqual(Product.objects.count(), 0)     # preview only

    def test_confirm_writes_the_previewed_rows(self):
        self.client.post(
            reverse('pos:product_import'),
            {
                'file': csv_file(f'{HEADER}\nRice 5kg,SKU-1,,Grocery,,,520,650,0,10,20,pc,yes'),
                'update_existing': 'on',
            },
        )
        self.client.post(reverse('pos:product_import_confirm'))
        product = Product.objects.get(sku='SKU-1')
        self.assertEqual(product.name, 'Rice 5kg')
        self.assertEqual(product.stock_quantity, 10)

    def test_confirm_without_a_preview_is_refused(self):
        response = self.client.post(reverse('pos:product_import_confirm'), follow=True)
        self.assertContains(response, 'expired')
        self.assertEqual(Product.objects.count(), 0)

    def test_a_bad_file_reports_an_error(self):
        response = self.client.post(
            reverse('pos:product_import'),
            {'file': csv_file('nothing,useful\n1,2')},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['summary'])


class BulkScaleTests(TestCase):
    """Section 52: the importer must cope with a real catalogue load."""

    def setUp(self):
        self.user = make_user('store', Role.INVENTORY)
        self.category = make_category('Grocery')

    def _file(self, count, first=1):
        lines = '\n'.join(
            f'Product {i},SKU-BULK-{i},,Grocery,,Brand,{10 + i % 7},'
            f'{50 + i % 11},{i % 3 * 5},{i % 40},5,pc,yes'
            for i in range(first, first + count)
        )
        return f'{HEADER}\n{lines}'

    def _import(self, count, first=1, **options):
        rows = rows_from(self._file(count, first), **options)
        self.assertTrue(all(r.is_valid for r in rows),
                        msg=next((r.message for r in rows if not r.is_valid), ''))
        return commit_rows(rows, self.user, **options)

    def test_ten_products(self):
        self.assertEqual(self._import(10)['created'], 10)
        self.assertEqual(Product.objects.count(), 10)

    def test_one_hundred_products(self):
        self.assertEqual(self._import(100)['created'], 100)
        self.assertEqual(Product.objects.count(), 100)

    def test_five_hundred_products(self):
        result = self._import(500)
        self.assertEqual(result['created'], 500)
        self.assertEqual(Product.objects.count(), 500)
        # Opening stock was applied through the stock service, not written raw.
        self.assertEqual(
            StockMovement.objects.filter(reason=StockMovement.Reason.OPENING).count(),
            result['stocked'],
        )

    def test_one_thousand_products(self):
        result = self._import(1000)
        self.assertEqual(result['created'], 1000)
        self.assertEqual(Product.objects.count(), 1000)
        self.assertEqual(Product.objects.filter(sku='SKU-BULK-1000').count(), 1)

    def test_reimporting_the_same_file_creates_no_duplicates(self):
        self._import(200)
        second = self._import(200)
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['updated'], 200)
        self.assertEqual(Product.objects.count(), 200)

    def test_a_second_batch_adds_to_the_first(self):
        self._import(100)
        self._import(100, first=101)
        self.assertEqual(Product.objects.count(), 200)

    def test_prices_discounts_and_stock_survive_the_round_trip(self):
        self._import(50)
        # Row 7: cost 10+7%7=10, price 50+7%11=57, discount 7%3*5=5, stock 7%40=7
        product = Product.objects.get(sku='SKU-BULK-7')
        self.assertEqual(product.cost_price, Decimal('10.00'))
        self.assertEqual(product.selling_price, Decimal('57.00'))
        self.assertEqual(product.discount_percent, Decimal('5.00'))
        self.assertEqual(product.stock_quantity, 7)

    def test_existing_products_are_not_corrupted_by_a_failed_batch(self):
        """A batch that blows up half way leaves earlier imports untouched."""
        self._import(20)
        before = Product.objects.count()
        before_names = set(Product.objects.values_list('name', flat=True))

        rows = rows_from(self._file(5, first=100))
        with mock.patch('pos.imports.adjust_stock',
                        side_effect=[None, None, RuntimeError('disk full')]):
            with self.assertRaises(RuntimeError):
                commit_rows(rows, self.user)

        self.assertEqual(Product.objects.count(), before)
        self.assertEqual(set(Product.objects.values_list('name', flat=True)), before_names)


class StorefrontExportTests(TestCase):
    """A catalogue exported from a shop front: no SKU, no cost, image links,
    and both a list price and an already-discounted price."""

    HEADER = ('id,name,category,subcategory,brand,price,old_price,'
              'discount_percent,final_price,stock,rating,review_count,image_url')

    def setUp(self):
        self.user = make_user('store', Role.INVENTORY)

    def _rows(self, body, **options):
        options.setdefault('create_missing', True)
        return rows_from(f'{self.HEADER}\n{body}', **options)

    def test_list_price_becomes_the_selling_price(self):
        """old_price is pre-discount; using `price` would discount twice."""
        rows = self._rows(
            '2,Spring Roll,Frozen,Snacks,Paragon,262,320,18,262,20,3.8,22,'
            'https://cdn.example.com/roll.webp'
        )
        data = rows[0].data
        self.assertTrue(rows[0].is_valid, rows[0].message)
        self.assertEqual(data['selling_price'], Decimal('320.00'))
        self.assertEqual(data['discount_percent'], Decimal('18.00'))

    def test_the_till_price_matches_the_files_final_price(self):
        rows = self._rows(
            '30,Water Bottle,Kitchen,Storage,EV,105,210,50,105,60,4.5,61,'
            'https://cdn.example.com/b.webp'
        )
        commit_rows(rows, self.user, create_missing=True)
        product = Product.objects.get(sku='SKU-30')
        self.assertEqual(product.selling_price, Decimal('210.00'))
        self.assertEqual(product.final_price, Decimal('105.00'))

    def test_rounded_source_discounts_warn_but_still_import(self):
        rows = self._rows(
            "15,Sunflower Oil,Oil,Sunflower,King's,1800,2100,14,1800,30,4.5,48,"
            'https://cdn.example.com/o.webp'
        )
        row = rows[0]
        self.assertTrue(row.is_valid)          # not blocked
        self.assertTrue(row.has_warning)
        self.assertIn('1800', row.warning_message)
        self.assertEqual(summarise(rows)['warnings'], 1)

    def test_image_link_is_stored(self):
        url = 'https://cdn.example.com/photo.webp'
        rows = self._rows(f'1,Chola,Dal,Packed,BPM,67,67,0,67,40,4.5,38,{url}')
        commit_rows(rows, self.user, create_missing=True)
        self.assertEqual(Product.objects.get(sku='SKU-1').image_url, url)

    def test_a_non_http_image_link_is_rejected(self):
        rows = self._rows(
            '1,Chola,Dal,Packed,BPM,67,67,0,67,40,4.5,38,javascript:alert(1)'
        )
        self.assertFalse(rows[0].is_valid)
        self.assertIn('not an http(s) URL', rows[0].message)

    def test_sku_is_generated_from_the_source_id(self):
        rows = self._rows('7,Essence,Baking,Flavors,FC,140,140,0,140,60,4.8,69,')
        self.assertEqual(rows[0].data['sku'], 'SKU-7')
        self.assertTrue(rows[0].generated_sku)

    def test_generated_skus_are_stable_so_reimport_updates(self):
        """The whole point: importing the same file twice must not duplicate."""
        body = ('1,Chola,Dal,Packed,BPM,67,67,0,67,40,4.5,38,\n'
                '2,Roll,Frozen,Snacks,Paragon,262,320,18,262,20,3.8,22,')
        first = commit_rows(self._rows(body), self.user, create_missing=True)
        self.assertEqual(first['created'], 2)

        second_rows = self._rows(body)
        self.assertTrue(all(r.action == 'update' for r in second_rows))
        second = commit_rows(second_rows, self.user, create_missing=True)
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['updated'], 2)
        self.assertEqual(Product.objects.count(), 2)

    def test_reimport_does_not_add_stock_again(self):
        body = '1,Chola,Dal,Packed,BPM,67,67,0,67,40,4.5,38,'
        commit_rows(self._rows(body), self.user, create_missing=True)
        commit_rows(self._rows(body), self.user, create_missing=True)
        self.assertEqual(Product.objects.get(sku='SKU-1').stock_quantity, 40)

    def test_missing_cost_defaults_to_zero_without_blocking(self):
        rows = self._rows('1,Chola,Dal,Packed,BPM,67,67,0,67,40,4.5,38,')
        self.assertTrue(rows[0].is_valid, rows[0].message)
        self.assertEqual(rows[0].data['cost_price'], Decimal('0.00'))

    def test_unknown_extra_columns_are_ignored(self):
        """rating, review_count and subcategory are not model fields."""
        rows = self._rows('1,Chola,Dal,Packed,BPM,67,67,0,67,40,4.5,38,')
        self.assertTrue(rows[0].is_valid)
        self.assertNotIn('rating', rows[0].data)

    def test_categories_are_created_and_reused(self):
        body = ('1,A,Dal or Lentil,x,B,10,10,0,10,5,4,1,\n'
                '2,B,Dal or Lentil,x,B,10,10,0,10,5,4,1,\n'
                '3,C,Frozen,x,B,10,10,0,10,5,4,1,')
        commit_rows(self._rows(body), self.user, create_missing=True)
        self.assertEqual(Category.objects.filter(name='Dal or Lentil').count(), 1)
        self.assertEqual(Category.objects.filter(name='Frozen').count(), 1)

    def test_the_real_fifty_product_file_imports_cleanly(self):
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent.parent / 'sample-data' / 'products_50.csv'
        rows = rows_from(path.read_text(encoding='utf-8'), create_missing=True)
        summary = summarise(rows)
        self.assertEqual(summary['total'], 50)
        self.assertEqual(summary['errors'], 0)

        result = commit_rows(rows, self.user, create_missing=True)
        self.assertEqual(result['created'], 50)
        self.assertEqual(Product.objects.count(), 50)
        self.assertEqual(Product.objects.exclude(image_url='').count(), 50)


class UploadPageDefaultsTests(TestCase):
    """Uploading a fresh catalogue must work without changing any option.

    Regression: `create_missing` used to default off, so every row of a new
    file failed with "Category does not exist" and nothing could be imported.
    """

    NEW_CSV = (
        'id,name,category,brand,price,old_price,discount_percent,stock,image_url\n'
        '101,Rice Cooker,Home Appliance,Walton,3200,3800,16,12,https://example.com/a.webp\n'
        '102,Ceiling Fan,Home Appliance,Vision,4100,4500,9,8,https://example.com/b.webp\n'
        '103,LED TV,Electronics Large,Marcel,21500,24000,10,5,https://example.com/c.webp\n'
    )

    def setUp(self):
        self.manager = make_user('mgr', Role.MANAGER)
        self.client.force_login(self.manager)

    def test_create_missing_is_ticked_when_the_page_loads(self):
        response = self.client.get(reverse('pos:product_import'))
        self.assertTrue(response.context['form'].fields['create_missing'].initial)

    def _upload(self, **extra):
        data = {'file': csv_file(self.NEW_CSV, 'new.csv'), 'create_missing': 'on',
                'update_existing': 'on'}
        data.update(extra)
        return self.client.post(reverse('pos:product_import'), data)

    def test_a_brand_new_catalogue_imports_with_default_options(self):
        response = self._upload()
        summary = response.context['summary']
        self.assertEqual(summary['total'], 3)
        self.assertEqual(summary['valid'], 3)
        self.assertEqual(summary['errors'], 0)

        self.client.post(reverse('pos:product_import_confirm'))
        self.assertEqual(Product.objects.count(), 3)
        self.assertTrue(Category.objects.filter(name='Home Appliance').exists())
        self.assertTrue(Category.objects.filter(name='Electronics Large').exists())

    def test_every_detail_from_the_file_is_stored(self):
        self._upload()
        self.client.post(reverse('pos:product_import_confirm'))
        product = Product.objects.get(sku='SKU-101')
        self.assertEqual(product.name, 'Rice Cooker')
        self.assertEqual(product.brand, 'Walton')
        self.assertEqual(product.category.name, 'Home Appliance')
        self.assertEqual(product.selling_price, Decimal('3800.00'))
        self.assertEqual(product.discount_percent, Decimal('16.00'))
        self.assertEqual(product.stock_quantity, 12)
        self.assertEqual(product.image_url, 'https://example.com/a.webp')

    def test_unticking_create_missing_explains_what_to_do(self):
        response = self._upload(create_missing='')
        self.assertEqual(response.context['summary']['valid'], 0)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('Create categories' in m for m in messages),
            msg=f'no actionable message, got: {messages}',
        )

    def test_the_preview_shows_the_price_the_till_will_charge(self):
        response = self._upload()
        rows = response.context['rows']
        # 3800 list less 16% = 3192
        self.assertEqual(rows[0].final_price, Decimal('3192.00'))
        self.assertContains(response, 'Sells at')
        self.assertContains(response, 'Supplier')
