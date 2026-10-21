import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from analysis import build_facts
from presentation import render_analysis, render_sources


ROOT = Path(__file__).resolve().parents[1]


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.sources = build_facts(ROOT)
        cls.facts["delivery"] = [record["content"] for record in cls.sources.values() if record["file"] == "data/vendors.csv"]

    def test_source_tables_keep_every_original_field_and_note(self):
        original = copy.deepcopy(self.sources)
        with patch("presentation.st") as ui:
            render_sources(list(self.sources), self.sources)
        rows = [row for call in ui.dataframe.call_args_list for row in call.args[0]]
        expected = [
            record["content"]
            for record in self.sources.values() if isinstance(record["content"], dict)
        ]
        self.assertEqual(rows, expected)
        self.assertEqual(ui.dataframe.call_count, 8)
        files = list(dict.fromkeys(record["file"] for record in self.sources.values() if isinstance(record["content"], dict)))
        for filename, call in zip(files, ui.dataframe.call_args_list):
            displayed_columns = call.kwargs["column_order"]
            self.assertEqual(set(displayed_columns), set(call.args[0][0]))
            self.assertNotEqual(displayed_columns[0], "row_id")
            if filename == "data/inventory_counts.csv":
                self.assertEqual(displayed_columns[:4], ["ingredient_name", "quantity", "unit", "counted_at"])
                self.assertEqual(displayed_columns[-1], "row_id")
            if filename == "data/invoice_lines.csv":
                self.assertEqual(displayed_columns[:5], ["item_description", "quantity", "unit", "unit_price", "line_total"])
                self.assertEqual(displayed_columns[-2:], ["invoice_id", "row_id"])
            if filename == "data/pos_daily.csv":
                self.assertEqual(displayed_columns[:4], ["item_name", "business_date", "quantity_sold", "net_sales"])
                self.assertEqual(displayed_columns[-1], "row_id")
        captions = [call.args[0] for call in ui.caption.call_args_list]
        markdown = [call.args[0] for call in ui.markdown.call_args_list]
        for record in self.sources.values():
            self.assertTrue(any(record["file"] in caption for caption in captions))
            if isinstance(record["content"], str):
                self.assertIn(f"{record['file']} · {record['id']}", captions)
                self.assertIn(record["content"].replace("$", r"\$"), markdown)
        ui.json.assert_not_called()
        ui.selectbox.assert_not_called()
        self.assertEqual(self.sources, original)

    def test_source_selection_deduplicates_and_keeps_saved_message_metadata(self):
        update = {
            "file": "source_updates", "id": "UPD-001", "content": "DEMO: Monday and Thursday.",
            "learned_at": "2026-10-05T05:00:00-04:00",
        }
        sources = {**self.sources, "source_updates#UPD-001": update}
        ref = "data/invoice_lines.csv#INV-099-L01"
        with patch("presentation.st") as ui:
            render_sources([ref, ref, "source_updates#UPD-001"], sources)
        self.assertEqual(ui.dataframe.call_args.args[0], [sources[ref]["content"]])
        ui.markdown.assert_any_call(update["content"])
        self.assertTrue(any(update["learned_at"] in call.args[0] for call in ui.caption.call_args_list))

    def test_analysis_views_render_real_values_and_limits_without_json(self):
        app = AppTest.from_string(
            "from pathlib import Path\n"
            "from analysis import build_facts\n"
            "from presentation import render_analysis\n"
            f"facts, sources = build_facts(Path({str(ROOT)!r}))\n"
            "facts['delivery'] = [record['content'] for record in sources.values() if record['file'] == 'data/vendors.csv']\n"
            "render_analysis(facts)\n",
            default_timeout=10,
        ).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[0].value, "$11,407.00")
        rows = [row for table in app.dataframe for row in table.value.to_dict("records")]
        self.assertTrue(any(row.get("Net sales (USD)") == "11407" for row in rows))
        self.assertTrue(any(row.get("Menu ID") == "M05" and row.get("Portions change") == "8" for row in rows))
        self.assertEqual(len(app.json), 0)

        app.selectbox(key="analysis_section").set_value("Purchasing").run()
        self.assertEqual(len(app.exception), 0)
        rows = [row for table in app.dataframe for row in table.value.to_dict("records")]
        self.assertTrue(any(row.get("Invoice") == "INV-009" and row.get("Difference (USD)") == "47.5" for row in rows))
        self.assertTrue(any(row.get("Invoice") == "INV-010" and row.get("Difference (USD)") == "378" for row in rows))
        self.assertTrue(any(row.get("Supplier") == "Alder Wholesale" and row.get("Purchases (USD)") == "844.2" for row in rows))
        excluded = next(row for row in rows if row.get("Invoice") == "INV-099")
        self.assertEqual(excluded["Invoice date"], "2026-10-04")
        self.assertEqual(excluded["Delivery date"], "2026-10-06")
        self.assertIn("physical receipt", excluded["Why excluded"])
        self.assertTrue(any("Not COGS" in caption.value for caption in app.caption))
        self.assertEqual(len(app.json), 0)

        app.selectbox(key="analysis_section").set_value("Stock reconciliation").run()
        self.assertEqual(len(app.exception), 0)
        rows = app.dataframe[0].value.to_dict("records")
        chicken = next(row for row in rows if row["Ingredient"] == "Chicken thigh boneless skinless")
        self.assertEqual(chicken["Included receipts"], "540")
        self.assertEqual(chicken["Known recipe usage"], "519.1")
        self.assertEqual(chicken["Unexplained difference"], "5")
        self.assertIn("not measured waste", app.info[0].value)
        missing = app.dataframe[1].value.to_dict("records")[0]
        self.assertEqual((missing["Menu ID"], missing["Portions"], missing["Net sales (USD)"]), ("M99", "32", "320"))
        self.assertEqual(len(app.json), 0)

        app.selectbox(key="analysis_section").set_value("Restocking").run()
        self.assertEqual(len(app.exception), 0)
        rows = app.dataframe[0].value.to_dict("records")
        rice = next(row for row in rows if row["Ingredient"] == "Long grain rice, dry")
        self.assertEqual(rice["Shortfall"], "23.54")
        app.radio(key="restocking_scenario").set_value(1).run()
        self.assertEqual(len(app.exception), 0)
        eggs = next(row for row in app.dataframe[0].value.to_dict("records") if row["Ingredient"] == "Large shell eggs")
        self.assertEqual((eggs["Known demand"], eggs["Closing count"], eggs["Shortfall"], eggs["Minimum whole packs"]),
                         ("272", "84", "188", "2"))
        self.assertEqual(eggs["Pack size"], "180 each / case")
        self.assertEqual(eggs["Supplier"], "Alder Wholesale")
        self.assertEqual(eggs["Vendor ID"], "V1")
        self.assertTrue(any("intervening weekday consumption" in caption.value for caption in app.caption))
        self.assertTrue(any("INV-099" in warning.value for warning in app.warning))
        self.assertEqual(len(app.json), 0)

        app.selectbox(key="analysis_section").set_value("Context").run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("not an open-ended cancellation" in caption.value for caption in app.caption))
        self.assertTrue(any("have not seen the cases" in markdown.value for markdown in app.markdown))
        app.selectbox(key="notebook_entry").set_value(13).run()
        self.assertTrue(any("Breakfast tacos ON today" in markdown.value for markdown in app.markdown))
        self.assertTrue(any("GM_OPS_LOG.md#N14" in caption.value for caption in app.caption))
        self.assertEqual(len(app.json), 0)

    def test_analysis_displays_supplied_results_without_recalculating_or_mutating(self):
        facts = copy.deepcopy(self.facts)
        facts["consumption"]["ingredients"]["Chicken thigh boneless skinless"]["unexplained_difference"] = "12.345"
        before = copy.deepcopy(facts)
        with patch("presentation.st") as ui:
            ui.selectbox.return_value = "Stock reconciliation"
            render_analysis(facts)
        row = next(row for row in ui.dataframe.call_args_list[0].args[0]
                   if row["Ingredient"] == "Chicken thigh boneless skinless")
        self.assertEqual(row["Unexplained difference"], "12.345")
        self.assertEqual(facts, before)


if __name__ == "__main__":
    unittest.main()
