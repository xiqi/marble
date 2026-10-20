import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from analysis import build_facts


ROOT = Path(__file__).resolve().parents[1]


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.sources = build_facts(ROOT)

    def test_sales_period_and_changes(self):
        self.assertEqual(self.facts["snapshot"]["business_now"], "2026-10-05T06:00:00-04:00")
        self.assertEqual(self.facts["snapshot"]["next_saturday"], "2026-10-10")
        weeks = self.facts["sales"]["weeks"]
        self.assertEqual([week["net_sales"] for week in weeks], ["10203.5", "11296.5", "11407"])
        change = self.facts["sales"]["changes"][-1]
        self.assertEqual(change["net_sales"], {"absolute": "110.5", "percent": "0.9782"})
        self.assertEqual(change["menu"]["M05"]["quantity_sold"]["absolute"], "8")
        self.assertIsNone(self.facts["sales"]["changes"][0]["menu"]["M05"]["net_sales"]["percent"])

    def test_purchases_keep_conflicting_totals_and_exclude_future_receipt(self):
        weeks = self.facts["purchasing"]["weeks"]
        self.assertEqual([week["line_purchases"] for week in weeks], ["1431.85", "1650.05", "1436.85"])
        self.assertEqual(weeks[1]["header_purchases"], "1697.55")
        self.assertEqual(weeks[1]["line_purchases_net_sales_percent"], "14.6067")
        self.assertEqual(weeks[2]["line_purchases_net_sales_percent"], "12.5962")
        excluded = self.facts["purchasing"]["excluded_invoices"]
        self.assertEqual([item["invoice_id"] for item in excluded], ["INV-099"])
        self.assertEqual(excluded[0]["items"][0]["quantity"], "40")
        self.assertIn("GM_OPS_LOG.md#N15", excluded[0]["sources"])

    def test_vendor_alias_pack_change_and_direct_pound_invoice(self):
        chicken = "Chicken thigh boneless skinless"
        history = self.facts["purchasing"]["price_history"][chicken]
        self.assertEqual(history["first_to_last_change"], {"absolute": "0.45", "percent": "14.0625"})
        observations = history["observations"]
        direct = next(row for row in observations if "data/invoice_lines.csv#INV-007-L01" in row["sources"])
        self.assertEqual(direct["normalized_quantity"], "80")
        new_pack = next(row for row in observations if "data/invoice_lines.csv#INV-013-L01" in row["sources"])
        self.assertEqual(new_pack["normalized_quantity"], "60")
        self.assertEqual(new_pack["unit_price"], "3.65")
        self.assertEqual({row["vendor_id"] for row in observations}, {"V1"})

    def test_invoice_and_price_anomalies(self):
        anomalies = self.facts["purchasing"]["anomalies"]
        mismatch = next(row for row in anomalies if row["kind"] == "invoice_total_mismatch")
        self.assertEqual(mismatch["invoice_id"], "INV-009")
        self.assertEqual(mismatch["difference"], "47.5")
        price = next(row for row in anomalies if row["kind"] == "price_differs_from_same_sku_median")
        self.assertEqual(price["row_id"], "INV-010-L01")
        self.assertEqual(price["multiple"], "10")
        self.assertEqual(price["difference_from_median_amount"], "378")

    def test_count_reconciliation_does_not_assign_cause(self):
        ingredients = self.facts["consumption"]["ingredients"]
        expected = {
            "Chicken thigh boneless skinless": "5", "Mixed bell peppers, trimmed": "3",
            "6 inch flour tortilla": "48", "Shredded cheddar": "2.4",
            "Long grain rice, dry": "0", "Black beans, drained": "0",
            "Large shell eggs": "0", "Canola oil": "0", "Whole avocado": "0",
        }
        self.assertEqual({name: row["unexplained_difference"] for name, row in ingredients.items()}, expected)
        self.assertEqual(ingredients["Chicken thigh boneless skinless"]["period_receipts_without_known_dispute"], "540")
        missing = self.facts["consumption"]["missing_recipes"]["M99"]
        self.assertEqual((missing["quantity_sold"], missing["net_sales"]), ("32", "320"))
        self.assertTrue(any("do not establish a causal link" in text for text in self.facts["consumption"]["limitations"]))

    def test_restocking_uses_closing_count_once_and_labels_scenarios(self):
        scenarios = {row["id"]: row for row in self.facts["restocking"]["scenarios"]}
        week = scenarios["full_week"]["ingredients"]
        tortilla = week["6 inch flour tortilla"]
        self.assertEqual(tortilla["known_recipe_demand"], "1235")
        self.assertEqual(tortilla["closing_count_quantity"], "280")
        self.assertEqual(tortilla["shortfall_against_closing_count"], "955")
        self.assertEqual(tortilla["minimum_whole_packs_for_known_gap"], "4")
        self.assertEqual(week["Black beans, drained"]["minimum_whole_packs_for_known_gap"], "5")
        self.assertEqual(week["Long grain rice, dry"]["minimum_whole_packs_for_known_gap"], "1")
        eggs = scenarios["weekend"]["ingredients"]["Large shell eggs"]
        self.assertEqual(eggs["known_recipe_demand"], "272")
        self.assertEqual(eggs["closing_count_quantity"], "84")
        self.assertEqual(eggs["shortfall_against_closing_count"], "188")
        self.assertEqual(eggs["minimum_whole_packs_for_known_gap"], "2")
        self.assertTrue(any("intervening weekday consumption" in text for text in self.facts["restocking"]["limitations"]))

    def test_sources_complete_and_all_references_resolve(self):
        json.dumps(self.facts, allow_nan=False)
        for filename in (ROOT / "data").glob("*.csv"):
            with filename.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            count = sum(source["file"] == f"data/{filename.name}" for source in self.sources.values())
            self.assertEqual(count, len(rows), filename.name)

        def check(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "sources":
                        for reference in item:
                            self.assertIn(reference, self.sources)
                    elif key == "evidence_sets" and isinstance(item, list):
                        for group in item:
                            self.assertIn(group, self.facts["evidence_sets"])
                    else:
                        check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(self.facts)
        for references in self.facts["evidence_sets"].values():
            for reference in references:
                self.assertIn(reference, self.sources)
        correction = self.facts["operational_context"]["breakfast_correction"]
        self.assertEqual(correction["applies_on"], "2026-10-03")
        self.assertIn("GM_OPS_LOG.md#N14", correction["sources"])
        self.assertIn("data/pos_daily.csv#POS-20-M05", correction["sources"])

    def mutated_facts(self, table, mutate):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "data", root / "data")
            for filename in ("SITE_PROFILE.md", "GM_OPS_LOG.md", "README.md", "ABOUT_MARBLE.md", "CANDIDATE_BRIEF.md"):
                shutil.copy(ROOT / filename, root / filename)
            path = root / "data" / f"{table}.csv"
            with path.open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields = reader.fieldnames
                rows = list(reader)
            mutate(rows)
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            return build_facts(root)

    def test_changes_are_computed_from_inputs(self):
        def mutate(rows):
            row = next(row for row in rows if row["row_id"] == "POS-20-M05")
            row.update(quantity_sold="67", gross_sales="603.00", net_sales="603.00")

        facts, _ = self.mutated_facts("pos_daily", mutate)
        self.assertEqual(facts["sales"]["weeks"][2]["net_sales"], "11416")
        self.assertEqual(facts["consumption"]["ingredients"]["Large shell eggs"]["unexplained_difference"], "-2")
        weekend = facts["restocking"]["scenarios"][1]
        self.assertEqual(weekend["ingredients"]["Large shell eggs"]["known_recipe_demand"], "274")

    def test_recipe_effective_date_is_used_for_each_sale(self):
        def mutate(rows):
            row = next(row for row in rows if row["row_id"] == "REC-M05-EGG")
            rows.append({**row, "row_id": "REC-M05-EGG-V2", "quantity": "3", "effective_from": "2026-10-03"})

        facts, _ = self.mutated_facts("recipes", mutate)
        eggs = facts["consumption"]["ingredients"]["Large shell eggs"]
        self.assertEqual(eggs["known_recipe_usage"], "664")
        self.assertEqual(facts["restocking"]["scenarios"][1]["ingredients"]["Large shell eggs"]["known_recipe_demand"], "408")

    def test_invoice_date_does_not_choose_purchase_week(self):
        def mutate(rows):
            next(row for row in rows if row["invoice_id"] == "INV-013")["invoice_date"] = "2026-09-27"

        facts, _ = self.mutated_facts("invoice_headers", mutate)
        self.assertEqual(facts["purchasing"]["weeks"][2]["line_purchases"], "1436.85")

    def test_disputed_receipt_stays_excluded_if_date_is_not_future(self):
        def mutate(rows):
            next(row for row in rows if row["invoice_id"] == "INV-099")["delivery_date"] = "2026-10-04"

        facts, _ = self.mutated_facts("invoice_headers", mutate)
        self.assertEqual(facts["consumption"]["ingredients"]["Chicken thigh boneless skinless"]["period_receipts_without_known_dispute"], "540")

    def test_corrected_pepper_price_recomputes_the_anomaly(self):
        def mutate(rows):
            row = next(row for row in rows if row["row_id"] == "INV-010-L01")
            row.update(unit_price="21.00", line_total="42.00")

        facts, _ = self.mutated_facts("invoice_lines", mutate)
        self.assertFalse(any(row["kind"] == "price_differs_from_same_sku_median" for row in facts["purchasing"]["anomalies"]))
        self.assertEqual(facts["purchasing"]["weeks"][1]["line_purchases"], "1272.05")
        self.assertTrue(any(row["invoice_id"] == "INV-010" and row["kind"] == "invoice_total_mismatch"
                            for row in facts["purchasing"]["anomalies"]))

    def test_duplicate_nonfinite_and_missing_reference_fail_at_boundary(self):
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            self.mutated_facts("pos_daily", lambda rows: rows.append(rows[0].copy()))
        with self.assertRaisesRegex(ValueError, "invalid quantity_sold"):
            self.mutated_facts("pos_daily", lambda rows: rows[0].update(quantity_sold="NaN"))
        with self.assertRaisesRegex(ValueError, "unknown invoice"):
            self.mutated_facts("invoice_lines", lambda rows: rows[0].update(invoice_id="MISSING"))


if __name__ == "__main__":
    unittest.main()
