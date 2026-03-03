"""
Settings Service — Centralized settings management with defaults.

ALL PRICES IN CENTS (ex GST unless noted)
"""

from typing import Any, Optional
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.core.dates import sydney_now


# =============================================================================
# DEFAULT SETTINGS
# =============================================================================
# These are used if no database value exists

DEFAULTS = {
    # =========================================================================
    # PRICING - Concrete Supply (cents per m3 ex GST)
    # =========================================================================
    "pricing.concrete_n20": 23300,
    "pricing.concrete_n25": 23800,
    "pricing.concrete_n32": 24600,
    "pricing.concrete_exposed": 27300,
    "pricing.concrete_yard": "225 Jude Road, Howlong NSW",
    "pricing.concrete_free_km": 20,
    "pricing.concrete_travel_rate": 150,  # cents per km per m3
    "pricing.short_load_threshold": 3.0,  # m3
    "pricing.short_load_fee_per_m3": 5000,  # cents ($50/m3)
    "pricing.concrete_buffer": 0.08,  # 8% wastage

    # =========================================================================
    # PRICING - Mix Additives (cents per m3 ex GST)
    # =========================================================================
    "pricing.additive_accelerator_1": 600,
    "pricing.additive_accelerator_2": 1200,
    "pricing.additive_accelerator_3": 1800,
    "pricing.retarder_cost_per_litre": 500,  # $5.00/L
    "pricing.cement_content_n20": 310,   # kg/m³
    "pricing.cement_content_n25": 340,   # kg/m³
    "pricing.cement_content_n32": 400,   # kg/m³

    # Coloured concrete surcharges (cents per m³ ex GST)
    "pricing.colour_charcoal": 5000,
    "pricing.colour_cinnamon": 4266,
    "pricing.colour_cornstalk": 4594,
    "pricing.colour_daintree": 4758,
    "pricing.colour_copper": 4922,
    "pricing.colour_bluestone": 5625,
    "pricing.colour_earth_brown": 5742,
    "pricing.colour_chardonnay": 5906,
    "pricing.colour_classic_suede": 6070,
    "pricing.colour_billabong": 6234,
    "pricing.colour_kosciusko": 6234,
    "pricing.colour_butterscotch": 6563,
    "pricing.colour_espresso": 6563,
    "pricing.colour_brandy_snap": 6891,
    "pricing.colour_gum_leaf": 6891,
    "pricing.colour_dynasty": 7109,
    "pricing.colour_goanna": 7266,
    "pricing.colour_hidden_valley": 7547,
    "pricing.colour_dusty_rose": 7711,
    "pricing.colour_beach": 7711,
    "pricing.colour_brazil_nut": 8039,
    "pricing.colour_choc_malt": 8695,
    "pricing.colour_flower_gum": 9188,
    "pricing.colour_colonial_sunset": 9844,
    "pricing.colour_black": 10000,
    "pricing.colour_desert_beige": 10336,
    "pricing.colour_bronzed_aussie": 11484,
    "pricing.colour_hawkesbury": 11813,
    "pricing.colour_brick": 12797,
    "pricing.colour_brolga": 13125,
    "pricing.colour_antique_rose": 15422,
    "pricing.colour_jade_green": 15422,

    # =========================================================================
    # PRICING - Reinforcement (all-up cents per m2 ex GST)
    # Derived from real unit prices (inc GST): Bar=$7.37, Tie=$0.089, Chair=$0.46
    # Bar laps factored at 6%. Converted to ex GST below.
    # =========================================================================
    "pricing.reo_gfrp_500mm": 1052,   # $11.57/m² inc GST → 1052c ex
    "pricing.reo_gfrp_450mm": 1182,   # $13.00/m² inc GST → 1182c ex
    "pricing.reo_gfrp_400mm": 1355,   # $14.91/m² inc GST → 1355c ex
    "pricing.reo_gfrp_350mm": 1598,   # $17.58/m² inc GST → 1598c ex
    "pricing.reo_sl72_mesh": 659,
    "pricing.reo_sl82_mesh": 815,
    "pricing.reo_sl92_mesh": 987,
    "pricing.reo_sl102_mesh": 1414,

    # =========================================================================
    # PRICING - Dowels (cents per lineal metre ex GST)
    # =========================================================================
    "pricing.dowel_gfrp_350mm": 137,
    "pricing.dowel_gfrp_400mm": 120,
    "pricing.dowel_gfrp_450mm": 107,
    "pricing.dowel_gfrp_500mm": 96,
    "pricing.dowel_steel_400mm": 325,
    "pricing.steel_delivery": 5000,  # cents flat ($50)

    # =========================================================================
    # PRICING - Control Joints (cents per lm ex GST - includes labour)
    # =========================================================================
    "pricing.control_joint_sawcut": 800,
    "pricing.control_joint_tooled": 800,

    # =========================================================================
    # PRICING - Chemicals (cents per m2 ex GST)
    # =========================================================================
    "pricing.chemical_release_agent": 30,
    "pricing.chemical_evap_retarder": 75,
    "pricing.chemical_curing": 217,
    "pricing.chemical_hardener": 280,
    "pricing.chemical_surface_retarder": 205,
    "pricing.chemical_sealer": 300,
    "pricing.chemical_acid_wash": 300,
    "pricing.chemical_equipment": 10,

    # =========================================================================
    # PRICING - Materials (cents ex GST)
    # =========================================================================
    "pricing.material_moisture_barrier": 60,  # per m2
    "pricing.material_isolation_joint": 207,  # per lm
    "pricing.material_fence_sheeting": 700,  # per lm
    "pricing.material_formwork_depreciation": 30,  # per lm
    "pricing.material_subbase_sand": 354,  # per m2 at 37.5mm
    "pricing.material_subbase_delivery": 5000,  # flat fee ($50) for ≤20km
    "pricing.material_subbase_delivery_radius_km": 20,  # free delivery radius
    "pricing.material_subbase_delivery_per_km": 150,  # cents/km beyond radius ($1.50)
    "pricing.material_pump_per_sqm": 3273,  # per m2
    "pricing.material_step_materials": 7727,  # per step
    "pricing.material_step_hours": 1.5,  # hours per step
    "pricing.material_rebate_per_lm": 1700,  # per lm - formwork + reo for stepped edge
    "pricing.material_pier_sono_tube": 2000,  # per pier - cardboard form tube
    "pricing.material_pier_starter_bar": 700,  # per bar - N12 reo
    "pricing.material_pier_labour_hrs": 0.6,  # hrs per pier
    "pricing.material_edge_beam_reo": 1000,  # per lm - ligatures + bars

    # =========================================================================
    # PRICING - Plumbing & Drainage (cents ex GST)
    # =========================================================================
    "pricing.drain_pit_300": 43750,           # per pit - 300×300mm supply + concreted in (base+25%)
    "pricing.drain_pit_450": 68750,           # per pit - 450×450mm supply + concreted in (base+25%)
    "pricing.drain_centralising_pit": 56250,  # per pit - supply + concreted in (base+25%)
    "pricing.drain_grate_standard": 22500,    # per grate - standard channel concreted in (base+25%)
    "pricing.drain_grate_heavy": 35000,       # per grate - heavy duty concreted in (base+25%)
    "pricing.drain_surface_drain_lm": 15000,  # per lm - strip drain concreted into slab (base+25%)
    "pricing.drain_ag_pipe_90_lm": 4375,      # per lm - 90mm ag pipe supply + install (base+25%)
    "pricing.drain_stormwater_100_lm": 6875,  # per lm - 100mm stormwater pipe (base+25%)
    "pricing.drain_stormwater_150_lm": 9375,  # per lm - 150mm stormwater pipe (base+25%)
    "pricing.drain_tpiece_junction": 10625,   # per connection - T-piece / junction (base+25%)
    "pricing.drain_trench_lm": 5625,          # per lm - drain trench excavation (base+25%)
    "pricing.drain_gravel_lm": 3125,          # per lm - gravel/aggregate bedding (base+25%)
    "pricing.drain_relocate": 43750,          # per item - relocate existing drain (base+25%)
    "pricing.drain_labour_hr": 12500,         # per hour - drainage labour (base+25%)

    # =========================================================================
    # PRICING - Excavation (cents ex GST) - volume-based flat rates
    # =========================================================================
    "pricing.excavation_3m3": 120000,
    "pricing.excavation_4m3": 135000,
    "pricing.excavation_5m3": 145000,
    "pricing.excavation_6m3": 155000,
    "pricing.excavation_7m3": 170000,
    "pricing.excavation_8m3": 185000,
    "pricing.excavation_9m3": 200000,
    "pricing.excavation_over_9_base": 200000,
    "pricing.excavation_per_extra_m3": 20000,

    # =========================================================================
    # PRICING - Productivity Rates (matching pricing.py PRODUCTIVITY dict)
    # =========================================================================
    "pricing.productivity_boxing": 19,              # lm/hr
    "pricing.productivity_sand_compact": 10,        # m²/hr
    "pricing.productivity_plate_compaction": 20,    # m²/hr (3 min/m²)
    "pricing.productivity_hand_dig": 0.38,          # m³/hr
    "pricing.productivity_machine_dig": 1.0,        # m³/hr
    "pricing.productivity_fence_sheeting": 20,      # lm/hr
    "pricing.productivity_sawcut": 25,              # lm/hr
    "pricing.productivity_isolation_joint": 30,     # lm/hr
    "pricing.productivity_rebates": 10,             # lm/hr
    "pricing.productivity_dowels": 30,              # dowels/hr
    "pricing.productivity_moisture_barrier_full": 30, # m²/hr
    "pricing.productivity_sealer": 50,              # m²/hr

    # Pour rate
    "pricing.pour_minutes_per_sqm": 11,  # flat rate
    "pricing.pour_minimum_hours": 6.0,

    # =========================================================================
    # PRICING - Crew Rates (cents per hour)
    # =========================================================================
    "pricing.crew_owner_base": 9400,
    "pricing.crew_owner_loaded": 9400,
    "pricing.crew_owner_sell": 9400,           # $/hr sell rate ($94/hr) — Head Concretor
    "pricing.crew_finisher_base": 3463,
    "pricing.crew_finisher_loaded": 4514,
    "pricing.crew_finisher_sell": 5450,        # $/hr sell rate ($54.50/hr) — Concretor
    "pricing.crew_exp_labourer_base": 3398,
    "pricing.crew_exp_labourer_loaded": 4433,
    "pricing.crew_exp_labourer_sell": 5350,    # $/hr sell rate ($53.50/hr)
    "pricing.crew_labourer_base": 3238,
    "pricing.crew_labourer_loaded": 4235,
    "pricing.crew_labourer_sell": 5100,        # $/hr sell rate ($51.00/hr)

    # Team tier pricing (cents per hour and per m2)
    "pricing.team_lean_hourly": 14500,         # $145/hr
    "pricing.team_lean_sqm": 3200,             # $32/m²
    "pricing.team_standard_hourly": 14750,     # $147.50/hr
    "pricing.team_standard_sqm": 3300,         # $33/m²
    "pricing.team_premium_hourly": 16500,      # $165/hr
    "pricing.team_premium_sqm": 3700,          # $37/m²

    # =========================================================================
    # PRICING - Markup Tiers (percentages as decimals)
    # =========================================================================
    "pricing.markup_economy_materials": 0.05,   # 5%
    "pricing.markup_economy_labour": 0.05,      # 5%
    "pricing.markup_standard_materials": 0.10,  # 10%
    "pricing.markup_standard_labour": 0.08,     # 8%
    "pricing.markup_premium_materials": 0.15,   # 15%
    "pricing.markup_premium_labour": 0.10,      # 10%

    # Material markup by cost tier (JSON)
    "pricing.material_markup_tiers": [
        {"max": 500, "Economy": 0.25, "Standard": 0.30, "Premium": 0.35},
        {"max": 1500, "Economy": 0.20, "Standard": 0.25, "Premium": 0.30},
        {"max": 5000, "Economy": 0.15, "Standard": 0.20, "Premium": 0.25},
        {"max": 15000, "Economy": 0.125, "Standard": 0.15, "Premium": 0.185},
        {"max": 99999999, "Economy": 0.08, "Standard": 0.10, "Premium": 0.125},
    ],

    # =========================================================================
    # PRICING - Overhead & Travel (cents)
    # =========================================================================
    "pricing.overhead_insurance": 150,  # per m2 ($1.50 — $150/month)
    "pricing.overhead_xero": 75,  # per m2 ($0.75 — $75/month)
    "pricing.overhead_software": 40,  # per m2 ($0.40 — $40/month)
    "pricing.overhead_equipment": 5,  # per m2 ($0.05 — $5/month)
    "pricing.overhead_accountant": 30,  # per m2 ($0.30 — $30/month)
    "pricing.overhead_base_area": 100,  # m2 for minimum calc
    "pricing.overhead_minimum": 300,  # minimum cents per m2 ($3.00/m2)

    "pricing.travel_free_km": 0,
    "pricing.travel_rate": 130,  # cents per km (total distance)

    # =========================================================================
    # PRICING - Complexity Multipliers
    # =========================================================================
    "pricing.complexity_easy": 0.90,
    "pricing.complexity_standard": 1.00,
    "pricing.complexity_complex": 1.15,
    "pricing.complexity_very_complex": 1.30,

    "pricing.buffer_easy": 0.05,
    "pricing.buffer_standard": 0.10,
    "pricing.buffer_complex": 0.15,
    "pricing.buffer_very_complex": 0.20,
    "pricing.setup_buffer": 0.10,

    # =========================================================================
    # PRICING - Tax & Payroll
    # =========================================================================
    "pricing.gst_rate": 0.10,
    "pricing.super_rate": 0.12,
    "pricing.payg_rate": 0.17,
    "pricing.workcover_rate": 0.085,
    "pricing.minimum_quote": 165000,  # cents inc GST ($1,650)

    # =========================================================================
    # BUSINESS DETAILS
    # =========================================================================
    "business.name": "Kyle R Gyoles Concreting",
    "business.trading_as": "KRG Concreting",
    "business.abn": "76 993 685 401",
    "business.phone": "0423 005 129",
    "business.email": "kyle@krgconcreting.au",
    "business.address_line1": "Albury-Wodonga",
    "business.address_line2": "NSW/VIC",
    "business.website": "www.krgconcreting.au",

    # Bank Details
    "business.bank_name": "Great Southern Bank",
    "business.bank_bsb": "",
    "business.bank_account": "",
    "business.bank_account_name": "",

    # =========================================================================
    # SMS - Vonage
    # =========================================================================
    "sms.provider": "vonage",
    "sms.vonage_api_key": "",
    "sms.vonage_api_secret": "",
    "sms.vonage_from_number": "",
    "sms.enabled": False,

    # =========================================================================
    # EMAIL - Resend
    # =========================================================================
    "email.from_address": "quotes@krgconcreting.au",
    "email.from_name": "KRG Concreting",
    "email.reply_to": "kyle@krgconcreting.au",
    "email.enabled": True,

    # =========================================================================
    # QUOTATION SETTINGS
    # =========================================================================
    "quotation.default_expiry_days": 30,
    "quotation.default_markup_tier": "Standard",
    "quotation.include_gst": True,
    "quotation.terms_pdf_path": "/static/documents/tcs/KRG_Terms_and_Conditions_v3.1.pdf",
    "quotation.show_line_items": True,
    "quotation.show_breakdown": False,

    # =========================================================================
    # INVOICE SETTINGS
    # =========================================================================
    "invoice.payment_terms_days": 14,
    "invoice.deposit_percent": 30,
    "invoice.progress_percent": 60,
    "invoice.final_percent": 10,
    "invoice.late_fee_percent": 2,
    "invoice.show_bank_details": True,
    "invoice.deposit_due": "on_acceptance",    # on_acceptance, 7, 14
    "invoice.prepour_due": "before_pour",      # before_pour, 7, 14
    "invoice.final_due": "on_completion",      # on_completion, 7, 14, 30

    # =========================================================================
    # REMINDER SETTINGS (outbound to customers)
    # =========================================================================
    "reminders.payment_before_days": 3,
    "reminders.payment_on_due": True,
    "reminders.payment_after_days": [3, 7, 14],
    "reminders.job_before_days": [7, 1],
    "reminders.send_sms": False,
    "reminders.send_email": True,
    "reminders.payment_mode": "manual",  # "manual" = notify Kyle, "auto" = send directly

    # =========================================================================
    # IN-APP NOTIFICATION PREFERENCES
    # =========================================================================
    "reminders.notify_quote_sent": True,
    "reminders.notify_quote_viewed": True,
    "reminders.notify_quote_accepted": True,
    "reminders.notify_payments": True,
    "reminders.notify_email_tracking": True,
    "reminders.followup_enabled": True,
    "reminders.followup_days": [3, 7, 14],

    # =========================================================================
    # LABOUR DEFAULTS
    # =========================================================================
    "labour.default_hourly_rate": 5000,  # cents
    "labour.setup_hours_base": 4,
    "labour.pour_rate_m2_hr": 15,

    # =========================================================================
    # OWNER GOALS
    # =========================================================================
    "goals.goal_type": "weekly",  # weekly, fortnightly, monthly
    "goals.goal_amount_cents": 180000,  # $1,800 default weekly take-home target
}


