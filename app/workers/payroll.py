"""
PAYG Tax Withholding Calculator for Australian Workers.

Based on ATO tax tables 2025-26.
Calculates withholding based on projected annual income using tax brackets,
rather than a flat rate.
"""

from decimal import Decimal
from enum import Enum


class PayFrequency(Enum):
    WEEKLY = 52
    FORTNIGHTLY = 26
    MONTHLY = 12


# Australian tax brackets 2025-26
TAX_BRACKETS = [
    {"min": 0, "max": 18200, "rate": 0, "base": 0},
    {"min": 18201, "max": 45000, "rate": 0.19, "base": 0},
    {"min": 45001, "max": 135000, "rate": 0.30, "base": 5092},
    {"min": 135001, "max": 190000, "rate": 0.37, "base": 32092},
    {"min": 190001, "max": float('inf'), "rate": 0.45, "base": 52442},
]

# Medicare levy (2%)
MEDICARE_LEVY_RATE = 0.02


def calculate_annual_tax(annual_income: Decimal) -> Decimal:
    """Calculate annual tax based on income brackets."""
    for bracket in TAX_BRACKETS:
        if bracket["min"] <= annual_income <= bracket["max"]:
            taxable_above_threshold = annual_income - bracket["min"]
            tax = Decimal(bracket["base"]) + (taxable_above_threshold * Decimal(bracket["rate"]))
            return tax
    return Decimal(0)


def calculate_payg_withholding(
    gross_pay: Decimal,
    pay_frequency: PayFrequency,
    claims_tax_free_threshold: bool = True,
) -> Decimal:
    """
    Calculate PAYG withholding for a worker.

    Args:
        gross_pay: Gross pay for this period (before tax)
        pay_frequency: How often the worker is paid (weekly/fortnightly/monthly)
        claims_tax_free_threshold: Whether worker claims tax-free threshold

    Returns:
        Amount to withhold for PAYG tax
    """
    if gross_pay <= 0:
        return Decimal("0.00")

    # Project annual income based on this pay period
    projected_annual_income = gross_pay * pay_frequency.value

    # If NOT claiming tax-free threshold, withhold at higher rate from dollar one
    if not claims_tax_free_threshold:
        withholding = gross_pay * Decimal("0.32")
        medicare = gross_pay * Decimal(str(MEDICARE_LEVY_RATE))
        return (withholding + medicare).quantize(Decimal("0.01"))

    # Calculate annual tax if claiming threshold
    annual_tax = calculate_annual_tax(projected_annual_income)

    # Add Medicare levy (2% of total income)
    medicare_levy = projected_annual_income * Decimal(str(MEDICARE_LEVY_RATE))

    # Total annual tax + Medicare
    total_annual_withholding = annual_tax + medicare_levy

    # Convert to per-period withholding
    withholding_this_period = total_annual_withholding / pay_frequency.value

    # Round to nearest cent
    return withholding_this_period.quantize(Decimal("0.01"))


def get_withholding_percentage(
    gross_pay: Decimal,
    pay_frequency: PayFrequency,
    claims_tax_free_threshold: bool = True,
) -> Decimal:
    """
    Get the effective withholding percentage for display purposes.

    Returns:
        Percentage as decimal (e.g., 0.14 for 14%)
    """
    if gross_pay <= 0:
        return Decimal(0)
    withholding = calculate_payg_withholding(gross_pay, pay_frequency, claims_tax_free_threshold)
    return (withholding / gross_pay).quantize(Decimal("0.0001"))


def calculate_payg_for_period_wages(
    period_wages_cents: int,
    pay_frequency_str: str = "weekly",
    claims_tax_free_threshold: bool = True,
) -> int:
    """
    Convenience function for use in take-home calculations.

    Takes wages in cents (as used throughout ConcreteIQ) and returns
    PAYG withholding in cents.

    Args:
        period_wages_cents: Gross wages for the period in cents
        pay_frequency_str: "weekly", "fortnightly", or "monthly"
        claims_tax_free_threshold: Whether worker claims tax-free threshold

    Returns:
        PAYG withholding amount in cents
    """
    freq_map = {
        "weekly": PayFrequency.WEEKLY,
        "fortnightly": PayFrequency.FORTNIGHTLY,
        "monthly": PayFrequency.MONTHLY,
    }
    pay_frequency = freq_map.get(pay_frequency_str, PayFrequency.WEEKLY)

    gross_pay = Decimal(period_wages_cents) / 100
    withholding = calculate_payg_withholding(gross_pay, pay_frequency, claims_tax_free_threshold)
    return int(withholding * 100)
