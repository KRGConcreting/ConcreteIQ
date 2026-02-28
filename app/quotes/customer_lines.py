"""
Customer-facing line item generation for Quote Preview.

Transforms calculator results into editable grouped line items
that the customer sees on the quote. Internal cost data is NOT
exposed — this is purely a presentation layer.
"""

from uuid import uuid4
from typing import Optional


# GST rate (matches pricing.py)
GST_RATE = 0.10


def generate_customer_line_items(
    calculator_result: dict,
    calculator_input: dict,
) -> list[dict]:
    """
    Build default customer-facing line item groups from calculator output.

    Each group has a category name, sub-item descriptions, a total price,
    and a toggle for showing individual sub-item prices.

    Args:
        calculator_result: The CalculatorResult.to_dict() output
        calculator_input: The raw calculator input dict

    Returns:
        List of group dicts ready to store as customer_line_items
    """
    groups = []
    sort_order = 0

    # === 1. CONCRETE ===
    # concrete_cost_cents already includes travel, additive, short load,
    # colour surcharge, and fibre cost — do NOT add them again.
    concrete_total = calculator_result.get("concrete_cost_cents") or 0

    if concrete_total > 0:
        sub_items = []

        # Grade description
        grade = calculator_input.get("concrete_grade", "N25")
        sub_items.append({
            "description": f"{grade} ready-mix concrete",
            "price_cents": None,
        })

        # Volume
        volume = calculator_result.get("volume_m3", 0)
        if volume > 0:
            sub_items.append({
                "description": f"{volume:.2f}m³ (includes 8% wastage buffer)",
                "price_cents": None,
            })

        # Coloured concrete
        colour = calculator_input.get("concrete_colour", "")
        if calculator_input.get("coloured_concrete") and colour:
            sub_items.append({
                "description": f"Colour oxide: {colour}",
                "price_cents": None,
            })

        # Mix additive
        additive = calculator_input.get("mix_additive", "None")
        if additive and additive != "None":
            sub_items.append({
                "description": f"Mix additive: {additive}",
                "price_cents": None,
            })

        # Concrete delivery
        concrete_distance = calculator_input.get("concrete_distance_km", 0)
        if concrete_distance and concrete_distance > 0:
            sub_items.append({
                "description": f"Concrete delivery ({concrete_distance}km)",
                "price_cents": None,
            })

        # Short load fee
        if calculator_result.get("short_load_fee_cents", 0) > 0:
            sub_items.append({
                "description": "Small load surcharge",
                "price_cents": None,
            })

        groups.append({
            "id": str(uuid4()),
            "category": "Concrete Supply & Delivery",
            "sub_items": sub_items,
            "total_cents": concrete_total,
            "show_sub_prices": False,
            "sort_order": sort_order,
        })
        sort_order += 1

    # === 2. SITE PREPARATION (excavation + removal + subbase) ===
    show_exc = calculator_result.get("show_excavation", False)
    show_rem = calculator_result.get("show_removal", False)
    show_sub = calculator_result.get("show_subbase", False)

    site_prep_total = (
        (calculator_result.get("excavation_cost_cents") or 0) +
        (calculator_result.get("removal_cost_cents") or 0) +
        (calculator_result.get("subbase_cost_cents") or 0)
    )

    if site_prep_total > 0 and (show_exc or show_rem or show_sub):
        sub_items = []

        if show_exc:
            exc_depth = calculator_input.get("excavation_depth", 0)
            area = calculator_input.get("slab_area", 0)
            sub_items.append({
                "description": f"Excavation to {exc_depth}mm depth ({area}m²)",
                "price_cents": None,
            })

        if show_rem:
            rem_area = calculator_input.get("removal_area", 0)
            rem_thick = int(calculator_input.get("removal_thickness", 100))
            rem_reinforced = calculator_input.get("removal_reinforced", False)
            reo_note = ", reinforced" if rem_reinforced else ""
            sub_items.append({
                "description": f"Existing concrete removal ({rem_area}m² × {rem_thick}mm{reo_note})",
                "price_cents": None,
            })
            # Disposal detail
            rem_method = calculator_input.get("removal_method", "manual")
            rem_disposal = calculator_input.get("removal_disposal", "skip_bin")
            method_label = "machine" if rem_method == "machine" else "manual"
            disposal_labels = {"skip_bin": "skip bin", "trailer": "trailer to tip"}
            disposal_label = disposal_labels.get(rem_disposal, "skip bin")
            sub_items.append({
                "description": f"Incl. sawcutting, {method_label} removal & {disposal_label} disposal",
                "price_cents": None,
            })

        if show_sub:
            sub_thick = calculator_input.get("subbase_thickness", 0)
            sub_items.append({
                "description": f"Subbase preparation ({sub_thick}mm compacted fill)",
                "price_cents": None,
            })

            if calculator_input.get("compaction", False):
                sub_items.append({
                    "description": "Mechanical compaction",
                    "price_cents": None,
                })

        groups.append({
            "id": str(uuid4()),
            "category": "Site Preparation",
            "sub_items": sub_items,
            "total_cents": site_prep_total,
            "show_sub_prices": False,
            "sort_order": sort_order,
        })
        sort_order += 1

    # === PLUMBING & DRAINAGE ===
    show_drainage = calculator_result.get("show_drainage", False)
    drainage_total = calculator_result.get("drainage_cost_cents") or 0

    if show_drainage and drainage_total > 0:
        sub_items = []
        ci = calculator_input  # shorthand

        # Pits
        total_pits = (ci.get("drain_pits_300", 0) + ci.get("drain_pits_450", 0) + ci.get("drain_centralising_pits", 0))
        if total_pits > 0:
            pit_details = []
            if ci.get("drain_pits_300", 0):
                pit_details.append(f"{ci['drain_pits_300']}× 300mm")
            if ci.get("drain_pits_450", 0):
                pit_details.append(f"{ci['drain_pits_450']}× 450mm")
            if ci.get("drain_centralising_pits", 0):
                pit_details.append(f"{ci['drain_centralising_pits']}× centralising")
            sub_items.append({"description": f"Stormwater pits ({', '.join(pit_details)})", "price_cents": None})

        # Grates
        total_grates = ci.get("drain_grates_standard", 0) + ci.get("drain_grates_heavy", 0)
        if total_grates > 0:
            sub_items.append({"description": f"Drainage grates ({total_grates})", "price_cents": None})

        # Surface drain
        if ci.get("drain_surface_drain_lm", 0) > 0:
            sub_items.append({"description": f"Surface drain ({ci['drain_surface_drain_lm']}lm)", "price_cents": None})

        # Pipe
        if ci.get("drain_ag_pipe_lm", 0) > 0:
            sub_items.append({"description": f"90mm ag pipe ({ci['drain_ag_pipe_lm']}lm)", "price_cents": None})
        if ci.get("drain_stormwater_100_lm", 0) > 0:
            sub_items.append({"description": f"100mm stormwater pipe ({ci['drain_stormwater_100_lm']}lm)", "price_cents": None})
        if ci.get("drain_stormwater_150_lm", 0) > 0:
            sub_items.append({"description": f"150mm stormwater pipe ({ci['drain_stormwater_150_lm']}lm)", "price_cents": None})

        # Other items
        if ci.get("drain_tpiece_connections", 0) > 0:
            sub_items.append({"description": f"Junction connections ({ci['drain_tpiece_connections']})", "price_cents": None})
        if ci.get("drain_trench_lm", 0) > 0:
            sub_items.append({"description": f"Trench excavation ({ci['drain_trench_lm']}lm)", "price_cents": None})
        if ci.get("drain_relocations", 0) > 0:
            sub_items.append({"description": f"Drain relocation ({ci['drain_relocations']})", "price_cents": None})

        groups.append({
            "id": str(uuid4()),
            "category": "Plumbing & Drainage",
            "sub_items": sub_items or [{"description": "Drainage works", "price_cents": None}],
            "total_cents": drainage_total,
            "show_sub_prices": False,
            "sort_order": sort_order,
        })
        sort_order += 1

    # === 3. SETUP ===
    setup_total = (
        (calculator_result.get("setup_materials_cents") or 0) +
        (calculator_result.get("setup_labour_cents") or 0)
    )

    if setup_total > 0:
        sub_items = []

        # Reinforcement
        reo = calculator_input.get("reinforcement", "None")
        if reo and reo != "None":
            area = calculator_input.get("slab_area", 0)
            sub_items.append({
                "description": f"{reo} reinforcement ({area}m²)",
                "price_cents": None,
            })

        # Edge formwork
        edge_fw = calculator_input.get("edge_formwork", 0)
        int_fw = calculator_input.get("internal_formwork", 0)
        total_fw = (edge_fw or 0) + (int_fw or 0)
        if total_fw > 0:
            sub_items.append({
                "description": f"Formwork ({total_fw}lm)",
                "price_cents": None,
            })

        # Moisture barrier
        if calculator_input.get("inc_moisture_barrier", False):
            sub_items.append({
                "description": "Moisture barrier (vapour barrier)",
                "price_cents": None,
            })

        # Isolation joints
        iso = calculator_input.get("isolation_joints", 0)
        if iso and iso > 0:
            sub_items.append({
                "description": f"Isolation joints ({iso}lm)",
                "price_cents": None,
            })

        # Fence sheeting
        bp = calculator_input.get("fence_sheeting", 0)
        if bp and bp > 0:
            sub_items.append({
                "description": f"Fence sheeting ({bp}lm)",
                "price_cents": None,
            })

        # Steps
        steps = calculator_input.get("steps", 0)
        if steps and steps > 0:
            sub_items.append({
                "description": f"Step formation ({steps} step{'s' if steps > 1 else ''})",
                "price_cents": None,
            })

        # Dowels
        dowel_count = calculator_result.get("dowel_count", 0)
        if dowel_count and dowel_count > 0:
            sub_items.append({
                "description": f"Dowel bars ({dowel_count} bars)",
                "price_cents": None,
            })

        # Rebates (starter bars)
        rebate_lm = calculator_input.get("rebates", 0)
        if rebate_lm and rebate_lm > 0:
            sub_items.append({
                "description": f"Rebates — formed step with starter bars ({rebate_lm}lm)",
                "price_cents": None,
            })

        # Pier holes
        piers = calculator_input.get("pier_holes", 0)
        if piers and piers > 0:
            dia = calculator_input.get("pier_diameter", 300)
            depth = calculator_input.get("pier_depth", 600)
            bars = calculator_input.get("pier_starters", 4)
            sub_items.append({
                "description": f"Pier holes ({piers}× {int(dia)}mm dia × {int(depth)}mm deep, {bars} starter bars each)",
                "price_cents": None,
            })

        # Edge beams
        if calculator_input.get("edge_beams") and calculator_input.get("edge_beam_length", 0) > 0:
            beam_len = calculator_input.get("edge_beam_length", 0)
            beam_depth = calculator_input.get("edge_beam_depth", 200)
            beam_width = calculator_input.get("edge_beam_width", 300)
            sub_items.append({
                "description": f"Edge beams ({beam_len}lm × {int(beam_width)}mm × {int(beam_depth)}mm) with reinforcement",
                "price_cents": None,
            })

        # Setup labour
        setup_hrs = calculator_result.get("setup_hours", 0)
        if setup_hrs > 0:
            sub_items.append({
                "description": f"Setup labour ({setup_hrs:.1f} hrs)",
                "price_cents": None,
            })

        # Site prep + mobilisation
        sub_items.append({
            "description": "Site preparation & mobilisation",
            "price_cents": None,
        })

        groups.append({
            "id": str(uuid4()),
            "category": "Setup",
            "sub_items": sub_items,
            "total_cents": setup_total,
            "show_sub_prices": False,
            "sort_order": sort_order,
        })
        sort_order += 1

    # === 4. POUR & FINISH ===
    pour_total = (
        (calculator_result.get("pour_materials_cents") or 0) +
        (calculator_result.get("pour_labour_cents") or 0) +
        (calculator_result.get("finish_materials_cents") or 0) +
        (calculator_result.get("finish_labour_cents") or 0)
    )

    if pour_total > 0:
        sub_items = []

        sub_items.append({
            "description": "Concrete placement & finishing",
            "price_cents": None,
        })

        # Placement method
        if calculator_input.get("placement_method") == "Wheelbarrow":
            sub_items.append({
                "description": "Wheelbarrow placement (extended pour time)",
                "price_cents": None,
            })

        # Falls complexity
        falls_pct = calculator_input.get("falls_complexity_pct", 0)
        if falls_pct and falls_pct > 0:
            sub_items.append({
                "description": f"Complex drainage falls (+{int(falls_pct)}% pour time)",
                "price_cents": None,
            })

        # Chemicals/inclusions
        inclusion_items = []
        if calculator_input.get("inc_release_agent"):
            inclusion_items.append("form release agent")
        if calculator_input.get("inc_evap_retarder"):
            inclusion_items.append("evaporation retarder")
        if calculator_input.get("inc_curing_compound"):
            inclusion_items.append("curing compound")
        if calculator_input.get("inc_durability_enhancer"):
            inclusion_items.append("durability enhancer")
        if calculator_input.get("inc_surface_retarder"):
            inclusion_items.append("surface retarder")
        if calculator_input.get("inc_sealer"):
            inclusion_items.append("penetrating sealer")

        if inclusion_items:
            sub_items.append({
                "description": f"Includes: {', '.join(inclusion_items)}",
                "price_cents": None,
            })

        # Control joints
        cj = calculator_input.get("control_joints", 0)
        cj_method = calculator_input.get("control_joint_method", "Sawcut")
        if cj and cj > 0:
            sub_items.append({
                "description": f"Control joints — {cj_method} ({cj}lm)",
                "price_cents": None,
            })

        # Exposed aggregate
        wash_off = calculator_input.get("wash_off", "N/A")
        if wash_off and wash_off != "N/A":
            desc = "Exposed aggregate finish"
            if calculator_input.get("acid_wash"):
                desc += " with acid wash"
            sub_items.append({
                "description": desc,
                "price_cents": None,
            })

        # Pump
        if calculator_input.get("pump_required"):
            sub_items.append({
                "description": "Concrete pump",
                "price_cents": None,
            })

        # Pour labour hours
        pour_hrs = calculator_result.get("pour_hours", 0)
        finish_hrs = calculator_result.get("finish_hours", 0)
        total_pour_hrs = pour_hrs + finish_hrs
        if total_pour_hrs > 0:
            sub_items.append({
                "description": f"Pour & finish labour ({total_pour_hrs:.1f} hrs)",
                "price_cents": None,
            })

        # Equipment + site clean
        sub_items.append({
            "description": "Equipment & site clean-up",
            "price_cents": None,
        })

        groups.append({
            "id": str(uuid4()),
            "category": "Pour & Finish",
            "sub_items": sub_items,
            "total_cents": pour_total,
            "show_sub_prices": False,
            "sort_order": sort_order,
        })
        sort_order += 1

    # === 5. TRAVEL (only if shown) ===
    travel_cents = calculator_result.get("travel_cents", 0)
    show_travel = calculator_result.get("show_travel", False)

    if show_travel and travel_cents > 0:
        distance_km = calculator_input.get("distance_km", 0)
        groups.append({
            "id": str(uuid4()),
            "category": "Travel",
            "sub_items": [
                {
                    "description": f"Site travel ({distance_km}km from base)",
                    "price_cents": None,
                },
            ],
            "total_cents": travel_cents,
            "show_sub_prices": False,
            "sort_order": sort_order,
        })
        sort_order += 1

    # Distribute overhead + markup proportionally across groups so that
    # sum(group.total_cents) == calculator subtotal_cents.  Without this,
    # customers see line-item totals that don't add up to the quoted price.
    subtotal_cents = calculator_result.get("subtotal_cents", 0)
    raw_sum = sum(g["total_cents"] for g in groups)
    if raw_sum > 0 and subtotal_cents > 0 and raw_sum != subtotal_cents:
        remainder = subtotal_cents
        for i, g in enumerate(groups):
            if i == len(groups) - 1:
                g["total_cents"] = remainder  # last group absorbs rounding
            else:
                g["total_cents"] = round(g["total_cents"] * subtotal_cents / raw_sum)
                remainder -= g["total_cents"]

    return groups