# =============================================================================
# SERVICE FUNCTIONS
# =============================================================================

async def get_setting(
    db: AsyncSession,
    category: str,
    key: str,
    default: Any = None
) -> Any:
    """Get a single setting value."""
    full_key = f"{category}.{key}"

    result = await db.execute(
        select(Setting).where(Setting.category == category, Setting.key == key)
    )
    setting = result.scalar_one_or_none()

    if setting:
        return _parse_value(setting.value, setting.value_type)

    # Return from defaults
    return DEFAULTS.get(full_key, default)


async def get_settings_by_category(db: AsyncSession, category: str) -> dict:
    """Get all settings in a category."""
    result = await db.execute(
        select(Setting).where(Setting.category == category)
    )
    settings = result.scalars().all()

    # Start with defaults for this category
    output = {}
    for k, v in DEFAULTS.items():
        if k.startswith(f"{category}."):
            # Extract just the key part after category.
            setting_key = k.split('.', 1)[1]
            output[setting_key] = v

    # Override with DB values
    for s in settings:
        output[s.key] = _parse_value(s.value, s.value_type)

    return output


async def get_all_pricing(db: AsyncSession) -> dict:
    """Get all pricing settings as a flat dict."""
    return await get_settings_by_category(db, 'pricing')


async def set_setting(
    db: AsyncSession,
    category: str,
    key: str,
    value: Any
) -> Setting:
    """Set a setting value."""
    value_type = _detect_type(value)
    value_str = _serialize_value(value, value_type)

    result = await db.execute(
        select(Setting).where(Setting.category == category, Setting.key == key)
    )
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value_str
        setting.value_type = value_type
        setting.updated_at = sydney_now()
    else:
        setting = Setting(
            category=category,
            key=key,
            value=value_str,
            value_type=value_type
        )
        db.add(setting)

    await db.flush()
    return setting


