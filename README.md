# Oil &amp; Gas Multi-Business ERP

A professional ERP system for an oil &amp; gas company that runs several
business units under one head office:

1. Petrol Pump Retail Business
2. Pump Name Based Bulk Sale Business
3. Carriage / Transport Business
4. Oil Agencies Business
5. Head Office / Central Control
6. Accounting &amp; Ledgers
7. Reports &amp; Dashboards

> **Status:** ~70% complete. Core modules (master data, accounting, petrol
> pumps incl. stock & COGS, bulk sale, carriage, agency, head office, reports
> with Excel/PDF export) are live.

## Private System & Premium UI

This is a **private internal system** — there are no public pages. A global
`before_request` gate in `app/__init__.py` redirects every anonymous request
(except `/auth/*` and static assets) to the sign-in screen, so **the first page
anyone ever sees is Sign in**. Deep links survive the redirect via `?next=`.

UI theme ("CMA & Co. — Oil & Gas Command Suite"):

- **Sign-in** (`templates/auth/login.html`) is a standalone full-screen page
  (does not extend `base.html`): animated brand panel + glass sign-in card,
  floating-label inputs, password eye, caps-lock warning, loading submit,
  error shake, ember particles, live clock. Respects `prefers-reduced-motion`.
- **App shell** (`templates/base.html`): dark command sidebar (role-aware
  links via `can_access_module`, active-state highlighting by blueprint),
  sticky glass topbar with live clock, floating auto-dismiss toast
  notifications for flash messages, page-enter animation.
- **Theme engine** (`static/css/style.css`): re-skins Bootstrap globally —
  cards, tables, buttons, forms, badges, breadcrumbs, alerts — so every
  existing template inherits the design system with **zero per-page changes**.
  Fonts: **Playfair Display** (luxury display serif — page titles, login brand,
  hero headings) + Space Grotesk (section headings) + Inter (body) + JetBrains
  Mono (data labels); every token carries local fallbacks (Segoe UI / Georgia /
  Consolas) so the UI stays premium even if the fonts CDN is unreachable.
  Gradient-gold headings keep a solid-gold fallback (`@supports
  background-clip:text`) so text can never render invisible, and all dim label
  greys are contrast-tuned for readability on both the dark panels and the
  light surface.
- **Home** (`templates/core/home.html`): personalised command hub — greeting,
  role chips, and animated module cards filtered by the user's access.

## Tech Stack

- Python + Flask (application-factory + blueprint architecture)
- SQLAlchemy ORM
- Flask-Migrate / Alembic
- python-dotenv for configuration
- SQLite for local development, PostgreSQL for production
- Jinja2 templates + Bootstrap 5

## Project Structure

```
project1/
├── run.py                 # Entry point
├── requirements.txt
├── .env.example
├── README.md
└── app/
    ├── __init__.py        # Application factory (create_app)
    ├── config.py          # Configuration from environment
    ├── extensions.py      # db, migrate instances
    ├── core/              # Homepage
    ├── auth/              # Authentication (placeholder)
    ├── master_data/       # Products, customers, vendors (placeholder)
    ├── accounting/        # Ledgers & vouchers (placeholder)
    ├── petrol_pumps/      # Retail sales (placeholder)
    ├── bulk_sale/         # Bulk sales (placeholder)
    ├── carriage/          # Transport (placeholder)
    ├── agency/            # Oil agency (placeholder)
    ├── head_office/       # Central control (placeholder)
    ├── reports/           # Reports & dashboards (placeholder)
    ├── templates/         # Jinja2 templates
    └── static/            # CSS / JS / images
```

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows PowerShell
# source venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your environment file
copy .env.example .env         # Windows
# cp .env.example .env         # macOS / Linux

# 4. Run the development server
python run.py
```

Then open <http://127.0.0.1:5000/> in your browser.

## Database Setup

The core foundation models live in [`app/core/models.py`](app/core/models.py):
`Company`, `BusinessUnit` (with a `BusinessUnitType` enum) and `PetrolPump`.

> **Note:** On this machine the `flask` script is not on PATH, so the commands
> below use `python -m flask`. If `flask` works directly for you, you can drop
> the `python -m ` prefix.

Run these once, in order, from the project root (Windows PowerShell):

```powershell
# Tell Flask where the app is
set FLASK_APP=run.py            # PowerShell: $env:FLASK_APP = "run.py"

# 1. Create the migrations/ folder (only if it does not exist yet)
python -m flask db init

# 2. Generate a migration from the current models
python -m flask db migrate -m "create core foundation models"

