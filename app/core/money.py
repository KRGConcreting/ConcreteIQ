"""Money utilities. ALL MONEY STORED AS CENTS (integers)."""

from decimal import Decimal, ROUND_HALF_UP


def cents_to_dollars(cents: int) -> Decimal:
    """Convert cents to dollars as Decimal."""
    return Decimal(cents) / 100


def dollars_to_cents(dollars: float | Decimal | str) -> int:
    """
    Convert dollars to cents.
    Always rounds to nearest cent.
    """
    if isinstance(dollars, str):
        dollars = Decimal(dollars)
    elif isinstance(dollars, float):
        dollars = Decimal(str(dollars))
    
    cents = dollars * 100
    return int(cents.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def format_money(cents: int) -> str:
    """
    Format cents as dollar string.
    Example: 334210 -> '$3,342.10'
    """
    dollars = cents / 100
    return f"${dollars:,.2f}"


def format_money_no_symbol(cents: int) -> str:
    """
    Format cents as dollar string without symbol.
    Example: 334210 -> '3,342.10'
    """
    dollars = cents / 100
    return f"{dollars:,.2f}"


def calculate_gst(subtotal_cents: int, gst_rate: int = 10) -> int:
    """
    Calculate GST amount.
    Default rate is 10% (Australia).
    """
    return int(round(subtotal_cents * gst_rate / 100))


def add_gst(subtotal_cents: int, gst_rate: int = 10) -> int:
    """Calculate total including GST."""
    gst = calculate_gst(subtotal_cents, gst_rate)
    return subtotal_cents + gst


def extract_gst(total_cents: int, gst_rate: int = 10) -> tuple[int, int]:
    """
    Extract subtotal and GST from GST-inclusive total.
    Returns (subtotal_cents, gst_cents).
    """
    subtotal = int(round(total_cents * 100 / (100 + gst_rate)))
    gst = total_cents - subtotal
    return subtotal, gst


def calculate_percentage(total_cents: int, percentage: int) -> int:
    """Calculate a percentage of an amount."""
    return int(round(total_cents * percentage / 100))


# Payment split percentages (KRG standard)
PAYMENT_SPLIT = {
    "booking": 30,    # Progress payment on acceptance
    "prepour": 60,    # Pre-pour payment
    "completion": 10, # Final payment
}


def calculate_payment_split(total_cents: int) -> dict[str, int]:
    """
    Calculate 30/60/10 payment split.
    Returns dict with booking, prepour, completion amounts.
    """
    booking = calculate_percentage(total_cents, PAYMENT_SPLIT["booking"])
    prepour = calculate_percentage(total_cents, PAYMENT_SPLIT["prepour"])
    # Completion is remainder to avoid rounding issues
    completion = total_cents - booking - prepour
    
    return {
        "booking": booking,
        "prepour": prepour,
        "completion": completion,
    }
