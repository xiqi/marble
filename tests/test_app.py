import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, time
from pathlib import Path
from unittest.mock import patch

from openai import OpenAIError
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


class AppTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "memory.sqlite"
        environment = patch.dict(os.environ, {
            "MARBLE_DB_PATH": str(self.db_path),
        })
        environment.start()
        self.addCleanup(environment.stop)
        model = patch("assistant.generate", side_effect=self.report)
        self.generate = model.start()
        self.addCleanup(model.stop)
        self.app = AppTest.from_file(ROOT / "app.py", default_timeout=10)

    def report(self, facts, sources, question=None, history=None):
        alder = next(vendor for vendor in facts["delivery"] if vendor["vendor_id"] == "V1")
        source = alder["next_source_ref"] or alder["rules"][0]["source_ref"]
        return {
            "report": {
                "title": "Morning brief" if question is None else "Answer",
                "items": [
                    {
                        "title": f"Delivery check {index + 1}",
                        "evidence": [{
                            "text": f"Recorded next order window: {alder['next_delivery']}.",
                            "sources": [source],
                        }],
                        "interpretation": "Confirm the recorded schedule before ordering.",
                        "next_step": "Check existing orders with the supplier.",
                        "unknowns": ["Existing orders are not confirmed."],
                    }
                    for index in range(3 if question is None else 1)
                ],
            },
            "model": "mock-model",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "estimated_cost_usd": 0.0003,
        }

    def test_first_brief_is_generated_once_and_widget_reruns_do_not_repeat_it(self):
        self.app.run()
        self.assertEqual(len(self.app.exception), 0)
        self.generate.assert_called_once()
        self.assertEqual(len(self.app.session_state["brief"]["report"]["items"]), 3)
        self.assertEqual(
            [tab.label for tab in self.app.tabs],
            ["Morning brief", "Questions", "Delivery", "Records"],
        )
        self.assertTrue(any(
            "Tuesday, October 06, 2026 at 12:00 PM EDT" in element.value
            for element in self.app.markdown
        ))
        self.app.run()
        self.app.selectbox(key="analysis_section").set_value("Restocking").run()
        self.app.selectbox(key="analysis_section").set_value("Stock reconciliation").run()
        self.assertEqual(len(self.app.exception), 0)
        self.generate.assert_called_once()

    def test_followup_receives_the_question_and_previous_answer(self):
        self.app.run()
        self.app.chat_input[0].set_value("What should I check before ordering?").run()
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.generate.call_args.args[2], "What should I check before ordering?")
        self.assertEqual(self.generate.call_args.args[3], [])
        self.app.chat_input[0].set_value("And when is the cutoff?").run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 3)
        question, history = self.generate.call_args.args[2:]
        self.assertEqual(question, "And when is the cutoff?")
        self.assertEqual(history[0], {
            "role": "user", "content": "What should I check before ordering?",
        })
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(json.loads(history[1]["content"])["title"], "Answer")
        self.assertEqual(len(self.app.session_state["messages"]), 2)
        self.app.run()
        self.assertEqual(self.generate.call_count, 3)

    def test_failed_question_remains_visible_until_explicit_retry(self):
        self.app.run()
        self.generate.side_effect = OpenAIError("Simulated API failure")
        self.app.chat_input[0].set_value("Why did purchases change?").run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 2)
        turn = self.app.session_state["messages"][0]
        self.assertEqual(turn["question"], "Why did purchases change?")
        self.assertIsNone(turn["result"])
        self.assertEqual(turn["error"], "Simulated API failure")
        self.assertIn("Simulated API failure", [item.value for item in self.app.error])
        self.app.run()
        self.assertEqual(self.generate.call_count, 2)
        self.generate.side_effect = self.report
        next(button for button in self.app.button if button.label == "Retry this answer").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 3)
        self.assertEqual(len(self.app.session_state["messages"]), 1)
        self.assertIsNotNone(self.app.session_state["messages"][0]["result"])
        self.assertIsNone(self.app.session_state["messages"][0]["error"])
        self.assertEqual(self.generate.call_args.args[2], "Why did purchases change?")

    def test_failed_brief_retries_only_after_the_retry_button(self):
        self.generate.side_effect = OpenAIError("Simulated morning API failure")
        self.app.run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertIsNone(self.app.session_state["brief"])
        self.assertEqual(self.generate.call_count, 1)
        self.assertTrue(any(
            "Your records and delivery rules are still available" in item.value
            for item in self.app.error
        ))
        self.assertIn("Simulated morning API failure", [item.value for item in self.app.caption])
        self.app.run()
        self.assertEqual(self.generate.call_count, 1)
        self.generate.side_effect = self.report
        next(button for button in self.app.button if button.label == "Retry morning brief").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 2)
        self.assertIsNotNone(self.app.session_state["brief"])

    def test_retry_after_a_later_answer_preserves_history_and_appends_the_answer(self):
        self.app.run()
        self.generate.side_effect = OpenAIError("Simulated API failure")
        self.app.chat_input[0].set_value("Why did purchases change?").run()
        self.generate.side_effect = self.report
        self.app.chat_input[0].set_value("What is the next delivery?").run()
        later_answer = self.app.session_state["messages"][1]["result"]
        next(button for button in self.app.button if button.label == "Retry this answer").click().run()

        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 4)
        messages = self.app.session_state["messages"]
        self.assertEqual(len(messages), 3)
        self.assertIsNone(messages[0]["result"])
        self.assertEqual(messages[1]["result"], later_answer)
        self.assertIsNotNone(messages[2]["result"])
        question, history = self.generate.call_args.args[2:]
        self.assertEqual(question, "Why did purchases change?")
        self.assertEqual(history[1], {"role": "user", "content": "What is the next delivery?"})
        self.assertEqual(json.loads(history[2]["content"]), later_answer["report"])

    def test_correction_rebuilds_brief_and_marks_old_chat_as_historical(self):
        self.app.run()
        self.app.chat_input[0].set_value("When should I order from Alder?").run()
        old_revision = self.app.session_state["messages"][0]["revision"]
        self.app.radio(key="update_kind").set_value("Confirmed correction")
        self.app.text_area(key="update_message").set_value("SIMULATED: Alder confirms Monday/Thursday, noon the day before.")
        self.app.multiselect(key="update_weekdays").set_value(["Monday", "Thursday"])
        self.app.checkbox(key="update_cutoff_known").check().run()
        self.app.number_input(key="update_lead_days").set_value(1)
        self.app.time_input(key="update_cutoff_time").set_value(time(12))
        next(button for button in self.app.button if button.label == "Save delivery update").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 3)
        self.assertEqual(len(self.generate.call_args.args), 2)
        alder = next(
            vendor for vendor in self.generate.call_args.args[0]["delivery"]
            if vendor["vendor_id"] == "V1"
        )
        self.assertEqual(alder["next_delivery"], "2026-10-08")
        self.assertEqual(alder["deadline"], "2026-10-07T12:00:00-04:00")
        self.assertTrue(any(
            "Wednesday, October 07, 2026 at 12:00 PM EDT" in element.value
            for element in self.app.markdown
        ))
        self.assertIn("source_updates#UPD-001", self.generate.call_args.args[1])
        self.assertNotEqual(self.app.session_state["brief_revision"], old_revision)
        self.assertTrue(any(
            "Historical answer — delivery rules have changed" in caption.value
            for caption in self.app.caption
        ))
        self.app.chat_input[0].set_value("Does the earlier cutoff still apply?").run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 4)
        history = self.generate.call_args.args[3]
        self.assertTrue(history[1]["content"].startswith(
            "Historical answer under now-obsolete delivery rules: "
        ))
        alder = next(
            vendor for vendor in self.generate.call_args.args[0]["delivery"]
            if vendor["vendor_id"] == "V1"
        )
        self.assertEqual(alder["next_delivery"], "2026-10-08")
        self.app.run()
        self.assertEqual(self.generate.call_count, 4)

    def test_future_conflict_warns_and_shows_both_upcoming_rule_sources(self):
        self.app.run()
        self.app.radio(key="update_kind").set_value("Unresolved conflicting message")
        message = "SIMULATED: an unconfirmed message says Thursday from October 6."
        self.app.text_area(key="update_message").set_value(message)
        self.app.multiselect(key="update_weekdays").set_value(["Monday", "Thursday"])
        self.app.checkbox(key="update_cutoff_known").check().run()
        self.app.number_input(key="update_lead_days").set_value(1)
        self.app.time_input(key="update_cutoff_time").set_value(time(12))
        self.app.date_input(key="update_start").set_value(date(2026, 10, 6))
        next(button for button in self.app.button if button.label == "Save delivery update").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 2)
        self.assertTrue(any(
            "Conflicting delivery information" in warning.value for warning in self.app.warning
        ))
        alder = next(
            vendor for vendor in self.generate.call_args.args[0]["delivery"]
            if vendor["vendor_id"] == "V1"
        )
        self.assertFalse(alder["conflicted"])
        self.assertTrue(alder["next_conflicted"])
        self.assertIsNone(alder["next_delivery"])
        details = next(
            expander for expander in self.app.expander
            if expander.label.startswith("Schedule details & sources")
            and any("GM_OPS_LOG.md · N09" in item.value for item in expander.caption)
        )
        self.assertTrue(any("source_updates · UPD-001" in item.value for item in details.caption))
        self.assertIn(message, [item.value for item in details.markdown])
        self.assertTrue(any("**Monday, Wednesday**" in item.value for item in details.markdown))
        self.assertTrue(any("**Monday, Thursday**" in item.value for item in details.markdown))
        self.assertTrue(any("Effective 2026-10-06" in item.value for item in details.caption))

    def test_future_conflict_with_unknown_cutoff_still_warns_and_reaches_the_model(self):
        self.app.run()
        self.app.selectbox(key="update_supplier").set_value("V2")
        self.app.radio(key="update_kind").set_value("Unresolved conflicting message")
        message = "SIMULATED: Juniper says Tuesday from October 6; cutoff unknown."
        self.app.text_area(key="update_message").set_value(message)
        self.app.multiselect(key="update_weekdays").set_value(["Tuesday"])
        self.app.date_input(key="update_start").set_value(date(2026, 10, 6))
        next(button for button in self.app.button if button.label == "Save delivery update").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 2)
        self.assertTrue(any(
            "Conflicting delivery information" in warning.value for warning in self.app.warning
        ))
        juniper = next(
            vendor for vendor in self.generate.call_args.args[0]["delivery"]
            if vendor["vendor_id"] == "V2"
        )
        self.assertFalse(juniper["conflicted"])
        self.assertTrue(juniper["next_conflicted"])
        self.assertFalse(juniper["cutoff_known"])
        self.assertIsNone(juniper["next_delivery"])
        self.assertIsNone(juniper["deadline"])
        expected_sources = {"data/vendors.csv#V2", "source_updates#UPD-001"}
        self.assertEqual(
            {rule["source_ref"] for rule in juniper["upcoming_rules"]}, expected_sources,
        )
        details = next(
            expander for expander in self.app.expander
            if expander.label.startswith("Schedule details & sources")
            and any(
                "vendor_id" in table.value.columns and "V2" in set(table.value["vendor_id"])
                for table in expander.dataframe
            )
        )
        self.assertTrue(any("source_updates · UPD-001" in item.value for item in details.caption))
        self.assertIn(message, [item.value for item in details.markdown])
        self.assertTrue(any("**Monday, Wednesday, Friday**" in item.value for item in details.markdown))
        self.assertTrue(any("**Tuesday**" in item.value for item in details.markdown))
        self.assertTrue(any("Effective 2026-10-06" in item.value for item in details.caption))

    def test_cutoff_and_expiry_controls_toggle_without_model_calls(self):
        self.app.run()
        self.assertIsNone(self.app.radio(key="update_kind").value)
        self.assertNotIn("update_lead_days", [widget.key for widget in self.app.number_input])
        self.assertNotIn("update_cutoff_time", [widget.key for widget in self.app.time_input])
        self.assertNotIn("update_end", [widget.key for widget in self.app.date_input])

        self.app.checkbox(key="update_cutoff_known").check().run()
        self.assertIsNone(self.app.number_input(key="update_lead_days").value)
        self.assertIsNone(self.app.time_input(key="update_cutoff_time").value)
        self.assertNotIn("update_end", [widget.key for widget in self.app.date_input])
        self.app.checkbox(key="update_expires").check().run()
        self.assertEqual(self.app.date_input(key="update_end").value, date(2026, 10, 11))

        self.app.checkbox(key="update_cutoff_known").uncheck().run()
        self.assertNotIn("update_lead_days", [widget.key for widget in self.app.number_input])
        self.assertNotIn("update_cutoff_time", [widget.key for widget in self.app.time_input])
        self.assertIn("update_end", [widget.key for widget in self.app.date_input])
        self.app.checkbox(key="update_expires").uncheck().run()
        self.assertNotIn("update_end", [widget.key for widget in self.app.date_input])
        self.assertEqual(len(self.app.exception), 0)
        self.generate.assert_called_once()

    def test_hidden_cutoff_and_end_are_saved_as_unknown_even_after_entry(self):
        self.app.run()
        self.app.radio(key="update_kind").set_value("Confirmed correction")
        self.app.text_area(key="update_message").set_value("SIMULATED: Monday/Thursday; cutoff and end date unconfirmed.")
        self.app.multiselect(key="update_weekdays").set_value(["Monday", "Thursday"])
        self.app.checkbox(key="update_cutoff_known").check().run()
        self.app.checkbox(key="update_expires").check().run()
        self.app.number_input(key="update_lead_days").set_value(1)
        self.app.time_input(key="update_cutoff_time").set_value(time(12))
        self.app.date_input(key="update_end").set_value(date(2026, 10, 15))
        self.app.run()
        self.app.checkbox(key="update_cutoff_known").uncheck()
        self.app.checkbox(key="update_expires").uncheck().run()
        self.generate.assert_called_once()
        next(button for button in self.app.button if button.label == "Save delivery update").click().run()

        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 2)
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT lead_days, cutoff_time, effective_to FROM delivery_rules WHERE id = 'RULE-UPD-001'"
            ).fetchone()
        self.assertEqual(row, (None, None, None))

    def test_confirmed_cutoff_requires_both_fields_without_saving_partial_updates(self):
        self.app.run()
        self.app.radio(key="update_kind").set_value("Confirmed correction")
        self.app.text_area(key="update_message").set_value("SIMULATED: Alder confirms Monday/Thursday with a cutoff.")
        self.app.multiselect(key="update_weekdays").set_value(["Monday", "Thursday"])
        self.app.checkbox(key="update_cutoff_known").check().run()
        with closing(sqlite3.connect(self.db_path)) as connection:
            original_rule_count = connection.execute("SELECT COUNT(*) FROM delivery_rules").fetchone()[0]

        for lead_days, cutoff_time in [(None, None), (1, None), (None, time(12))]:
            with self.subTest(lead_days=lead_days, cutoff_time=cutoff_time):
                self.app.number_input(key="update_lead_days").set_value(lead_days)
                self.app.time_input(key="update_cutoff_time").set_value(cutoff_time)
                next(button for button in self.app.button if button.label == "Save delivery update").click().run()
                self.assertEqual(len(self.app.exception), 0)
                self.assertTrue(any(
                    "Enter both the number of days before delivery and the cutoff time" in item.value
                    for item in self.app.error
                ))
                with closing(sqlite3.connect(self.db_path)) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_updates").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM delivery_rules").fetchone()[0], original_rule_count)
                self.generate.assert_called_once()

    def test_example_question_uses_the_chat_flow_once(self):
        self.app.run()
        next(button for button in self.app.button if button.label == "What changed last week?").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.generate.call_count, 2)
        question = "What changed in sales last week compared with the previous week?"
        self.assertEqual(self.generate.call_args.args[2:], (question, []))
        self.assertEqual(len(self.app.session_state["messages"]), 1)
        self.assertEqual(self.app.session_state["messages"][0]["question"], question)
        self.assertIsNotNone(self.app.session_state["messages"][0]["result"])
        self.assertNotIn("What changed last week?", [button.label for button in self.app.button])
        self.app.run()
        self.assertEqual(self.generate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