# 3. Apply the migration (creates the tables / app.db)
python -m flask db upgrade
```

### Seed the foundation data

Insert the company, 5 business units and 3 petrol pumps. Safe to run more than
once — existing records are never duplicated.

```powershell
python seed.py        # standalone script
# OR
python -m flask seed  # equivalent CLI command
```

### Verify the data

```powershell
python -c "from app import create_app; from app.core.models import Company, BusinessUnit, PetrolPump; app=create_app();
with app.app_context(): print(Company.query.all()); print(BusinessUnit.query.all()); print(PetrolPump.query.all())"
```

You should see 1 company, 5 business units, and 3 petrol pumps (all linked to
the Petrol Pump Retail unit).

### Future model changes

Whenever you add or change models in a later task:

```powershell
python -m flask db migrate -m "describe the change"
python -m flask db upgrade
```

## Routes

| URL              | Module        |
| ---------------- | ------------- |
| `/`              | Home          |
| `/auth`          | Authentication |
| `/master-data`   | Master Data   |
| `/accounting`    | Accounting    |
| `/petrol-pumps`  | Petrol Pumps  |
| `/bulk-sale`     | Bulk Sale     |
| `/carriage`      | Carriage      |
| `/agency`        | Agency        |
| `/head-office`   | Head Office   |
| `/reports`       | Reports       |

## Master Data

Manage Business Units and Petrol Pumps (Admin / Owner and Head Office Manager
only). Records are never hard-deleted — they are activated/deactivated.

| URL | Purpose |
| --- | --- |
| `/master-data` | Master Data dashboard |
| `/master-data/business-units` | List / search business units |
| `/master-data/business-units/create` | Create business unit |
| `/master-data/business-units/<id>` | View business unit |
| `/master-data/business-units/<id>/edit` | Edit business unit |
| `/master-data/petrol-pumps` | List / search petrol pumps |
| `/master-data/petrol-pumps/create` | Create petrol pump |
| `/master-data/petrol-pumps/<id>` | View petrol pump |
| `/master-data/petrol-pumps/<id>/edit` | Edit petrol pump |

A petrol pump can only be linked to a business unit whose type is
`PETROL_PUMP_RETAIL`; the create/edit forms list only active retail units.

### Products

| URL | Purpose |
| --- | --- |
| `/master-data/product-categories` | List / search product categories |
| `/master-data/product-categories/create` | Create category |
| `/master-data/product-categories/<id>` | View category (+ its products) |
| `/master-data/product-categories/<id>/edit` | Edit category |
| `/master-data/products` | List / search products |
| `/master-data/products/create` | Create product |
| `/master-data/products/<id>` | View product |
| `/master-data/products/<id>/edit` | Edit product |

Product names must be unique **within a category** (the same name may exist in
different categories). Default purchase/sale rates cannot be negative. Units:
liters, bottles, cartons, drums, pieces.

### Customers & Vendors

Customers and vendors have full CRUD: **+ New** (add), **Edit**, **Deactivate /
Activate** (soft retire), and **Delete** (hard remove). Delete is *guarded* — it
only removes a record with **no referencing transactions**; otherwise it refuses
and tells you to deactivate instead (keeping history intact). Note the guard
checks every FK, including `CarriageTrip.rented_vehicle_vendor_id` (not all
vendor FKs are named `vendor_id`).


| URL | Purpose |
| --- | --- |
| `/master-data/customers` | List / search customers |
| `/master-data/customers/create` | Create customer |
| `/master-data/customers/<id>` | View / `/edit` customer |
| `/master-data/vendors` | List / search vendors |
| `/master-data/vendors/create` | Create vendor |
| `/master-data/vendors/<id>` | View / `/edit` vendor |

Customers and vendors each belong to a business unit. The same name may exist
under different business units, but not twice within the same unit. Opening
balance and credit limit cannot be negative. Vendor type is required (PSO,
Agency Supplier Company, Rented Vehicle Provider, Maintenance Vendor, etc.).

### Vehicles & Drivers

Manageable by **Admin / Owner, Head Office Manager, and Transport Manager**
(the only master-data section the Transport Manager can access).

| URL | Purpose |
| --- | --- |
| `/master-data/drivers` | List / search drivers |
| `/master-data/drivers/create` | Create driver |
| `/master-data/drivers/<id>` | View (with assigned vehicles) / `/edit` |
| `/master-data/vehicles` | List / search vehicles |
| `/master-data/vehicles/create` | Create vehicle |
| `/master-data/vehicles/<id>` | View / `/edit` vehicle |

Vehicle number is unique (case-insensitive). A vehicle may optionally link to a
driver. Owner name is required when ownership type is *Rented Vehicle*. Salary
and capacity cannot be negative. Vehicle types: Tanker, Truck, Pickup, Other.
Ownership types: Own Vehicle, Rented Vehicle, Customer Vehicle.

> The Master Data **dashboard** (`/master-data`) remains Admin + Head Office
> only; the Transport Manager reaches `/master-data/vehicles` and
> `/master-data/drivers` directly.

### Cash / Bank Accounts & Expense Categories

Manageable by **Admin / Owner, Head Office Manager, and Accountant**.

| URL | Purpose |
| --- | --- |
| `/master-data/cash-bank-accounts` | List / search accounts |
| `/master-data/cash-bank-accounts/create` | Create account |
| `/master-data/cash-bank-accounts/<id>` | View / `/edit` account |
| `/master-data/expense-categories` | List / search expense categories |
| `/master-data/expense-categories/create` | Create expense category |
| `/master-data/expense-categories/<id>` | View / `/edit` expense category |

Both can optionally belong to a business unit; leaving it blank makes a
**global** record. Names are unique within their scope (per business unit, and
separately within the global scope). Account types: Cash, Bank, Wallet, PSO
Ledger, Customer Receivable Control, Vendor Payable Control, Other. Opening and
current balances cannot be negative. Reaching the Accountant role, the dashboard
itself is still Admin + Head Office; the Accountant uses the two URLs directly.

> **Seeding note:** Default pumps, the product catalogue, the default
> customers/vendors, and the default drivers/vehicles are only created when
> their tables are empty, so re-running `python seed.py` never recreates or
> duplicates records you have renamed/edited.

## Accounting

Accessible by **Admin / Owner, Head Office Manager, and Accountant**.

| URL | Purpose |
| --- | --- |
| `/accounting` | Accounting dashboard |
| `/accounting/chart-of-accounts` | List / search chart accounts |
| `/accounting/chart-of-accounts/create` | Create account |
| `/accounting/chart-of-accounts/<id>` | View / `/edit` account |
| `/accounting/journal-vouchers` | List / filter journal vouchers |
| `/accounting/journal-vouchers/create` | Create a balanced manual voucher |
| `/accounting/journal-vouchers/<id>` | View voucher with debit/credit lines |

Models: `ChartOfAccount` (self-referential parent/children, optional business
unit), `Voucher`, `JournalEntry`, `JournalEntryLine`. Double-entry rules live in
`app/accounting/services.py`:

- every entry needs a business unit and at least 2 lines;
- total debit must equal total credit, and both must be > 0;
- a line cannot have both a debit and a credit; amounts cannot be negative.

Voucher numbers are generated per type (e.g. `JV-0001`). The default chart of
accounts is seeded once (empty-table guarded).

## Petrol Pump Setup

Accessible by **Admin / Owner, Head Office Manager, and Petrol Pump Manager**
(note: Cashier can see the petrol pumps landing page but **not** the setup
pages). Configures pump equipment only — no sales/readings yet.

| URL | Purpose |
| --- | --- |
| `/petrol-pumps` | Petrol pumps landing (links to setup) |
| `/petrol-pumps/setup` | Setup dashboard |
| `/petrol-pumps/setup/machines` | Machines / dispensers CRUD |
| `/petrol-pumps/setup/nozzles` | Nozzles CRUD |
| `/petrol-pumps/setup/tanks` | Tanks CRUD |

Models: `PumpMachine`, `PumpNozzle`, `PumpTank`. Nozzles and tanks may only use
**fuel products** (Petrol, Diesel, High Octane — the *Fuel Products* category);
LDO/Kerosene/lubricants are rejected. A nozzle's machine must belong to the
selected pump; nozzle numbers are unique per machine; tank names are unique per
pump. Readings/stock cannot be negative, current reading ≥ opening reading, and
current stock cannot exceed capacity. Seeded once: 6 machines, 12 nozzles, 9
tanks (empty-table guarded).

## Petrol Pump Machine Readings (Retail Fuel Sale)

Accessible by **Admin / Owner, Head Office Manager, Petrol Pump Manager, and
Cashier** (Accountant is excluded). Retail fuel sale is always calculated from
the meter — never entered directly:

```
sale_liters = closing_reading - opening_reading
sale_amount = sale_liters × rate
```

| URL | Purpose |
| --- | --- |
| `/petrol-pumps/machine-readings/console` | **Reading Console** — the primary entry experience |
| `/petrol-pumps/machine-readings` | History — list / filter readings |
| `/petrol-pumps/machine-readings/create` | Classic single-reading form (advanced) |
| `/petrol-pumps/machine-readings/<id>` | View reading + calculated totals |
| `/petrol-pumps/machine-readings/<id>/edit` | Edit (recalculates) |
| `/petrol-pumps/daily-sale-summary` | Product-wise sale totals (date/pump filter) |

**Reading Console** (the Machine Readings tab's main screen): a pump selector
bar on top → premium machine cards (custom dispenser SVG per machine, its
nozzle chips with product colour + live meter, Day/Night done badges, today's
sale) → tapping a machine opens a dark luxury modal asking only the essentials:
date, **12-hour shift** (Day ☀ / Night ☾ toggle), and per nozzle the opening
(prefilled from the meter), closing, and sale rate (prefilled from the last
reading, else the product default) — liters and sale amount calculate **live**
as you type, and one Save records the whole machine's shift
(`machine_readings_console_save`, one `MachineReading` per touched nozzle;
untouched nozzles are skipped). Same validations and tank-stock sync as the
classic form; a second entry for the same nozzle/date/shift is blocked.

Model: `MachineReading`. Validations: machine∈pump, nozzle∈machine, product ==
nozzle's product, non-negative readings, closing ≥ opening, rate ≥ 0, and no
duplicate reading for the same nozzle/date/shift (blank shift → "Full Day").
On save, the nozzle's `current_reading` is advanced to the closing reading.

> **Stock note:** machine readings do **not** update tank stock yet — fuel stock
> movements are handled in a later task. This module is petrol pump *retail*
> sale only and never touches bulk/carriage/agency records.

## Petrol Pump Purchases (+ Tank Stock)

Accessible by **Admin / Owner, Head Office Manager, Petrol Pump Manager, and
Accountant** (Cashier excluded). Records fuel purchases (normally from PSO) and
increases pump tank stock when fuel is received.

| URL | Purpose |
| --- | --- |
| `/petrol-pumps/purchases/console` | **Purchase Console** — the primary entry experience |
| `/petrol-pumps/purchases` | History — list / filter purchases |
| `/petrol-pumps/purchases/create` | Classic multi-line form (advanced, up to 4 rows) |
| `/petrol-pumps/purchases/<id>` | View purchase + items + stock status |
| `/petrol-pumps/purchases/<id>/edit` | Edit (safely re-posts stock) |

**Purchase Console** (the Purchases tab's main screen, mirrors the Reading
Console): a pump selector bar → premium fuel **storage-tank** cards (Petrol /
Diesel / High Octane, each showing current liters + "received today") plus
**Lubricant** cards in the same screen → tapping a card opens a dark modal asking
only: the **vendor** (a required selector — a purchase cannot be saved without
one; the PSO vendor is pre-selected for convenience), quantity, purchase rate
(prefilled from the last purchase, else the product's default), the **delivery
vehicle chosen from the carriage fleet**, and driver name (auto-prefilled from
the vehicle's assigned driver, still editable). The chosen vendor's payable is
created (visible in Head Office → Outstanding Payables). One Save creates a `PumpPurchase` +
item: fuel cards target the tank (posts tank stock), lubricant cards record the
purchase with no tank. Same `_sync_stock` posting as the classic form.

Models: `PumpPurchase` (+ `stock_posted` flag), `PumpPurchaseItem`. Item total =
`quantity × rate`; purchase total = sum of items.

**Tank stock** increases only when the purchase is **active**, **tank_received =
Yes**, and **delivery_status = Received**. This is enforced idempotently by a
`stock_posted` flag and a single `_sync_stock()` routine:

- create Received → stock +qty (posted once);
- Pending/Cancelled or tank-not-received → no stock change;
- deactivate a posted purchase → stock −qty; reactivate → +qty (never double);
- edit reverses the old quantities then re-applies the new ones;
- a negative-stock guard blocks unposting that would drive a tank below zero.

When `tank_received = Yes`, each item needs a tank that belongs to the selected
pump and whose product matches the item. Duplicate invoice numbers per vendor
are rejected. This is petrol-pump *retail* purchase only — it never touches
bulk/carriage/agency stock flows.

## Fuel Stock / Inventory (tank balances + movement ledger)

Accessible by the petrol-pump module roles (incl. Cashier). Fuel tank stock now
moves **both ways** — purchases in, **retail sales out** — and every change is
recorded in an audit ledger.

| URL | Purpose |
| --- | --- |
| `/petrol-pumps/tank-stock` | Current fuel on hand per tank (Excel/PDF export) |
| `/petrol-pumps/stock-movements` | Audit ledger of every movement (filter + export) |

How it works (`app/petrol_pumps/stock.py`, `StockMovement` model):

- **Running balance** stays on `PumpTank.current_stock_liters`; **`StockMovement`**
  is the signed audit trail behind it (+ in, − out), tied to its source via
  `(source_type, source_id)`. Both are updated in the same transaction.
- **Retail sales decrement stock.** Each `MachineReading` resolves the pump's
  single active tank for its product (`resolve_tank`) and posts a `−sale_liters`
  movement. New columns `MachineReading.tank_id` + `stock_posted` make this
  idempotent, mirroring the purchase pattern:
  - create/activate active reading → tank −liters (posted once);
  - deactivate → restored; edit → reverses the **previously posted** quantity
    (read from the ledger, not the new value) then re-posts;
  - if a pump has no *single* active tank for the product, the sale saves but
    stock isn't adjusted (a flash note says so).
- **Sales are facts** (meter-derived), so a sale may drive a tank negative — shown
  in red as a signal of a missing purchase entry, for the future gain/loss flow.
- Purchases also write `Purchase Received` ledger rows, so the ledger reconciles
  with the tank balance.

### Stock Gain / Loss Adjustments (BRD §12.3 + §16 approval)

| URL | Purpose |
| --- | --- |
| `/petrol-pumps/stock-adjustments` | List / filter adjustments (Excel/PDF export) |
| `/petrol-pumps/stock-adjustments/create` | New dip count (live difference preview) |
| `/petrol-pumps/stock-adjustments/<id>` | Detail + approve / reject / reopen actions |
| `/petrol-pumps/stock-adjustments/<id>/edit` | Edit (Pending only) |

Model `StockAdjustment`: system stock is **snapshotted from the tank** at save;
`difference = physical − system` (+gain / −loss). Lifecycle: **Pending →
Approved / Rejected** (Rejected can be reopened).

- **Approval-gated posting**: only an *Approved* adjustment touches
  `PumpTank.current_stock_liters` and writes a `Stock Gain` / `Stock Loss`
  `StockMovement` row — Pending entries have zero stock effect (BRD §6.6).
- Entry roles: Admin / Owner, Head Office Manager, Petrol Pump Manager.
  **Approval roles: Admin / Owner, Head Office Manager only.** Cashier can
  view the list but cannot create or approve (verified 403).
- Reject reverses any posted stock from the ledger rows (idempotent pattern);
  a guard blocks rejecting a posted *gain* if reversal would drive the tank
  negative. Editing is locked once approved.
- **P&L**: approved gains/losses are valued at the pump+product
  weighted-average cost (`costing.stock_adjustment_value`) and appear as a
  "Stock gain/loss (approved)" line in the pump P&L; pump
  `net = revenue − COGS − expenses + stock gain/loss`.

> Not in this slice (next tasks): lubricant stock (needs a lubricant-stock
> master like tanks), godown/agency stock, and stock transfers.
> `StockMovement` is intentionally generic to absorb these.

## Maintenance & Daily Checklist (BRD §15)

Pump upkeep, under Petrol Pumps. Managed by Admin / Owner, Head Office Manager,
Petrol Pump Manager (Cashier excluded — verified 403).

| URL | Purpose |
| --- | --- |
| `/petrol-pumps/checklists` | Daily checklist list (filters + export) |
| `/petrol-pumps/checklists/create` · `/<id>` · `/<id>/edit` | Grid create / view / edit |
| `/petrol-pumps/maintenance` | Maintenance complaints (search/filter + export) |
| `/petrol-pumps/maintenance/create` · `/<id>` · `/<id>/edit` | Log / view / edit complaint |
| `/petrol-pumps/maintenance/<id>/set-status` | Quick Pending → In Progress → Completed |

- **`DailyChecklist`** (+ `DailyChecklistItem` child rows): one per (pump, day),
  `UniqueConstraint` enforced. The create grid covers the 11 standard §15.1
  items, each marked **OK / Issue / N/A** with an optional note; re-creating the
  same day **opens the existing one for edit** (no duplicates). `issue_count`
  surfaces flagged items.
- **`MaintenanceComplaint`**: date, type, description, assigned vendor,
  estimated vs actual cost, and a **Pending / In Progress / Completed** status
  workflow (quick-transition buttons on the detail page). Full CRUD + delete.

## Pump Staff / HR (BRD §6.9)

Employee master + attendance + payroll, under Petrol Pumps. Managed by
Admin / Owner, Head Office Manager, Petrol Pump Manager (Cashier excluded —
salary data). Salary payments additionally allow the Accountant.

| URL | Purpose |
| --- | --- |
| `/petrol-pumps/staff` | Employee list (search/filter, Excel/PDF export) |
| `/petrol-pumps/staff/create` · `/<id>` · `/<id>/edit` | Add / view / edit employee |
| `/petrol-pumps/staff/<id>/delete` | Hard delete employee |
| `/petrol-pumps/staff/attendance` | Mark daily attendance (per-pump grid, upsert) |
| `/petrol-pumps/staff/attendance/report` | Attendance report (filters + export) |
| `/petrol-pumps/staff/salary-payments` | Salary / advance payments (list + export) |
| `/petrol-pumps/staff/salary-payments/create` · `/<id>/edit` | Record / edit payment |

- **`PumpStaff`**: name, designation (Manager/Cashier/Filler/Sweeper/Gunman/Other),
  duty-station pump, shift, CNIC, phone, emergency contact, address, monthly
  salary, joining date, status. Soft-delete (Deactivate) + guarded-style Delete.
- **`PumpAttendance`**: one row per (employee, day), `UniqueConstraint` enforced.
  The mark grid **upserts** — re-saving a day updates instead of duplicating.
  Statuses: Present / Absent / Leave / Half Day.
- **`PumpSalaryPayment`** (Salary or Advance): when a *Paid From* account is set,
  that account's balance is **decreased**, kept idempotent by `is_posted`
  (same pattern as head-office payments). Edit reverses then re-posts; cancel
  restores; delete reverses any balance effect. Negative-balance safe.

## Petrol Pump Daily Operations

Accessible by **Admin / Owner, Head Office Manager, Petrol Pump Manager, and
Cashier** (Accountant excluded → 403).

| URL | Purpose |
| --- | --- |
| `/petrol-pumps/expenses` | Pump expenses CRUD |
| `/petrol-pumps/lubricant-sales` | Lubricant sales CRUD (non-fuel) |
| `/petrol-pumps/daily-closings` | Daily closing (derived totals) |

Models: `PumpExpense`, `LubricantSale` (+ `LUBRICANT_PAYMENT_METHODS`),
`PumpDailyClosing`. Calculation helpers live in `app/petrol_pumps/services.py`:
`calculate_daily_fuel_sales`, `calculate_daily_lubricant_sales`,
`calculate_daily_expenses`, `calculate_daily_closing_summary`.

- **Expenses**: amount > 0; expense category must be global or a Petrol Pump
  Retail category; optional paid-from account / vendor.
- **Lubricant sales**: lubricant-category products only (fuel rejected); total =
  `quantity × rate`; Credit Customer payment requires a customer. *Lubricant
  stock movement is deferred to the stock module.*
- **Daily closing**: pick pump + date → fuel sale (from machine readings),
  lubricant sale (from lubricant sales) and expenses (from pump expenses) are
  computed; `total_sale = fuel + lubricant`,
  `expected_cash_to_submit = cash_received − expenses_paid`,
  `difference = cash_submitted − expected_cash_to_submit`. Payment-method splits
  are entered manually; a mismatch vs. total sale shows a **warning** (not a
  block). One closing per pump/date. No head-office cash receipt or accounting
  posting yet.

## Cash Transfers (BRD §11.2)

Move money between any two cash/bank/wallet accounts (e.g. Pump 1 Cash → Head
Office Cash), under Head Office.

| URL | Purpose |
| --- | --- |
| `/head-office/cash-transfers` | List / filter transfers (Excel/PDF export) |
| `/head-office/cash-transfers/create` · `/<id>` · `/edit` | Record / view / edit |
| `/head-office/cash-transfers/<id>/toggle-status` · `/delete` | Cancel / delete |

- `CashTransfer` model: idempotent posting via `is_posted` — the **source
  balance decreases and the destination increases** together; edit reverses then
  re-posts, cancel/delete restore both sides (negative-balance warning on the
  source). From and To must differ.
- **Auto-posts a journal** (Dr destination / Cr source) only when the two
  accounts map to *different* chart accounts (e.g. Cash → Bank). A same-type move
  (Cash → Cash) has no GL effect, so the journal is skipped while the per-account
  balances still move.

## Head Office — Cash Received from Pumps

Accessible by **Admin / Owner, Head Office Manager, and Accountant**. The first
cross-unit cash flow: records cash a petrol pump submits to head office.

| URL | Purpose |
| --- | --- |
| `/head-office` | Head office dashboard |
| `/head-office/cash-receipts` | List / filter receipts |
| `/head-office/cash-receipts/create` | New receipt (optionally `?daily_closing_id=`) |
| `/head-office/cash-receipts/<id>` | View / `/edit` receipt |

Model: `HeadOfficeCashReceipt`. A receipt belongs to a pump, may link to a pump
daily closing, and may be received **into a cash/bank account** — in which case
that account's `current_balance` is increased. Posting is idempotent via an
`is_posted` flag and a `_sync_receipt()` routine (deactivate → balance −amount,
reactivate → +amount, edit reverses then re-applies; a negative-balance guard
blocks unsafe unposting). Amount must be > 0; a linked closing must belong to
the pump and can only be receipted once; if the amount differs from the
closing's submitted cash a **warning** is shown (not blocked). No accounting
voucher is posted yet.

### Head Office Expenses

Same roles. Records head office spending (home, welfare, office, etc.).

| URL | Purpose |
| --- | --- |
| `/head-office/expenses` | List / filter expenses |
| `/head-office/expenses/create` | New expense |
| `/head-office/expenses/<id>` | View / `/edit` expense |

Model: `HeadOfficeExpense`. The expense category must be global or a **Head
Office** category. If a **paid-from account** is chosen, that account's balance
is **decreased** by the amount (idempotent via `is_posted`; deactivate refunds,
edit reverses then re-applies). Amount must be > 0. If a posting drives the
account balance below zero a **warning** is shown but the expense still saves
(balances start at zero, so blocking would make expenses impossible). No
accounting voucher is posted yet.

### Vendor / PSO Payments

Same roles. Pays a vendor (e.g. PSO) from a head office account.

| URL | Purpose |
| --- | --- |
| `/head-office/vendor-payments` | List / filter payments |
| `/head-office/vendor-payments/create` | New payment |
| `/head-office/vendor-payments/<id>` | View / `/edit` payment |

Model: `VendorPayment`. Vendor required; amount > 0. If a **paid-from account**
is chosen, its balance is **decreased** (idempotent via `is_posted`; deactivate
refunds, edit reverses then re-applies; negative balance warns, doesn't block).
Together with cash receipts and expenses, head office now has a working
money-in / money-out flow against the same cash/bank balances. No accounting
voucher is posted yet.

## Bulk Sale (pump-name-based)

Accessible by **Admin / Owner and Head Office Manager**. Bulk fuel sales done in
the *name* of a petrol pump but **completely separate** from petrol retail —
they never affect machine readings, retail daily sale or pump tank stock (fuel
goes PSO → customer directly).

| URL | Purpose |
| --- | --- |
| `/bulk-sale` | Dashboard (sale / purchase / profit totals) |
| `/bulk-sale/sales` | List / filter bulk sales |
| `/bulk-sale/sales/create` | New bulk sale |
| `/bulk-sale/sales/<id>` | View / `/edit` bulk sale |

Model: `BulkSale`. Mandatory pump, customer, vendor (PSO) and a **fuel** product.
System-calculated:

```
total_purchase_amount = quantity_liters × purchase_rate
total_sale_amount     = quantity_liters × sale_rate
net_profit            = total_sale_amount − total_purchase_amount
                        − carriage_charges − other_expense