async def set_settings_bulk(
    db: AsyncSession,
    category: str,
    data: dict
):
    """Set multiple settings at once."""
    for key, value in data.items():
        await set_setting(db, category, key, value)


async def get_bank_details(db: AsyncSession) -> dict:
    """Get bank details from database settings (not from env/config)."""
    business = await get_settings_by_category(db, 'business')
    return {
        "bank_name": business.get("bank_name") or "Great Southern Bank",
        "bank_account_name": business.get("bank_account_name") or "",
        "bank_bsb": business.get("bank_bsb") or "",
        "bank_account": business.get("bank_account") or "",
    }


async def get_business_dict(db: AsyncSession, include_bank: bool = True) -> dict:
    """Build a complete business info dict from database settings."""
    business = await get_settings_by_category(db, 'business')
    biz = {
        "name": business.get("name") or "KRG Concreting",
        "trading_as": business.get("trading_as") or business.get("name") or "KRG Concreting",
        "abn": business.get("abn") or "",
        "address": business.get("address_line1") or "",
        "phone": business.get("phone") or "",
        "email": business.get("email") or "",
    }
    if include_bank:
        biz["bank_name"] = business.get("bank_name") or "Great Southern Bank"
        biz["bank_account_name"] = business.get("bank_account_name") or ""
        biz["bank_bsb"] = business.get("bank_bsb") or ""
        biz["bsb"] = business.get("bank_bsb") or ""
        biz["bank_account"] = business.get("bank_account") or ""
        biz["account"] = business.get("bank_account") or ""
    return biz