def sum_customer_line_items(customer_line_items: list[dict]) -> tuple[int, int, int]:
    """
    Calculate totals from customer-facing line item groups.

    Returns:
        (subtotal_cents, gst_cents, total_cents)
    """
    subtotal = sum(item.get("total_cents", 0) for item in customer_line_items)
    gst = int(round(subtotal * GST_RATE))
    total = subtotal + gst
    return subtotal, gst, total


def calculate_profit_comparison(
    calculator_result: dict,
    customer_line_items: list[dict],
) -> dict:
    """
    Compute comparison data for the internal sidebar on the preview page.

    Shows how the customer-facing total compares to the calculator's
    internal cost breakdown. This is for the business owner's eyes only.

    Args:
        calculator_result: The stored CalculatorResult dict
        customer_line_items: Current customer-facing groups

    Returns:
        Dict with comparison metrics
    """
    calc_subtotal = calculator_result.get("subtotal_cents", 0)
    raw_cost = calculator_result.get("raw_cost_cents", 0)
    markup = calculator_result.get("markup_cents", 0)
    labour_margin = calculator_result.get("labour_margin_cents", 0)
    calc_profit = calculator_result.get("profit_cents", 0)

    customer_subtotal = sum(
        item.get("total_cents", 0) for item in customer_line_items
    )
    difference = customer_subtotal - calc_subtotal

    # Estimated profit: what the customer pays minus our raw cost
    estimated_profit = customer_subtotal - raw_cost if raw_cost > 0 else 0

    # Profit margin percentage
    profit_margin_pct = (
        round(estimated_profit / customer_subtotal * 100, 1)
        if customer_subtotal > 0 else 0
    )

    return {
        "calculator_subtotal_cents": calc_subtotal,
        "customer_subtotal_cents": customer_subtotal,
        "difference_cents": difference,
        "raw_cost_cents": raw_cost,
        "markup_cents": markup,
        "calculator_profit_cents": calc_profit,
        "estimated_profit_cents": estimated_profit,
        "profit_margin_percent": profit_margin_pct,
        "labour_margin_cents": labour_margin,
    }
