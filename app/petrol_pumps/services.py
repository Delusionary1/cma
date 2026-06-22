"""Petrol pump daily-operations calculation helpers.

These pull totals from the existing transactional records (machine readings,
lubricant sales, pump expenses) so the daily closing can be derived rather than
hand-typed.
"""
from decimal import Decimal

from app.extensions import db
from app.petrol_pumps.models import (
    LubricantSale,
    MachineReading,
    PumpCarriageCashFeed,
    PumpDailyClosing,
    PumpExpense,
    PumpSalaryPayment,
)

_ZERO = Decimal("0")


def _sum_col(amount_col, *filters):
    query = db.session.query(db.func.coalesce(db.func.sum(amount_col), 0))
    for f in filters:
        query = query.filter(f)
    return Decimal(str(query.scalar()))


def _closing_noncash_total(pump_id):
    """Sum of NON-CASH receipts the pump booked through its active daily
    closings: card + PSO card + Easypaisa + JazzCash + bank transfer + credit
    sale. None of these are physical cash — card/transfer/PSO/wallet land in a
    bank/wallet account and credit sale is a receivable — so they must be taken
    OUT of the cash sitting at the pump (and so head office never collects them
    as cash; they show in the bank/wallet balances instead)."""
    return _sum_col(
        PumpDailyClosing.bank_card_received
        + PumpDailyClosing.pso_card_amount
        + PumpDailyClosing.easypaisa_amount
        + PumpDailyClosing.jazzcash_amount
        + PumpDailyClosing.bank_transfer_amount
        + PumpDailyClosing.credit_sale_amount,
        PumpDailyClosing.petrol_pump_id == pump_id,
        PumpDailyClosing.is_active.is_(True),
    )


def pump_current_cash(pump_id):
    """Physical cash currently sitting at a pump (display figure):

        all sale (fuel + lubricant)
        - non-cash receipts booked in daily closings (card / transfer / PSO
          card / wallet / credit sale)
        - all pump expenses
        - cash already received by head office from this pump
        - cash fed to the carriage business.

    Live/all-time figure (like current tank stock), not period-bound. Until a
    day's closing splits the sale into cash vs non-cash, the whole sale shows as
    cash here; once the closing is entered, card/transfer/PSO/wallet/credit drop
    out automatically.
    """
    from app.head_office.models import HeadOfficeCashReceipt

    fuel = _sum_col(
        MachineReading.sale_amount,
        MachineReading.petrol_pump_id == pump_id,
        MachineReading.is_active.is_(True),
    )
    lube = _sum_col(
        LubricantSale.total_amount,
        LubricantSale.petrol_pump_id == pump_id,
        LubricantSale.is_active.is_(True),
    )
    expense = _sum_col(
        PumpExpense.amount,
        PumpExpense.petrol_pump_id == pump_id,
        PumpExpense.is_active.is_(True),
    )
    # Staff salaries/advances are paid out of the pump's cash, exactly like a
    # pump expense, so they reduce the pump's cash too.
    salary = _sum_col(
        PumpSalaryPayment.amount,
        PumpSalaryPayment.petrol_pump_id == pump_id,
        PumpSalaryPayment.is_active.is_(True),
    )
    received = _sum_col(
        HeadOfficeCashReceipt.amount,
        HeadOfficeCashReceipt.petrol_pump_id == pump_id,
        HeadOfficeCashReceipt.is_active.is_(True),
    )
    fed_to_carriage = _sum_col(
        PumpCarriageCashFeed.amount,
        PumpCarriageCashFeed.petrol_pump_id == pump_id,
        PumpCarriageCashFeed.is_active.is_(True),
    )
    noncash = _closing_noncash_total(pump_id)
    return fuel + lube - noncash - expense - salary - received - fed_to_carriage


def pump_cash_submitted_total(pump_id):
    """Total cash the pump has CONFIRMED to head office through its active daily
    closings (cash_submitted_to_head_office). Zero until a closing is entered."""
    return _sum_col(
        PumpDailyClosing.cash_submitted_to_head_office,
        PumpDailyClosing.petrol_pump_id == pump_id,
        PumpDailyClosing.is_active.is_(True),
    )


def pump_cash_received_total(pump_id):
    """Cash head office has already received from this pump."""
    from app.head_office.models import HeadOfficeCashReceipt
    return _sum_col(
        HeadOfficeCashReceipt.amount,
        HeadOfficeCashReceipt.petrol_pump_id == pump_id,
        HeadOfficeCashReceipt.is_active.is_(True),
    )


def pump_cash_to_collect(pump_id):
    """Cash head office may actually COLLECT from a pump right now.

    Two limits apply, whichever is smaller:
      1. what the pump has confirmed via daily closing
         (cash_submitted_to_head_office) minus what head office already received
         — this keeps the closing gate (zero until a closing exists), and
      2. the pump's live physical cash (`pump_current_cash`), which nets out
         salaries, expenses, carriage feeds etc. as they happen.

    Capping at (2) fixes the glitch where a salary/expense entered AFTER the
    closing left the stored cash_submitted snapshot too high, letting head office
    collect more cash than was physically at the pump (driving it negative).
    """
    confirmed = pump_cash_submitted_total(pump_id) - pump_cash_received_total(pump_id)
    physical = pump_current_cash(pump_id)
    return max(Decimal("0"), min(confirmed, physical))