async def delete_setting(db: AsyncSession, category: str, key: str) -> bool:
    """Delete a setting (reverts to default)."""
    result = await db.execute(
        select(Setting).where(Setting.category == category, Setting.key == key)
    )
    setting = result.scalar_one_or_none()

    if setting:
        await db.delete(setting)
        return True
    return False


async def reset_category(db: AsyncSession, category: str) -> int:
    """Delete all settings in a category (revert to defaults)."""
    from sqlalchemy import delete
    result = await db.execute(
        delete(Setting).where(Setting.category == category)
    )
    return result.rowcount


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _parse_value(value: Optional[str], value_type: str) -> Any:
    """Parse stored value based on type."""
    if value is None:
        return None

    if value_type == 'int':
        return int(value)
    elif value_type == 'float':
        return float(value)
    elif value_type == 'bool':
        return value.lower() in ('true', '1', 'yes')
    elif value_type == 'json':
        return json.loads(value)

    return value


def _serialize_value(value: Any, value_type: str) -> str:
    """Serialize value for storage."""
    if value_type == 'json':
        return json.dumps(value)
    elif value_type == 'bool':
        return 'true' if value else 'false'
    return str(value)


def _detect_type(value: Any) -> str:
    """Detect value type for storage."""
    if isinstance(value, bool):
        return 'bool'
    elif isinstance(value, int):
        return 'int'
    elif isinstance(value, float):
        return 'float'
    elif isinstance(value, (dict, list)):
        return 'json'
    return 'string'


# =============================================================================
# SYNC HELPERS (for pricing.py compatibility)
# =============================================================================

def get_default(full_key: str, default: Any = None) -> Any:
    """Get a default value synchronously (no DB)."""
    return DEFAULTS.get(full_key, default)


def get_defaults_by_category(category: str) -> dict:
    """Get all defaults for a category synchronously."""
    output = {}
    for k, v in DEFAULTS.items():
        if k.startswith(f"{category}."):
            setting_key = k.split('.', 1)[1]
            output[setting_key] = v
    return output
