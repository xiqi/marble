import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory import BUSINESS_NOW, delivery_context, initialize, save_rule


ZONE = ZoneInfo("America/New_York")


class DeliveryMemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "memory.sqlite"
        initialize(self.db)

    def update(self, **changes):
        values = {
            "vendor_id": "V1",
            "weekdays": [0, 3],
            "lead_days": 1,
            "cutoff_time": "12:00",
            "effective_from": "2026-10-05",
            "effective_to": None,
            "source_text": "DEMO: Alder confirmed Monday/Thursday, noon the previous day.",
            "kind": "correction",
        }
        values.update(changes)
        return save_rule(self.db, **values)

    def alder(self, now=BUSINESS_NOW):
        return next(vendor for vendor in delivery_context(self.db, now)["vendors"] if vendor["vendor_id"] == "V1")

    def test_initial_rules_and_unknown_cutoffs(self):
        context = delivery_context(self.db)
        alder = self.alder()
        self.assertEqual(alder["next_delivery"], "2026-10-07")
        self.assertEqual(alder["deadline"], "2026-10-06T12:00:00-04:00")
        self.assertTrue(alder["today_is_delivery"])
        self.assertEqual([rule["source_ref"] for rule in alder["rules"]], ["GM_OPS_LOG.md#N09"])
        for vendor in context["vendors"]:
            if vendor["vendor_id"] != "V1":
                self.assertFalse(vendor["cutoff_known"])
                self.assertIsNone(vendor["next_delivery"])
                self.assertIsNone(vendor["deadline"])
                self.assertEqual(vendor["upcoming_rules"], [])

    def test_correction_survives_reinitialization_and_new_connections(self):
        update_id = self.update()
        initialize(self.db)
        alder = self.alder()
        self.assertEqual(update_id, "UPD-001")
        self.assertEqual(alder["next_delivery"], "2026-10-08")
        self.assertEqual(alder["deadline"], "2026-10-07T12:00:00-04:00")
        self.assertEqual(alder["next_source_ref"], "source_updates#UPD-001")
        context = delivery_context(self.db)
        self.assertIn("DEMO", context["sources"]["source_updates#UPD-001"]["content"])
        self.assertEqual(len(context["history"]), 5)

    def test_conflict_keeps_sources_until_explicit_correction(self):
        self.update()
        self.update(weekdays=[0, 2], kind="conflict", source_text="DEMO: an unconfirmed text still says Wednesday.")
        alder = self.alder()
        self.assertTrue(alder["conflicted"])
        self.assertEqual(len(alder["rules"]), 2)
        self.assertIsNone(alder["next_delivery"])
        self.assertIsNone(alder["deadline"])
        self.assertEqual(
            {rule["next_delivery"] for rule in alder["rules"]},
            {"2026-10-07", "2026-10-08"},
        )
        final_id = self.update(weekdays=[0, 4], source_text="DEMO: rep explicitly confirms Friday and corrects both messages.")
        alder = self.alder()
        self.assertFalse(alder["conflicted"])
        self.assertEqual(alder["next_delivery"], "2026-10-09")
        self.assertEqual([rule["source_ref"] for rule in alder["rules"]], [f"source_updates#{final_id}"])
        self.assertEqual(len(delivery_context(self.db)["sources"]), 3)

    def test_temporary_exception_does_not_revive_permanently_replaced_base(self):
        self.update(weekdays=[1], effective_to="2026-10-06")
        self.assertEqual(self.alder()["next_delivery"], "2026-10-06")
        after = datetime(2026, 10, 7, 6, tzinfo=ZONE)
        alder = self.alder(after)
        self.assertEqual([rule["id"] for rule in alder["rules"]], ["N09-V1"])
        self.assertEqual(alder["next_delivery"], "2026-10-12")
        self.assertNotEqual(alder["next_delivery"], "2026-10-08")

    def test_future_effective_rule_changes_future_delivery_not_current_rule(self):
        self.update(effective_from="2026-10-06")
        alder = self.alder()
        self.assertEqual([rule["id"] for rule in alder["rules"]], ["N09-V1"])
        self.assertEqual(alder["next_delivery"], "2026-10-08")
        self.assertEqual(alder["next_source_ref"], "source_updates#UPD-001")

    def test_known_future_conflict_blocks_a_single_upcoming_delivery(self):
        self.update(effective_from="2026-10-06", kind="conflict")
        alder = self.alder()
        self.assertFalse(alder["conflicted"])
        self.assertTrue(alder["next_conflicted"])
        self.assertIsNone(alder["next_delivery"])
        self.assertIsNone(alder["deadline"])
        self.assertEqual(
            {rule["source_ref"] for rule in alder["upcoming_rules"]},
            {"GM_OPS_LOG.md#N09", "source_updates#UPD-001"},
        )
        self.assertEqual(
            {rule["next_delivery"] for rule in alder["upcoming_rules"]},
            {"2026-10-07", "2026-10-08"},
        )

    def test_future_conflict_is_visible_when_supplier_cutoff_is_unknown(self):
        self.update(
            vendor_id="V2", weekdays=[1], lead_days=None, cutoff_time=None,
            effective_from="2026-10-06", kind="conflict",
        )
        vendor = next(
            item for item in delivery_context(self.db)["vendors"] if item["vendor_id"] == "V2"
        )
        self.assertFalse(vendor["conflicted"])
        self.assertTrue(vendor["next_conflicted"])
        self.assertFalse(vendor["cutoff_known"])
        self.assertIsNone(vendor["next_delivery"])
        self.assertIsNone(vendor["deadline"])
        self.assertEqual(
            {rule["source_ref"] for rule in vendor["upcoming_rules"]},
            {"data/vendors.csv#V2", "source_updates#UPD-001"},
        )

    def test_unknown_cutoff_conflict_before_known_delivery_is_not_skipped(self):
        self.update(
            weekdays=[1], lead_days=None, cutoff_time=None, kind="conflict",
            effective_from="2026-10-06", effective_to="2026-10-06",
        )
        alder = self.alder()
        self.assertFalse(alder["conflicted"])
        self.assertTrue(alder["next_conflicted"])
        self.assertIsNone(alder["next_delivery"])
        self.assertIsNone(alder["deadline"])
        self.assertEqual(
            {rule["source_ref"] for rule in alder["upcoming_rules"]},
            {"GM_OPS_LOG.md#N09", "source_updates#UPD-001"},
        )

    def test_expiring_exception_restores_latest_permanent_rule(self):
        permanent_id = self.update()
        self.update(weekdays=[1], effective_to="2026-10-06")
        after = datetime(2026, 10, 7, 6, tzinfo=ZONE)
        alder = self.alder(after)
        self.assertEqual([rule["source_ref"] for rule in alder["rules"]], [f"source_updates#{permanent_id}"])
        self.assertEqual(alder["next_delivery"], "2026-10-08")

    def test_same_day_cutoff_and_partly_unknown_cutoff(self):
        self.update(lead_days=0)
        self.assertEqual(self.alder()["next_delivery"], "2026-10-05")
        self.assertEqual(self.alder()["deadline"], "2026-10-05T12:00:00-04:00")
        self.update(lead_days=None)
        self.assertFalse(self.alder()["cutoff_known"])
        self.assertIsNone(self.alder()["next_delivery"])

    def test_future_learned_message_is_absent_from_all_context(self):
        self.update(learned_at="2026-10-06T08:00:00-04:00")
        context = delivery_context(self.db)
        self.assertEqual(self.alder()["next_delivery"], "2026-10-07")
        self.assertEqual(context["sources"], {})
        self.assertEqual(len(context["history"]), 4)
        after = datetime(2026, 10, 6, 9, tzinfo=ZONE)
        self.assertEqual(self.alder(after)["next_delivery"], "2026-10-08")

    def test_original_change_is_not_applied_before_effective_date(self):
        before = datetime(2026, 9, 27, 6, tzinfo=ZONE)
        self.assertEqual([rule["id"] for rule in self.alder(before)["rules"]], ["BASE-V1"])
        after = datetime(2026, 9, 28, 6, tzinfo=ZONE)
        self.assertEqual([rule["id"] for rule in self.alder(after)["rules"]], ["N09-V1"])

    def test_cutoff_at_current_time_is_already_closed(self):
        now = datetime(2026, 10, 6, 12, tzinfo=ZONE)
        self.assertEqual(self.alder(now)["next_delivery"], "2026-10-12")

    def test_invalid_input_leaves_no_source_or_rule(self):
        invalid = [
            {"vendor_id": "UNKNOWN"},
            {"weekdays": []},
            {"weekdays": [True]},
            {"weekdays": [0, 0]},
            {"weekdays": [7]},
            {"lead_days": -1},
            {"cutoff_time": "25:00"},
            {"cutoff_time": "9:00"},
            {"effective_from": "2026-02-30"},
            {"effective_to": "2026-10-04"},
            {"source_text": " "},
            {"kind": "latest"},
            {"learned_at": "2026-10-05T06:00:00"},
            {"learned_at": None},
        ]
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.update(**values)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_updates").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM delivery_rules").fetchone()[0], 4)

    def test_failed_rule_insert_rolls_back_its_source(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("""
                CREATE TRIGGER reject_test_rule BEFORE INSERT ON delivery_rules
                WHEN NEW.id LIKE 'RULE-%'
                BEGIN SELECT RAISE(ABORT, 'test failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.update()
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_updates").fetchone()[0], 0)

    def test_concurrent_initialization_does_not_duplicate_or_reset_rules(self):
        self.update()
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: initialize(self.db), range(8)))
        self.assertEqual(len(delivery_context(self.db)["history"]), 5)
        self.assertEqual(self.alder()["next_delivery"], "2026-10-08")

    def test_concurrent_updates_have_unique_source_ids(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(
                lambda index: self.update(kind="conflict", source_text=f"DEMO message {index}"),
                range(4),
            ))
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(len(delivery_context(self.db)["sources"]), 4)


if __name__ == "__main__":
    unittest.main()
