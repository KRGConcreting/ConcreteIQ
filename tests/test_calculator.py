"""
Calculator Tests — Verify calculations match expected values.

Run with: pytest tests/test_calculator.py -v
"""

import pytest
from app.quotes.calculator import calculate, CalculatorInput


class TestBasicCalculations:
    """Test basic calculation scenarios."""

    def test_simple_25sqm_slab(self):
        """Test a simple 25m² slab — should match KRG typical quote."""
        inp = CalculatorInput(
            slab_area=25,
            slab_thickness=100,
            edge_formwork=20,
            control_joints=10,
            concrete_grade="N25",
            reinforcement="GFRP 450mm",
            control_joint_method="Sawcut",
            inc_moisture_barrier=True,
            inc_evap_retarder=True,
            tier="Standard",
            complexity="Standard",
            team_tier="Standard",
        )

        result = calculate(inp)

        # Basic sanity checks
        assert result.volume_m3 > 0
        assert result.total_cents > 0
        assert result.gst_cents > 0
        assert result.subtotal_cents > 0

        # Should be more than minimum quote
        assert result.total_cents >= 165000  # $1650

        # Should have payments
        assert len(result.payments) == 3

        # Payments should sum to total
        payment_sum = sum(p.amount_cents for p in result.payments)
        assert payment_sum == result.total_cents

        # Volume should be correct (25m² × 0.1m × 1.08 buffer ≈ 2.7m³)
        assert 2.5 <= result.volume_m3 <= 3.0

        # Rate per sqm should be reasonable ($100-300/m²)
        assert 10000 <= result.rate_per_sqm_cents <= 30000

    def test_zero_area_returns_empty(self):
        """Zero area should return empty result."""
        inp = CalculatorInput(slab_area=0)
        result = calculate(inp)

        assert result.total_cents == 0
        assert result.volume_m3 == 0

    def test_minimum_quote_applied(self):
        """Very small jobs should hit minimum quote."""
        inp = CalculatorInput(
            slab_area=2,  # Very small
            slab_thickness=100,
            concrete_grade="N25",
            reinforcement="None",
            tier="Standard",
            complexity="Easy",
            team_tier="Lean",
        )

        result = calculate(inp)

        # Should hit minimum
        assert result.minimum_applied == True
        assert result.total_cents == 275000  # $2750 minimum