```

Tracks delivery method/vehicle/driver/location, carriage (and who pays it),
payment method/status, and delivery status. Customer-receivable / PSO-payable
ledger posting and carriage-trip linking are deferred to later tasks (no running
customer/vendor balance exists yet).

## Carriage / Transport

Accessible by **Admin / Owner, Head Office Manager, and Transport Manager**.
Vehicle/tanker trips with their own freight income, expenses and profit/loss.

| URL | Purpose |
| --- | --- |
| `/carriage` | Dashboard (trip counts, freight, profit) |
| `/carriage/trips` | List / filter trips |
| `/carriage/trips/create` | New trip (auto trip number) |
| `/carriage/trips/<id>` | View / `/edit` trip |

Model: `CarriageTrip`. Supports own / rented / customer vehicles, the BRD trip
types, optional links to a vehicle/driver/rented-provider/product, freight, and
the six expense categories. System-calculated:

```
net_profit = total_freight_amount
             − (rent + fuel + toll + loading/unloading + driver
                + maintenance + other)
```

Trip number is auto-generated (`TRIP-0001`, …). Quantity delivered cannot exceed
quantity loaded. Carriage keeps its own profit/loss and does not post into the
pump/bulk/agency businesses.

Each trip **auto-posts on the Carriage business unit** (create/edit/toggle) via
`posting.sync_carriage_trip` — freight income (only when charged to a Customer /
Pump / Agency), own-vehicle cash expenses (when a **paid-from** account is set),
and rented-vehicle rent as a vendor payable. See the auto-posting table for the
exact debits/credits.

## Oil Agencies (LDO / Kerosene)

Accessible by **Admin / Owner and Head Office Manager**. A standalone business
with its own vendors, customers, purchases and sales of agency products (LDO,
Kerosene). Completely separate from the petrol pump business.

| URL | Purpose |
| --- | --- |
| `/agency` | Dashboard (purchase / sale / profit totals) |
| `/agency/purchases` | List / create / view / edit purchases |
| `/agency/sales` | List / create / view / edit sales |

Models: `AgencyPurchase` (vendor → agency) and `AgencySale` (agency → customer).
Products must be in the **Agency Products** category. System-calculated:

```
purchase total = quantity × purchase_rate
sale total     = quantity × sale_rate
net_profit     = sale total − cost total − carriage_cost − other_expense
```

Sales record delivery method (Customer Pickup / Rented Vehicle / Own Vehicle),
payment method/status. Agency godown stock is optional and deferred to the stock
module (BRD §9.5).

## Reports & Dashboards

Accessible by **Admin / Owner, Head Office Manager, Petrol Pump Manager,
Accountant, and Transport Manager** (read-only consolidation across all units).

| URL | Purpose |
| --- | --- |
| `/reports` | Company dashboard (cash, combined P/L, business-wise table) |
| `/reports/profit-loss` | Business-wise profit/loss detail (date-filterable) |
| `/reports/cash-position` | All cash/bank account balances + total |

Aggregation logic lives in `app/reports/services.py` (date-filterable):

- **Petrol Pump Retail:** revenue = fuel sale (machine readings) + lubricant
  sale; **fuel COGS is now matched** to sales via weighted-average costing (see
  below); net = revenue − fuel COGS − pump expenses. (Lubricant cost is not yet
  captured, since lubricants aren't bought through pump purchases.)
- **Bulk / Carriage / Agency:** true per-record net profit summed.
- **Head Office:** cash received, expenses, vendor payments (overhead; receipts
  are internal transfers, excluded from P/L).
- **Combined P/L** = pump net + bulk + carriage + agency − head office expenses
  − vendor payments.
- **Cash position** = sum of active `CashBankAccount.current_balance`.
- **Receivables / Payables** = exact, from the customer/vendor ledgers.

### Petrol pump fuel COGS (weighted-average costing)

`app/petrol_pumps/costing.py` derives a real cost of goods sold for retail fuel
sales — no schema change, no posting, nothing touches live tank stock:

```
weighted_avg_cost(pump, product) = Σ purchase total_amount / Σ purchase liters
fuel_cogs                        = Σ over (pump, product) of  sold_liters × cost
```

Cost is computed **per (pump, product)** over that pump's active fuel purchases
(optionally up to a `date_to`), so each pump is costed at its own PSO prices and
business-wise profit stays correct. A reading whose (pump, product) has no
purchase history contributes zero COGS (we don't invent a cost). The P/L now
shows **Fuel COGS** and **Fuel gross profit** rows; the old *indicative* labels
on pump net and combined P/L are gone.

### Excel / PDF export

`app/reports/exporters.py` is a reusable exporter: `export_response(fmt, base,
title, blocks)` builds a downloadable `.xlsx` (openpyxl) or `.pdf` (reportlab)
from a generic list of blocks (`{title, headers, rows}`). Any list/report view
adds export by checking `?export=xlsx|pdf` and reusing its current filters.

Export buttons (honour active filters) are on:

| Page | Excel / PDF |
| --- | --- |
| `/reports/profit-loss` | ✓ |
| `/reports/cash-position` | ✓ |
| `/petrol-pumps/daily-sale-summary` | ✓ |
| `/petrol-pumps/machine-readings` | ✓ |
| `/petrol-pumps/purchases` | ✓ |

New deps: `openpyxl`, `reportlab` (already in `requirements.txt`).

## Approval Workflow (BRD §16)

An oversight layer for sensitive entries: a manager **approval queue** plus a
status banner on each entry. One polymorphic `approvals` table (like
attachments) tracks the review per source record.

| URL | Purpose |
| --- | --- |
| `/approvals` | Manager inbox — pending/approved/rejected, filterable, approve/reject/reopen |
| `POST /approvals/<id>/approve` · `/reject` · `/reopen` | Decisions (with reason) |

- **What needs approval** (`app/approvals/service.py`): head-office expenses
  ≥ 50,000, vendor payments ≥ 100,000, and **all** bulk & agency sales. Each
  flow calls `request_if_needed(...)` on create/edit — idempotent: dropping
  below a threshold removes the request, and editing an already-approved amount
  re-opens it (Pending) for re-review.
- **Roles:** only **Admin / Owner** and **Head Office Manager** can approve;
  the queue and the sidebar **pending badge** appear only for them (others get
  403). The badge count is injected globally via the app context processor.
- **Visibility:** a reusable banner — `{% import "_approval.html" as appr with
  context %}` then `{{ appr.banner('ho_expense', expense.id) }}` — shows the
  status on the detail page, with inline approve/reject for approvers. Wired
  into: head-office expenses, vendor payments, bulk sales, agency sales. (Stock
  gain/loss adjustments keep their own built-in approval that *gates* posting.)
- **GL hard-gating:** the four gated entry types are **held out of the General
  Ledger / Trial Balance until approved**. `service.is_blocked(type, id)` is true
  when an approval row exists and is not yet Approved (Pending/Rejected); the
  posting engine's `_approval_clears(...)` folds this into each gated `sync_*`,
  and approve/reject/reopen call `posting.resync_for_approval(...)` so the GL
  entry appears on approval and disappears on rejection. Create/edit routes
  request the approval **before** posting so the gate sees it.
- **Grandfathered:** an entry with **no** approval record posts as before, so all
  pre-existing data is unaffected (no migration; there were 0 approval rows at
  rollout). Only newly-created sensitive entries are gated.
- **Scope = GL only.** Operational cash balances and the *derived* reports
  (receivables / P&L read source tables directly) are **not** gated, so a Pending
  entry still shows there but not in the journal/Trial Balance until a manager
  acts (a transient state). Stock gain/loss adjustments keep their own separate
  hard-gate.

## Attachments / File Upload (BRD §18)

Any record can carry files (invoices, PSO invoices, delivery challans, receipts,
vehicle docs, maintenance proof, photos). One polymorphic `attachments` table
points at its owner by `(entity_type, entity_id)`.

| URL | Purpose |
| --- | --- |
| `POST /attachments/upload` | Upload a file for an entity (multipart) |
| `GET /attachments/<id>/download` | Download (`?inline=1` to view in browser) |
| `POST /attachments/<id>/delete` | Remove file + row |

- Files are stored **outside `static/`** (in `UPLOAD_FOLDER`, default
  `uploads/`) under a generated UUID name, and are served only through the
  authenticated download route — so attachments are private to logged-in users
  (the app-wide login gate covers all three routes).
- Uploads are validated by **extension allowlist** (pdf/images/office/csv/txt)
  and capped at **10 MB** (`MAX_CONTENT_LENGTH`). The original filename, type,
  size, kind label and uploader are recorded.
- A reusable panel — `{% import "_attachments.html" as att with context %}` then
  `{{ att.panel('entity_type', record.id) }}` — drops a list + upload form onto
  any detail page. Wired into: **pump purchases, bulk sales, agency purchases,
  head-office expenses, maintenance complaints**. (Note: the import needs
  `with context` so the macro can see the injected `entity_attachments` helper.)

## Pump Nozzle-wise Sale (BRD §13.1)

`/petrol-pumps/nozzle-sale-summary` — retail fuel sale per **pump → machine →
nozzle → product** (liters + amount), the nozzle-level companion to the
product-wise Daily Sale Summary. Date/pump filterable, Excel/PDF export.
Cross-linked with the Daily Sale Summary.

## Agency Reports (BRD §13.4)

`/agency/reports` — **product-wise (LDO/Kerosene), customer-wise and
delivery-method-wise sale**, plus **vendor-wise purchases**, in one
date-filterable page (single Excel/PDF export with all four tables). Sale tables
show deals, qty, sale, net and **receivable** (unpaid sale value); the purchase
table shows **payable** (unpaid purchase value). The **delivery-method** table
(Customer Pickup / Rented Vehicle / Own Vehicle) additionally breaks out
**carriage cost** per method (BRD §9.7 delivery-method-wise cost / §13.4 delivery
method report). Logic in `app/agency/reports.py`. Linked from the Oil Agency
landing page.

**Godown stock (optional, BRD §9.5):** agency purchases can be marked *received
into godown* (`AgencyPurchase.to_godown`) and sales *sold from godown*
(`AgencySale.from_godown`). A **Godown Stock** report then derives per-product
on-hand = Σ received − Σ sold (`reports.godown_stock`), shown on the agency
reports page and in the export. Direct drop-ship purchases/sales (the default,
both flags off) don't touch godown stock, so the feature is fully opt-in and has
no GL/cash effect.

## Bulk Sale Reports (BRD §13.2)

`/bulk-sale/reports` — **customer-wise, product-wise and pump-wise** bulk sale in
one page (date-filterable, single Excel/PDF export with all three tables). Each
group shows deals, liters, sale value, net profit, and **outstanding receivable**
(sale value of deals not marked Paid) — answering "which customer has a pending
balance?". Logic in `app/bulk_sale/reports.py` (Python aggregation, sorted by
sale value). Linked from the Bulk Sale landing page.

## Carriage Profit/Loss Reports (BRD §13.3)

`/carriage/reports` — **vehicle-wise, driver-wise and delivery (trip-type)
profit/loss** in one page (date-filterable, single Excel/PDF export with all
tables). Directly answers the BRD §23 question "which vehicle is profitable or
loss-making?".

Logic in `app/carriage/reports.py`: trips are grouped in Python by vehicle and by
driver — using the master vehicle/driver when linked by id, else the free-text
number/name, else "Unassigned". Each group shows trip count, freight income,
total expenses (`CarriageTrip.total_expenses`), and net profit, sorted by net
descending. The **Delivery Report** (`delivery_pl`) groups the same metrics by
`trip_type` (PSO→pump, →bulk customer, godown→customer, supplier→agency customer,
…), covering the BRD §8.7 customer / pump / agency / bulk-sale delivery reports in
one breakdown. A **Carriage Expense Breakdown** (`expense_breakdown`) splits expenses by
category (rent, fuel, toll, loading/unloading, driver, maintenance, other) across
all trips, with per-category trip counts (§8.7). The page also includes a
**Rented Vehicle Payable** table (rent owed to rented-vehicle providers, grouped
by vendor — §13.3). Linked from the Carriage landing page.

## Account Ledgers — Cash Book & General Ledger (BRD §13.5)

Two running-balance statements (`app/reports/ledgers.py`), both date-filterable
with Excel/PDF export.

| URL | Shows |
| --- | --- |
| `/reports/cash-book` | **Cash / Bank Book** — every posted movement against one operational account (receipts, expenses, vendor & salary payments, transfers in/out) with a running balance |
| `/reports/general-ledger` | **General Ledger** — journal-line detail for one chart-of-accounts account; linked from each Trial Balance row |

- Each statement computes an **opening balance** from movements before the
  period, then runs the balance through the period to a closing figure.
- The Cash Book's closing balance **reconciles with the account's live
  `current_balance`** when unfiltered (verified) — a built-in integrity check
  that every cash movement is captured.
- General Ledger reuses the auto-posted journal data, so it's the detail behind
  the Trial Balance (click any account there to drill in).

## Per-Business-Unit Dashboards (BRD §14.2)

Each business unit has its own visual dashboard with KPI cards and inline
bar/chip breakdowns (dependency-free SVG/CSS, matching the theme). All
date-filterable; linked from the Reports landing page and cross-linked via tabs.

| URL | Shows |
| --- | --- |
| `/reports/dashboard/pump` | Fuel/lubricant sale, COGS, stock gain/loss, net, liters, tank stock; product-wise fuel sale |
| `/reports/dashboard/bulk` | Total sale, cost, net, outstanding receivable; pump-wise sale; delivery-status counts |
| `/reports/dashboard/carriage` | Trips, freight, expenses, net; trips & profit by vehicle type; trip-status counts |
| `/reports/dashboard/agency` | Sale, purchases, net, receivable/payable; LDO/Kerosene sale + quantity |
| `/reports/dashboard/head-office` | Cash / bank / wallet balances, receivables, payables, combined P/L; business-wise net table |

Data lives in `app/reports/dashboards.py` (one function per unit, reusing
`reports/services.py` + unit-specific aggregations). Accessible by the reports
roles. Bars/KPIs render gracefully when a period has no data.

## Automatic Double-Entry Posting

Selected financial transactions now post to the double-entry ledger
automatically — no manual journal voucher needed. Engine:
`app/accounting/posting.py`.

**Wired flows** (each generates one balanced voucher + journal entry):

| Transaction | Debit | Credit |
| --- | --- | --- |
| Customer receipt | Cash/Bank (1000/1010/1020) | Customer Receivables (1100) |
| Head-office expense | Head Office Expenses (6300) | Cash/Bank |
| Vendor payment | Vendor Payables (2000) / PSO Payable (2010) | Cash/Bank |
| Bulk sale | Receivables (1100) + Bulk Cost (5100) | Bulk Income (4100) + PSO Payable (2010) |
| Agency sale | Customer Receivables (1100) | Agency Income (4300) |
| Agency purchase | Agency Product Cost (5300) | Vendor Payables (2000) |
| Pump fuel purchase | Stock / Inventory (1200) | PSO Payable (2010) |
| Staff salary / advance | Pump Expenses (6000) | Cash/Bank |
| Pump daily closing (retail revenue) | Cash/Bank/PSO-card (1300)/Wallet/Receivables | Pump Fuel Sale (4000) + Lubricant Sale (4010) |
| Pump daily closing (fuel COGS) | Fuel Purchase Cost (5000) | Stock / Inventory (1200) |
| Cash transfer (cross-account-type) | Destination account | Source account |
| Carriage trip — freight income¹ | Customer Receivables (1100) | Carriage Income (4200) |
| Carriage trip — own-vehicle cash expenses² | Carriage Expenses (6100) | Paid-from Cash/Bank |
| Carriage trip — rented-vehicle rent | Carriage Expenses (6100) | Vendor Payables (2000) |

¹ Only when `freight_paid_by` ∈ {Customer, Pump, Agency}; company-borne freight
is an internal cost and recognises **no** income. ² Only when the trip's
**paid-from** account is set (a `CarriageTrip.paid_from_account_id` field added
for this). A single trip emits **one composite balanced Journal Voucher** made
of whichever of these three internally-balanced leg-groups apply (`rent_amount`
is excluded from the cash-expense group — it is a payable, not cash).

How it works:

- `JournalEntry` gained `source_type` + `source_id`; generated entries carry the
  source's identity. **Idempotent**: each posting first clears the prior
  voucher+entry for that source, then rebuilds it — so edit re-posts, and
  deactivating (toggle) clears the entry. Manual journal vouchers (NULL source)
  are never touched.
- A transaction posts only when **active and a cash/bank account is set** (both
  sides unambiguous). The poster is invoked from inside the existing
  `_sync_expense` / `_sync_payment` / `_sync_receipt` helpers, so every
  create/edit/toggle path is covered with one hook.
- Accounts resolve by **chart-of-accounts code** (never hard-coded ids); cash
  accounts map to the matching chart account by type. Operational cash/bank
  balances and the journal ledger are written in the same transaction.
- The engine never raises — a posting problem (missing account/business unit,
  unbalanced) silently skips, so it can't break the underlying save.

**Trial Balance** (`/reports/trial-balance`, Excel/PDF export): per-account
debit/credit totals from posted journal lines, with a balanced check (total
debit must equal total credit). This is the live proof the auto-posting is
sound.

The bulk-sale entry is a single balanced 4-line voucher (sale legs + cost legs);
sales/purchases are credit-based (receivable/payable) so there's no cash
ambiguity. Wiring is one hook per flow — for pump purchase and salary it lives
inside the existing `_sync_stock` / `_sync_salary` helper (covers
create/edit/toggle); for bulk/agency it sits before each route's commit.

**Pump retail revenue** posts from the **daily closing** (the one place with both
sale totals and the payment-method breakdown): it Dr's each payment account and
Cr's Fuel + Lubricant Sale. This is the single recognition point — individual
machine readings and lubricant sales are deliberately NOT posted, so revenue
never double-counts. It posts only when the entered payment splits balance the
derived sale totals (a mismatched closing is flagged in the UI and skipped).

The closing also posts the matching **fuel COGS** (Dr Fuel Purchase Cost / Cr
Stock) at the weighted-average cost of fuel sold that day, relieving the Stock
asset that pump purchases built up — so the ledger shows true fuel gross profit.

> Not yet auto-posted: carriage trips (freight payer is ambiguous) and head-office
> cash receipts (internal pump→HO transfer). The engine extends via `sync_<x>` + one hook.

## Customer & Vendor Ledgers (Receivables / Payables)

Accessible by **Admin / Owner, Head Office Manager, Accountant** (the accounting
module). Running balances are computed live from source records — no separate
posting layer to drift out of sync.

| URL | Purpose |
| --- | --- |
| `/accounting/customer-ledgers` | Customer receivable balances |
| `/accounting/customer-ledgers/<id>` | Customer statement (running balance) |
| `/accounting/vendor-ledgers` | Vendor payable balances |
| `/accounting/vendor-ledgers/<id>` | Vendor statement (running balance) |
| `/accounting/customer-receipts` | Record money received from customers |

Balances:

```
customer receivable = opening + bulk sales + agency sales
                      + credit lubricant sales − customer receipts
vendor payable      = opening + pump purchases + bulk purchase cost
                      + agency purchases − vendor payments
```

New model `CustomerReceipt` (in accounting) records collections that reduce a
customer's receivable; if a received-into account is chosen its balance
increases (idempotent, like vendor payments). The Reports dashboard now shows
**exact** total receivables and payables (previously indicative).

**Business-wise statement** (BRD §5.5/§5.6 — "reporting should show business-wise
ledger"): each customer/vendor detail page leads with a statement that breaks the
party's charges down **by business unit** — customers: Bulk Sale / Oil Agency /
Petrol Pump lubricant credit; vendors: Petrol Pump purchases / Bulk purchase cost
/ Oil Agency purchases — each with deal count and amount, then nets opening
balance and global receipts/payments (which aren't tagged to a business unit) to
the same closing balance the chronological ledger derives. `ledger.customer_statement` /
`ledger.vendor_statement`; the closing is asserted equal to `customer_balance` /
`vendor_balance`.

## Core Business Rule

Every transaction in future modules must be linked to the correct business
unit. Petrol pump retail sales, bulk sales, carriage, agency and head office
records must never be mixed incorrectly.