def calculate_daily_fuel_sales(pump_id, closing_date):
    """Return (fuel_sale_liters, fuel_sale_amount) from active machine readings."""
    row = (
        db.session.query(
            db.func.coalesce(db.func.sum(MachineReading.sale_liters), 0),
            db.func.coalesce(db.func.sum(MachineReading.sale_amount), 0),
        )
        .filter(
            MachineReading.petrol_pump_id == pump_id,
            MachineReading.reading_date == closing_date,
            MachineReading.is_active.is_(True),
        )
        .one()
    )
    return Decimal(str(row[0])), Decimal(str(row[1]))


def calculate_daily_lubricant_sales(pump_id, closing_date):
    """Return total lubricant sale amount from active lubricant sales."""
    total = (
        db.session.query(db.func.coalesce(db.func.sum(LubricantSale.total_amount), 0))
        .filter(
            LubricantSale.petrol_pump_id == pump_id,
            LubricantSale.sale_date == closing_date,
            LubricantSale.is_active.is_(True),
        )
        .scalar()
    )
    return Decimal(str(total))


def calculate_daily_expenses(pump_id, closing_date):
    """Return the day's pump expenses — regular pump expenses PLUS staff
    salary/advance payments, since both are paid out of the pump's cash. Keeping
    salaries here means the daily closing's 'Cash to Head Office' (and so the HO
    collection figure) matches pump_current_cash, which also nets out salaries."""
    expenses = _sum_col(
        PumpExpense.amount,
        PumpExpense.petrol_pump_id == pump_id,
        PumpExpense.expense_date == closing_date,
        PumpExpense.is_active.is_(True),
    )
    salaries = _sum_col(
        PumpSalaryPayment.amount,
        PumpSalaryPayment.petrol_pump_id == pump_id,
        PumpSalaryPayment.payment_date == closing_date,
        PumpSalaryPayment.is_active.is_(True),
    )
    return expenses + salaries


def calculate_daily_closing_summary(pump_id, closing_date, payment_inputs):
    """Build the full daily closing summary as a dict of Decimals/flags.

    The closing answers "where did the day's cash go". The accountant enters only
    the NON-CASH amounts (bank card, PSO card, Easypaisa, JazzCash, bank
    transfer, credit sale); the cash figures are DERIVED:

        total sale      = fuel + lubricant (from entries)
        cash received   = total sale − all non-cash receipts   (the cash portion)
        cash to submit  = cash received − expenses              (physical cash left)

    `payment_inputs` supplies the non-cash amounts (keys: bank_card_received,
    pso_card_amount, easypaisa_amount, jazzcash_amount, bank_transfer_amount,
    credit_sale_amount). cash_received / cash_submitted_to_head_office, if passed,
    are ignored — they are always computed.
    """
    fuel_liters, fuel_amount = calculate_daily_fuel_sales(pump_id, closing_date)
    lubricant_amount = calculate_daily_lubricant_sales(pump_id, closing_date)
    expenses_paid = calculate_daily_expenses(pump_id, closing_date)

    def amt(key):
        return Decimal(str(payment_inputs.get(key) or 0))

    bank_card = amt("bank_card_received")
    pso_card = amt("pso_card_amount")
    easypaisa = amt("easypaisa_amount")
    jazzcash = amt("jazzcash_amount")
    bank_transfer = amt("bank_transfer_amount")
    credit_sale = amt("credit_sale_amount")

    non_cash_total = (
        bank_card + pso_card + easypaisa + jazzcash + bank_transfer + credit_sale
    )
    total_sale_amount = fuel_amount + lubricant_amount

    # Derived cash flow.
    cash_received = total_sale_amount - non_cash_total          # cash portion of sale
    expected_cash_to_submit = cash_received - expenses_paid     # physical cash left
    cash_submitted = expected_cash_to_submit                    # auto — what HO collects

    payment_methods_total = cash_received + non_cash_total      # == total sale by design

    return {
        "fuel_sale_liters": fuel_liters,
        "fuel_sale_amount": fuel_amount,
        "lubricant_sale_amount": lubricant_amount,
        "expenses_paid": expenses_paid,
        "total_sale_amount": total_sale_amount,
        "cash_received": cash_received,
        "bank_card_received": bank_card,
        "pso_card_amount": pso_card,
        "easypaisa_amount": easypaisa,
        "jazzcash_amount": jazzcash,
        "bank_transfer_amount": bank_transfer,
        "credit_sale_amount": credit_sale,
        "non_cash_total": non_cash_total,
        "cash_submitted_to_head_office": cash_submitted,
        "expected_cash_to_submit": expected_cash_to_submit,
        "difference_amount": Decimal("0"),
        "payment_methods_total": payment_methods_total,
        # The only error worth flagging now: non-cash entered exceeds the sale,
        # which would make the cash portion negative.
        "payment_mismatch": non_cash_total > total_sale_amount,
    }