class TestPaymentSchedule:
    """Test payment schedule generation."""

    def test_always_30_60_10_split(self):
        """All jobs should use 30/60/10 payment split."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_grade="N25",
            reinforcement="GFRP 450mm",
        )

        result = calculate(inp)

        assert len(result.payments) == 3
        assert result.payments[0].percent == 0.30
        assert result.payments[1].percent == 0.60
        assert result.payments[2].percent == 0.10
        assert "30%" in result.payments[0].name
        assert "60%" in result.payments[1].name
        assert "10%" in result.payments[2].name

    def test_large_job_still_30_60_10(self):
        """Jobs >= $20k should ALSO use 30/60/10 (not 10/80/10)."""
        inp = CalculatorInput(
            slab_area=150,
            slab_thickness=100,
            edge_formwork=50,
            control_joints=50,
            concrete_grade="N32",
            reinforcement="GFRP 450mm",
            control_joint_method="Sawcut",
            pump_required=True,
            tier="Standard",
            complexity="Standard",
            team_tier="Standard",
        )

        result = calculate(inp)

        # Regardless of total, should be 30/60/10
        assert result.payments[0].percent == 0.30
        assert result.payments[1].percent == 0.60
        assert result.payments[2].percent == 0.10

    def test_payments_sum_to_total(self):
        """Payment amounts should sum to total."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_grade="N25",
            reinforcement="GFRP 450mm",
        )

        result = calculate(inp)

        payment_sum = sum(p.amount_cents for p in result.payments)
        assert payment_sum == result.total_cents

    def test_three_payments(self):
        """Should always have 3 payments."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
        )

        result = calculate(inp)
        assert len(result.payments) == 3


class TestConcreteCalculations:
    """Test concrete volume and cost calculations."""

    def test_volume_includes_buffer(self):
        """Volume should include 8% wastage buffer."""
        inp = CalculatorInput(
            slab_area=100,
            slab_thickness=100,  # 0.1m
        )

        result = calculate(inp)

        # 100m² × 0.1m = 10m³ base
        # With 8% buffer = 10.8m³
        # Rounded up to 0.1 = 10.8m³
        assert result.volume_m3 == 10.8

    def test_short_load_fee(self):
        """Orders under 3m³ should have short load fee."""
        inp = CalculatorInput(
            slab_area=10,  # Will give < 3m³
            slab_thickness=100,
            concrete_grade="N25",
        )

        result = calculate(inp)

        # 10m² × 0.1m × 1.08 ≈ 1.08m³
        assert result.volume_m3 < 3.0
        assert result.short_load_fee_cents > 0

    def test_concrete_grades(self):
        """Different grades should have different prices."""
        results = {}

        for grade in ["N20", "N25", "N32"]:
            inp = CalculatorInput(
                slab_area=50,
                slab_thickness=100,
                concrete_grade=grade,
            )
            results[grade] = calculate(inp)

        # N32 should be more expensive than N25
        assert results["N32"].concrete_cost_cents > results["N25"].concrete_cost_cents
        # N25 should be more expensive than N20
        assert results["N25"].concrete_cost_cents > results["N20"].concrete_cost_cents


class TestReinforcementCalculations:
    """Test reinforcement calculations."""

    def test_gfrp_costs_more_than_none(self):
        """GFRP reinforcement should add cost."""
        inp_none = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            reinforcement="None",
        )

        inp_gfrp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            reinforcement="GFRP 450mm",
        )

        result_none = calculate(inp_none)
        result_gfrp = calculate(inp_gfrp)

        assert result_gfrp.setup_materials_cents > result_none.setup_materials_cents

    def test_dowel_count(self):
        """Dowels should be calculated from lineal metres."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            dowel_bars=10,  # 10 lineal metres
            reinforcement="GFRP 450mm",  # 450mm spacing
        )

        result = calculate(inp)

        # 10lm ÷ 0.45m = 22.2 dowels, rounded to 22
        assert result.dowel_spacing == 450
        assert result.dowel_count == 22


class TestPourHoursCalculation:
    """Test pour hours calculation — flat 11 min/m² rate."""

    def test_flat_rate_calculation(self):
        """Pour hours should use 11 min/m² flat rate."""
        inp = CalculatorInput(
            slab_area=100,
            slab_thickness=100,
        )

        result = calculate(inp)

        # 100m² × 11 min/m² = 1100 min = 18.33 hrs
        # Minimum 6 hrs, so should be 18.33
        assert 18 <= result.pour_hours <= 19

    def test_season_does_not_affect_pour_hours(self):
        """Season is display-only — should NOT affect pour hours."""
        inp_summer = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            season="Summer",
        )
        inp_winter = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            season="Winter",
        )

        result_summer = calculate(inp_summer)
        result_winter = calculate(inp_winter)

        assert result_summer.pour_hours == result_winter.pour_hours

    def test_small_job_minimum_hours(self):
        """Small jobs should hit minimum 6 hours pour time."""
        inp = CalculatorInput(
            slab_area=10,
            slab_thickness=100,
        )

        result = calculate(inp)

        # 10m² × 11 min/m² = 110 min = 1.83 hrs < 6 minimum
        assert result.pour_hours == 6.0

    def test_falls_complexity_adds_pour_time(self):
        """Falls complexity should increase pour hours."""
        inp_flat = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            fall_type="none",
        )
        inp_complex = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            fall_type="pits",
            fall_pit_count=3,
        )

        result_flat = calculate(inp_flat)
        result_complex = calculate(inp_complex)

        assert result_complex.pour_hours > result_flat.pour_hours

    def test_complexity_does_not_affect_pour_hours(self):
        """Site complexity should NOT affect pour hours (only labour cost)."""
        base_input = dict(
            slab_area=50,
            slab_thickness=100,
        )

        inp_easy = CalculatorInput(**base_input, complexity="Easy")
        inp_complex = CalculatorInput(**base_input, complexity="Very Complex")

        result_easy = calculate(inp_easy)
        result_complex = calculate(inp_complex)

        # Pour hours should be the same regardless of complexity
        assert result_easy.pour_hours == result_complex.pour_hours


