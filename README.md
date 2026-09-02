# O'dell Tech Shopping — Point of Sale System

A Django-based Point of Sale system for **O'dell Tech Shopping**, built to the
*Business Requirement Specification for a Point of Sale (POS) System* (25 August 2026). It covers the full retail counter
workflow — search, cart, discount, tax, payment, invoice, inventory update — plus
products, inventory, suppliers, purchases, customers, employees, returns and reporting,
all behind role-based access control.

---

## Contents

- [Features](#features)
- [Technology stack](#technology-stack)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Database setup](#database-setup)
- [Environment configuration](#environment-configuration)
- [Running the server](#running-the-server)
- [Demo data and sign-in accounts](#demo-data-and-sign-in-accounts)
- [User roles and permissions](#user-roles-and-permissions)
- [Registration and approval](#registration-and-approval)
- [Product Entry Automation](#product-entry-automation)
- [The customer number workflow](#the-customer-number-workflow)
- [Reports and the group chart](#reports-and-the-group-chart)
- [Employees, IDs and passwords](#employees-ids-and-passwords)
- [How the money is calculated](#how-the-money-is-calculated)
- [Testing](#testing)
- [Implementation decisions](#implementation-decisions)
- [Troubleshooting](#troubleshooting)

---

## Features

| BRS module | Status | Where |
|---|---|---|
| Authentication (BR-001) | Login, logout, sessions, failed-attempt logging | `pos/views/auth.py` |
| Self-registration | Sign-up from the login page, Cashier role, approval queue | `pos/forms.py` |
| Role-based access (BR-002) | 4 roles, capability map, nav filtered per role | `pos/permissions.py` |
| Products (BR-003–005) | CRUD, SKU/barcode, search, activate/deactivate | `pos/views/catalog.py` |
| Product Entry Automation | CSV/Excel bulk import, preview, warnings, images | `pos/imports.py` |
| Product images | `image_url` shown beside the name on every screen | `pos/models.py` |
| Categories (BR-006) | CRUD, category-wise products | `pos/views/catalog.py` |
| Inventory (BR-007–010) | Stock ledger, adjustments, low/out-of-stock alerts | `pos/views/inventory.py` |
| Suppliers & purchases (BR-011–012) | Supplier records, purchase receipt raises stock | `pos/services.py` |
| Sales & cart (BR-013–014) | Live POS terminal, barcode scan, cart editing | `pos/views/terminal.py` |
| Pricing (BR-015–018) | Product discount → subtotal → customer discount → tax | `pos/services.py` |
| Customer number workflow | Number-only checkout, automatic returning-customer detection | `pos/customers.py` |
| Reports | Daily, Monthly, Yearly, Customer, Inventory, Product Performance | `pos/reporting.py` |
| Group chart | Inline-SVG grouped bar chart on every report, real data | `pos/charts.py` |
| Employee IDs | Generated server-side, never typed, unique under concurrency | `pos/models.py` |
| Change password | Django's password change, session preserved | `pos/views/auth.py` |
| Payments (BR-019–021) | Cash/card/mobile/bank, change calculation | `pos/services.py` |
| Invoices (BR-022–023) | Printable receipt, searchable history | `pos/views/sales.py` |
| Customers (BR-024–025) | CRUD, purchase history | `pos/views/people.py` |
| Discounts (BR-026–027) | Percent/fixed, order/product/category, cashier ceiling | `pos/models.py` |
| Returns & refunds (BR-028–029) | Invoice lookup, part returns, refund, restock | `pos/services.py` |
| Employees (BR-030–031) | Employee records, activity log | `pos/views/people.py` |
| Reports (BR-032–036) | Sales, monthly, inventory, product, customer + CSV | `pos/views/reports.py` |
| Dashboard (§21) | Role-aware metrics, recent transactions, alerts | `pos/views/dashboard.py` |

All twelve business rules (BRL-1 … BRL-12) are enforced in `pos/services.py` and
covered by tests — see [Testing](#testing).

---

## Technology stack

- **Python** 3.13, **Django** 6.1
- **MySQL** 8.4 (database `pos_system`), via `mysqlclient`
- **Frontend**: server-rendered Django templates, hand-written CSS, vanilla JavaScript
- **Config**: `python-dotenv`
- **Spreadsheets**: `openpyxl` (Excel import only)

No CDN, no build step, no JavaScript framework — the app runs offline.

---

## Project structure

```
Pop System/
├── config/                 Django project (settings, root URLs, WSGI/ASGI)
├── pos/                    The application — all POS functionality
│   ├── models.py           16 entities (BRS §24)
│   ├── services.py         Business logic: stock, sales, returns, purchases
│   ├── imports.py          Product Entry Automation (CSV/Excel bulk import)
│   ├── customers.py        Customer numbers, matching and loyalty eligibility
│   ├── reporting.py        Report aggregation (database-side)
│   ├── charts.py           Grouped-bar chart geometry (inline SVG)
│   ├── permissions.py      Capability map + @require decorator + navigation
│   ├── forms.py            Server-side validation
│   ├── urls.py             All routes
│   ├── views/              auth, dashboard, terminal, catalog, inventory,
│   │                       sales, people, reports, config, errors
│   ├── templates/pos/      47 templates
│   ├── static/pos/         app.css, terminal.js
│   ├── management/commands/seed_demo.py
├── sample-data/            products_50.csv - a real storefront export
│   └── tests/              281 tests
├── pop/                    Unused stub app (left untouched, not installed)
├── manage.py
├── .env                    Local secrets — not committed
└── .env.example            Template for a new environment
```

**One app, not eight.** The BRS modules are tightly coupled (sales ↔ inventory ↔
products); splitting them into separate Django apps would create circular imports for
no benefit. Modularity comes from the `views/` package and the services layer instead.

---

## Installation

```bash
# From the project root, with the bundled virtual environment
venv/Scripts/activate            # Windows
# source venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

---

## Database setup

The project runs on **either** backend, chosen by `DB_ENGINE` in `.env`:

| `DB_ENGINE` | Needs a server? | Use it for |
|---|---|---|
| `sqlite` | **No** | Running the project anywhere with zero setup — a fresh clone, a laptop, a demo, anyone you send it to |
| `mysql` *(currently in use)* | Yes, MySQL 8.0.11+ | The shared `pos_system` database |

> **This project is currently set to `mysql`**, pointing at `pos_system` on
> MySQL 8.4 at `127.0.0.1:3307`. `run.ps1` starts that server automatically.

### SQLite — nothing to install

```powershell
python manage.py migrate
python manage.py runserver
```

That's it. `db.sqlite3` lives in the project folder and is committed, so the app
starts with the real catalogue and sales history already in place.

### MySQL

Set `DB_ENGINE=mysql` in `.env`, then make sure the server is running.

> **XAMPP will not work.** Django 6.1 requires MySQL 8.0.11+ or MariaDB 10.11+, and
> XAMPP ships MariaDB 10.4:
>
> ```
> django.db.utils.NotSupportedError: MariaDB 10.11 or later is required (found 10.4.32).
> ```
>
> On this machine MySQL 8.4 is installed separately at `C:\Users\Asus\mysql84`
> and listens on **3307**, leaving XAMPP's 3306 untouched.

Start it:

```powershell
& "C:\Users\Asus\mysql84\bin\mysqld.exe" --defaults-file=C:\Users\Asus\mysql84\my.ini
```

It is **not** registered as a Windows service, so it does not survive a reboot. If
Django reports `Can't connect to server on '127.0.0.1' (10061)`, that is why — either
start it with the command above, or install it as a service once (admin PowerShell):

```powershell
& "C:\Users\Asus\mysql84\bin\mysqld.exe" --install MySQL84 --defaults-file=C:\Users\Asus\mysql84\my.ini
net start MySQL84
```

Create the database and user (once):

```sql
CREATE DATABASE pos_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'pos_user'@'127.0.0.1' IDENTIFIED BY 'your-password';
GRANT ALL PRIVILEGES ON pos_system.* TO 'pos_user'@'127.0.0.1';
GRANT ALL PRIVILEGES ON `test_pos_system`.* TO 'pos_user'@'127.0.0.1';
FLUSH PRIVILEGES;
```

### Moving data between the two

```powershell
# MySQL -> SQLite
$env:PYTHONUTF8=1
$env:DB_ENGINE='mysql';  python manage.py dumpdata --natural-foreign --natural-primary `
    --exclude contenttypes --exclude auth.permission --exclude admin.logentry `
    --exclude sessions.session --indent 1 -o data.json
$env:DB_ENGINE='sqlite'; python manage.py migrate; python manage.py loaddata data.json
```

`PYTHONUTF8=1` is required: the ৳ currency symbol cannot be written by Windows'
default cp1252 codepage, and `dumpdata` fails with a misleading MySQL error without it.

### The MySQL system schema is off limits

`settings.py` refuses to start if `DB_NAME` is pointed at `mysql`,
`information_schema`, `performance_schema` or `sys`.

---

---

## Environment configuration

Secrets live in `.env` (git-ignored). Copy `.env.example` to `.env` and fill it in:

```ini
DJANGO_SECRET_KEY=replace-me-with-a-50-char-random-string
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_TIME_ZONE=Asia/Dhaka

DB_NAME=pos_system
DB_USER=pos_user
DB_PASSWORD=your-password
DB_HOST=127.0.0.1
DB_PORT=3307
```

For production set `DJANGO_DEBUG=False` and a fresh `DJANGO_SECRET_KEY`; secure cookies,
HSTS and SSL redirect switch on automatically.

---

## Running the server

```bash
python manage.py runserver
```

Open <http://127.0.0.1:8000/> — you land on the login screen.
Django admin, for maintenance only, is at `/admin/`.

---

## Demo data and sign-in accounts

```bash
python manage.py seed_demo              # 40 sample sales
python manage.py seed_demo --sales 100  # more history
```

Creates 5 users, 5 categories, 3 suppliers, 18 products (with deliberate low-stock and
out-of-stock items), 5 customers, 2 discounts, and sales spread over the last 45 days.
Re-running is safe: records are updated in place, and sample sales are skipped once the
store has traded.

| Username | Password | Role |
|---|---|---|
| `admin` | `Pos@12345` | Administrator |
| `manager` | `Pos@12345` | Manager |
| `cashier` | `Pos@12345` | Cashier |
| `cashier2` | `Pos@12345` | Cashier |
| `stock` | `Pos@12345` | Inventory Staff |

> These are development credentials. Change them before any real deployment.

---

## User roles and permissions

Capabilities are named strings granted per role in `pos/permissions.py`. Views declare
what they need with `@require(CAPABILITY)`; the sidebar is generated from the same map,
so a user is never shown a link they cannot open.

| | Administrator | Manager | Cashier | Inventory Staff |
|---|:---:|:---:|:---:|:---:|
| POS terminal / make sales | ✓ | ✓ | ✓ | — |
| See all transactions | ✓ | ✓ | own only | — |
| Void a sale | ✓ | ✓ | — | — |
| Process returns | ✓ | ✓ | ✓ | — |
| Manage products | ✓ | ✓ | — | ✓ |
| Bulk-import products | ✓ | ✓ | — | ✓ |
| **Change prices** (BRL-4) | ✓ | ✓ | — | — |
| Categories | ✓ | ✓ | — | ✓ |
| Inventory & stock adjustments | ✓ | ✓ | — | ✓ |
| Suppliers & purchases | ✓ | ✓ | — | ✓ |
| Customers | ✓ | ✓ | ✓ | — |
| Discounts | ✓ | ✓ | — | — |
| Employees | ✓ | ✓ | — | — |
| Reports | ✓ | ✓ | — | ✓ |
| Store settings | ✓ | — | — | — |

Unauthorised access returns a styled 403 page, never a traceback.

---

## Registration and approval

The login page offers **Create an account** so staff can sign themselves up.

An open registration form on a POS system is a privilege-escalation risk, so two
things are non-negotiable in `RegistrationForm`:

1. **The role is never read from the submitted form.** A self-registered account is
   always a **Cashier** — the lowest-privilege role. Posting `role=ADMIN`,
   `is_staff=on` or `is_superuser=on` changes nothing; there is a test for exactly
   that.
2. **New accounts are disabled until approved.** They appear under
   *Employees → Awaiting approval*, where an Administrator or Manager clicks
   **Approve**. Until then the account cannot sign in.

Both behaviours are switches under **Settings**:

| Setting | Default | Effect |
|---|---|---|
| `allow_self_registration` | On | Shows the sign-up link on the login page |
| `require_registration_approval` | On | New accounts start disabled |

Turning approval off gives instant access on sign-up. Only do that on a trusted
network — anyone who can reach the login page can then create a working till account.

Anyone needing Manager, Inventory Staff or Administrator rights must be created (or
promoted) by an Administrator through **Employees → Add Employee**.

---

## Product Entry Automation

Bulk-load the catalogue from a spreadsheet instead of typing products one at a time:
**Products → Import Products**.

The flow is deliberately two-step — *nothing is written until you approve the preview*:

1. **Upload** a `.csv` or `.xlsx` file (up to 2000 rows, 5 MB).
2. **Preview** every row with its photo, brand, category, supplier, cost, list
   price, discount, the price the till will charge, stock and unit — marked
   `New`, `Update`, `Warning` or `Error` with the reason.
3. **Confirm**, and all valid rows are written in a single transaction.

**Create categories and suppliers that do not exist yet** is ticked by default. A new
catalogue almost always brings new categories, and with that option off every one of
those rows fails validation — which looks exactly like the import not working at all.
4. An **import result** page reports Total / Added / Updated / Skipped / Failed, lists
   every skipped row with its reason, and offers a **Download Error Report** CSV.

Rows with errors are skipped, never guessed at. Click **Download template** for a
starter file with the expected columns.

### Columns

`name`, `sku`, `category`, `cost_price` and `selling_price` are required. Optional:
`barcode`, `supplier`, `brand`, `discount_percent`, `stock_quantity`,
`min_stock_level`, `unit`, `is_active`.

Column order does not matter, and common alternative headings are recognised
automatically — `Product`, `Item`, `Code`, `Qty`, `MRP`, `Sale price`, `Reorder
level`, `EAN`, and others. Comma, semicolon and tab delimiters all work.

### Rules the importer enforces

- Every row is validated with the same rules as the add-product form, including
  *selling price may not be below cost* — and a discount that would push the price
  below cost is rejected too.
- `discount_percent` sets the **product-level** discount (0–100). Customer discounts
  are never touched by an import: they only happen at the till.
- Duplicate SKUs or barcodes **within the file** are reported with the clashing row
  number; a barcode already belonging to another product is rejected by name.
- An SKU that already exists is treated as an **update**, not a clash — which is what
  makes re-importing a supplier price list work. Untick *Update products that already
  have this SKU* to have those rows flagged as errors instead.
- Categories and suppliers must already exist, unless you tick *Create categories and
  suppliers that do not exist yet*.
- **`stock_quantity` applies to new products only**, and goes through the normal stock
  service so it is recorded as an `OPENING` movement. Importing over an existing
  product never silently rewrites its stock: an import changes the catalogue, not the
  inventory ledger. Use a stock adjustment or a purchase for that.

Requires the *Manage products* capability — Administrator, Manager or Inventory Staff.

---

## How the money is calculated

Two discounts, applied in a fixed order that is documented in `pos/services.price_cart`
so it cannot drift:

```
product selling price
  → product-level discount      (Product.discount_percent)
  → product final price
  → cart subtotal
  → existing-customer discount  (2%, once, on the already-discounted amount)
  → tax                         (on the net amount)
  → final total
```

Worked example (5% and 10% product discounts, returning customer, no tax):

```
Product A   1000 × 1,  5% off  =  950
Product B   2000 × 1, 10% off  = 1800
Subtotal                        = 2750
Customer discount (2%)          =   55
Net before tax                  = 2695
```

The customer discount is applied **exactly once**, after product discounts, and never
compounds with them. There is a test for precisely this.

### What the browser is not allowed to decide

- **There is no cart-level discount.** The POS screen has no discount input at all, and
  `CheckoutForm` has no discount field. Extra `discount_value` / `order_discount`
  parameters posted by hand are ignored — there is a test that proves it.
- **Loyalty is decided server-side.** The till calls `/pos/api/customer/` to *show* the
  cashier whether a number is a returning customer, but `create_sale()` re-checks
  against real sales history before pricing anything. A cashier cannot enable, disable
  or change the discount.
- **Eligibility means a real purchase**, not merely a Customer row existing: it is based
  on non-void completed sales, so a voided sale does not earn loyalty.

Stock is still only ever changed through `pos.services.adjust_stock()`, which writes a
`StockMovement` row with the resulting balance (BRL-8, BRL-9). Sales lock product rows
with `select_for_update()` inside `transaction.atomic()` (BRL-3).

---

## The customer number workflow

The till asks for a **Customer Number** and nothing else — no dropdown, no name, no
email.

1. The cashier types the number (`01XXXXXXXXX`).
2. A live lookup reports **Existing customer — N previous orders, 2% applied** or
   **New customer — no customer discount**.
3. On checkout the server re-derives that from the database and prices accordingly.
4. If no Customer row exists yet, one is created from the number alone.

`pos/customers.py` normalises every accepted spelling — `01712345678`,
`+8801712345678`, `8801712345678`, `017-1234 5678` — to one canonical local form, and
matches existing rows under *any* stored spelling. That is what stops a second customer
record being created for someone already in the database under the older `+880…`
format.

### Privacy

Customer name, email and address are never shown on the customer dashboard, the
customer report, or the invoice. Those screens show the **customer number** and spend
only. The fields still exist on the model and keep their historical values — they are
simply not displayed. There are tests asserting a name and email set on a customer do
*not* appear on the invoice, dashboard or report.

---

## Reports

`Reports → Daily · Monthly · Yearly · Customer` (plus the existing Inventory and Product
Performance reports).

| Report | Parameters | Shows |
|---|---|---|
| Daily | date | Transactions, items, product/customer discounts, tax, paid, due, returns, net, payment breakdown, top products, by cashier |
| Monthly | month + year | The same totals plus best sellers, sales by category and by employee, day by day |
| Yearly | year | Annual totals with a month-by-month table (all 12 months, including quiet ones) |
| Customer | From + To date, optional number | Per customer number: orders, items, total shopping, discount, paid, due, refunds, net purchase |

Every date range is **inclusive of both ends**, and a From date after the To date is
rejected. All figures are aggregated in the database (`Sum`, `Count`, `Coalesce`,
`TruncMonth`) — no queryset is pulled into Python to be summed in a loop, and the
customer report resolves item counts in one extra grouped query rather than one per
customer. Void sales are excluded everywhere. All four export to CSV.

---

---

## Bulk activate / deactivate

The Products tab has a checkbox on every row plus a **select-all** box in the header.
Ticking anything reveals an action bar with **Activate** and **Deactivate**.

Two scopes:

- **Selected** — the rows you ticked on this page.
- **All matching this filter** — offered once the whole page is ticked and more rows
  match. It acts on every product the current search/category/status filter returns,
  not just the visible page.

The filter scope is re-evaluated on the server from the same helper the listing uses, so
a tampered page number or a stale tick cannot widen what gets changed. The endpoint is
POST-only, CSRF-protected and needs the *Manage products* capability — a cashier sees no
checkboxes at all and is refused a 403 if they call it directly.

Both destructive-feeling paths confirm first: deactivating anything, and activating a
whole filter (which would otherwise silently restore products archived on purpose).
The success message counts only rows that actually changed, so re-running an action
reports "Nothing to do" rather than a misleading number.

## Employees, IDs and passwords

### Employee IDs are generated, never typed

`User.save()` assigns the next free `EMP-###` on first save, so every creation path —
self-registration, the admin's Add Employee form, the Django admin, the seeder, the
shell — gets one automatically. The field is `editable=False`, which means Django
*refuses* to build a form containing it: submitting `employee_id=EMP-999` cannot have
any effect, and there is a test for that.

Allocation reads the highest existing number under `select_for_update()` and retries on
`IntegrityError`, with the column's unique constraint as the backstop. A test creates
eight accounts from parallel threads and asserts eight distinct IDs.

Existing IDs are never changed or renumbered. `EMP-001`–`EMP-005` kept their values, and
migration `0006` only filled in the two accounts that had none.

### Change password

**Every role** — Administrator, Manager, Cashier and Inventory Staff — can change their
own password. The link is at the bottom of the sidebar, under the signed-in user's name
and employee ID, and is also on the header chip. There is no capability gate: the view is
`@login_required` only. It uses Django's `PasswordChangeForm`, so the current password is
verified and the new one goes through the configured validators and hashers;
`update_session_auth_hash` keeps the user signed in afterwards.

---

## Reports and the group chart

`Reports` expands in the sidebar into all six reports, and every report page carries the
same tab strip:

```
Reports
├── Daily Report          /reports/daily/
├── Monthly Report        /reports/monthly/
├── Yearly Report         /reports/yearly/
├── Customer Report       /reports/customer/
├── Inventory Report      /reports/inventory/
└── Product Performance   /reports/products/
```

### The navigation bug that was fixed

`/reports/` used to be a page whose entire content was a table of links to the other
reports, and Inventory Report and Product Performance were the only two pages without
the tab strip — so those two looked like dead ends while the landing page looked like a
menu that had been rendered as content.

The fix was structural, not cosmetic: `/reports/` is now a genuine overview (headline
figures plus the sales-by-month chart) with the shortcut table removed, the tab strip is
included by **all** report templates, and the sidebar gained a submenu so each report is
one click away. A test asserts no report page contains the old shortcut block, that each
URL renders its own `<h1>`, and that Inventory and Product Performance show their own
content.

### The group chart

Each report carries one grouped bar chart appropriate to that page:

| Page | Chart |
|---|---|
| Overview | Sales vs net sales, by month |
| Daily | Revenue by product |
| Monthly | Sales by category |
| Yearly | Sales vs net sales, by month |
| Customer | Top customers: total shopping vs discount |
| Inventory | Stock value by category, cost vs retail |
| Product Performance | Top products: net revenue vs discount |

Geometry is computed in `pos/charts.py` from the same querysets the tables use, and
rendered as **inline SVG** — no chart library, no CDN, works offline, and prints. The
chart honours the page's date/category filter and disappears entirely when a range has
no data, rather than drawing an empty box. Values come from the database; JavaScript is
not involved at all.

---

## Testing

```bash
python manage.py test pos              # all 281 tests
python manage.py test pos.tests.test_sales -v 2
```

| File | Covers |
|---|---|
| `test_auth.py` | Login, invalid credentials, role escalation, per-role page access, price-edit restriction |
| `test_sales.py` | Pricing (BRS worked examples), stock deduction, insufficient stock, payment validation, atomicity, duplicate submission, invoice uniqueness, cashier visibility |
| `test_inventory.py` | Stock in/out, low & out-of-stock detection, product validation, duplicate SKU, purchases |
| `test_returns.py` | Partial and full returns, over-quantity refusal, refund maths with discount and tax, restock vs write-off |
| `test_views.py` | Every page renders, report aggregation, CSV exports, settings validation, password hashing |
| `test_imports.py` | File parsing (CSV, Excel, alternative headings), per-row validation, preview-vs-commit, update-by-SKU, transactionality |
| `test_registration.py` | Sign-up, approval queue, privilege-escalation attempts, password strength and hashing |
| `test_pricing.py` | Product discounts, the automatic customer discount, calculation order, number normalisation, automatic detection |
| `test_reports.py` | Daily/monthly/yearly/customer aggregation, inclusive ranges, privacy, CSV export |
| `test_employees.py` | Automatic employee IDs (including under concurrency), change-password flow |
| `test_report_nav.py` | Every report opens its own page, tab/sidebar state, chart geometry and data |

All 281 pass on both SQLite and MySQL.

---

## Implementation decisions

Where the BRS left room, these choices were made and are documented rather than hidden:

- **Login role dropdown.** The supplied design shows a "Select Role" selector on the
  login page. Trusting it would be a privilege-escalation hole, so it is kept for visual
  fidelity but **validated against the account's stored role** — picking "Administrator"
  on a cashier account is rejected. Covered by a test.
- **Custom CSS instead of Bootstrap.** The design is specific (navy sidebar, blue accent,
  particular card and table treatment). Hand-written CSS matches it exactly in ~600 lines
  with no CDN dependency, rather than fighting framework defaults.
- **Brand is a field, not a table.** BR-003 lists brand as product information, but the
  data requirements in §24.2 do not define a Brand entity — so it stays a `CharField`.
- **Sales are voided, never deleted** (BRL-6). Voiding restores stock and keeps the
  record; the Django admin has delete disabled for `Sale`.
- **Line items snapshot the product name and price**, so renaming or repricing a product
  never rewrites history on past invoices.
- **No product images.** The BRS does not require them and the design does not show them,
  so no image field, no Pillow dependency, no media serving.
- **Exports are CSV**, not PDF. The BRS asks for reports, not a specific format; invoices
  print cleanly from the browser via a dedicated print stylesheet.
- **Database connection timezone.** `DATABASES['default']['TIME_ZONE']` is set to match
  `TIME_ZONE`. See [Troubleshooting](#troubleshooting) for why this matters.
- **Self-registration is Cashier-only and approval-gated by default.** The BRS requires
  role-based access and says nothing about sign-up, so the registration form was built
  to be incapable of granting privilege — see
  [Registration and approval](#registration-and-approval).
- **Employee IDs use `editable=False`, not just form omission.** That makes Django
  refuse to build any form containing the field, so no submitted value can reach it
  through any code path — a stronger guarantee than leaving the field out of one form.
- **The chart is server-rendered SVG, not a JS library.** The app has no CDN access and
  must work offline, so `pos/charts.py` computes the geometry and the template emits
  inline SVG. It also means charts print correctly and the numbers cannot drift from the
  tables beside them.
- **Cart-level discounts were removed, the model was not.** The POS screen, the
  checkout form and the pricing engine no longer have any notion of a whole-cart
  discount, and the discount-management pages are gone. The `Discount` table and its
  rows are deliberately kept: `Sale.discount` is a foreign key on historical sales and
  must keep resolving. Old sales keep their recorded totals exactly as they were.
- **Historical sales keep a snapshot of the customer number.** `Sale.customer_number` is
  filled at the till and backfilled for old sales, so an invoice still identifies the
  right customer even if the Customer row is edited later.
- **Product photos are a URL, not an upload.** `image_url` needs no Pillow, no media
  directory and no storage configuration, and it matches how catalogue exports actually
  supply images. A product without one falls back to its initials, so lists never break.
- **Generated SKUs are derived from the source `id`, not from a counter.** A counter
  would produce a different SKU on every run, so re-importing the same file would create
  duplicates instead of updating — which is exactly the bug this design avoids.
- **The preview/confirm payload is copied field-by-field, not enumerated.** An earlier
  version listed the fields to carry between the two steps and silently dropped
  `discount_percent` and `image_url`: products imported through the web page lost their
  discount and photo, while the same file imported through the service kept them.
  Copying whatever the parser produced makes that class of bug impossible.
 Opening stock is applied only when
  a product is created, so the inventory ledger stays the single record of stock
  movement (BRL-8). A spreadsheet re-upload cannot quietly rewrite on-hand quantities.

---

## Troubleshooting

**`NotSupportedError: MariaDB 10.11 or later is required (found 10.4.x)`**
You are pointing at XAMPP's MariaDB. Django 6.1 does not support it. Start the MySQL 8.4
server and set `DB_PORT=3307` in `.env`.

**Date filters return nothing / dashboard shows zero for today**
MySQL can only resolve named time zones (`Asia/Dhaka`) after its `mysql.time_zone` tables
are loaded, which a fresh install does not do — `CONVERT_TZ()` silently returns `NULL`
and every `__date` lookup matches nothing. The project avoids this by setting
`DATABASES['default']['TIME_ZONE']` equal to `TIME_ZONE`, which removes the `CONVERT_TZ`
call altogether. Datetimes are then stored in store-local time, which is unambiguous for
Bangladesh (UTC+6, no daylight saving).

If you deploy to a zone that *does* observe DST, load the timezone tables instead and
remove that `TIME_ZONE` key:

```bash
mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql -u root mysql   # Linux/macOS
```

On Windows, import Oracle's `timezone_posix_sql.zip` from the MySQL download page.

**`Access denied for user 'pos_user'` when running tests**
The test runner needs rights on `test_pos_system`. Re-run the second `GRANT` in
[Database setup](#database-setup).

**Static files 404 in production**
`python manage.py collectstatic` — files are served from `STATIC_ROOT` (`staticfiles/`).