class TestComplexityAndTiers:
    """Test complexity and pricing tiers."""

    def test_complexity_affects_price(self):
        """Higher complexity should increase price."""
        base_input = dict(
            slab_area=50,
            slab_thickness=100,
            edge_formwork=20,
            concrete_grade="N25",
            reinforcement="GFRP 450mm",
            tier="Standard",
            team_tier="Standard",
        )

        inp_easy = CalculatorInput(**base_input, complexity="Easy")
        inp_standard = CalculatorInput(**base_input, complexity="Standard")
        inp_complex = CalculatorInput(**base_input, complexity="Complex")

        result_easy = calculate(inp_easy)
        result_standard = calculate(inp_standard)
        result_complex = calculate(inp_complex)

        assert result_easy.total_cents < result_standard.total_cents
        assert result_standard.total_cents < result_complex.total_cents

    def test_team_tier_affects_labour(self):
        """Different team tiers should affect labour cost."""
        base_input = dict(
            slab_area=50,
            slab_thickness=100,
            concrete_grade="N25",
            tier="Standard",
            complexity="Standard",
        )

        inp_lean = CalculatorInput(**base_input, team_tier="Lean")
        inp_standard = CalculatorInput(**base_input, team_tier="Standard")
        inp_premium = CalculatorInput(**base_input, team_tier="Premium")

        result_lean = calculate(inp_lean)
        result_standard = calculate(inp_standard)
        result_premium = calculate(inp_premium)

        assert result_lean.labour_sell_cents < result_standard.labour_sell_cents
        assert result_standard.labour_sell_cents < result_premium.labour_sell_cents


class TestDiscount:
    """Test customer discount feature."""

    def test_no_discount_by_default(self):
        """Default discount should be zero."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
        )
        result = calculate(inp)
        assert result.discount_cents == 0

    def test_discount_reduces_total(self):
        """Discount should reduce total price."""
        inp_no_disc = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_grade="N25",
        )
        inp_with_disc = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_grade="N25",
            customer_discount_percent=10,
        )

        result_no = calculate(inp_no_disc)
        result_disc = calculate(inp_with_disc)

        assert result_disc.total_cents < result_no.total_cents
        assert result_disc.discount_cents > 0
        # Subtotal should be the same (pre-discount)
        assert result_disc.subtotal_cents == result_no.subtotal_cents

    def test_discount_applied_before_gst(self):
        """GST should be calculated on discounted amount."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_grade="N25",
            customer_discount_percent=10,
        )
        result = calculate(inp)

        net = result.subtotal_cents - result.discount_cents
        expected_gst = int(round(net * 0.10))
        assert result.gst_cents == expected_gst
        assert result.total_cents == net + result.gst_cents


class TestLineItems:
    """Test line item generation."""

    def test_line_items_generated(self):
        """Should generate line items for PDF."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            edge_formwork=20,
            concrete_grade="N25",
            reinforcement="GFRP 450mm",
            pump_required=True,
            distance_km=30,  # Will trigger travel
        )

        result = calculate(inp)

        assert len(result.line_items) > 0

        # Should have concrete
        concrete_items = [li for li in result.line_items if "concrete" in li.description.lower()]
        assert len(concrete_items) > 0

        # Should have labour
        labour_items = [li for li in result.line_items if li.category == "labour"]
        assert len(labour_items) > 0

        # Should have pump
        pump_items = [li for li in result.line_items if "pump" in li.description.lower()]
        assert len(pump_items) > 0


class TestConcreteRemoval:
    """Test concrete removal calculations."""

    def test_removal_adds_cost(self):
        """Concrete removal should add cost."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_removal=True,
            removal_area=30,
        )

        result = calculate(inp)

        assert result.removal_cost_cents > 0
        assert result.show_removal == True

    def test_removal_skip_bin_disposal(self):
        """Skip bin disposal should work."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_removal=True,
            removal_area=10,
            removal_disposal="skip_bin",
        )

        result = calculate(inp)
        assert result.removal_disposal_cents > 0

    def test_removal_trailer_disposal(self):
        """Trailer disposal should work."""
        inp = CalculatorInput(
            slab_area=50,
            slab_thickness=100,
            concrete_removal=True,
            removal_area=10,
            removal_disposal="trailer",
        )

        result = calculate(inp)
        assert result.removal_disposal_cents > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
