import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assistant import Claim, Finding, Report, generate, resolve_sources


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.facts = {"evidence_sets": {"week": ["data/pos_daily.csv#P1"]}}
        self.sources = {"data/pos_daily.csv#P1": {
            "file": "data/pos_daily.csv", "id": "P1", "content": {"net_sales": "10"}
        }}
        self.item = Finding(
            title="Check the count",
            evidence=[Claim(text="Recorded sales", sources=["set:week"])],
            interpretation="Check before ordering.",
            next_step="Review the supplied record.", unknowns=[],
        )

    def test_groups_resolve_to_original_rows_and_invalid_refs_fail(self):
        self.assertEqual(resolve_sources(
            ["set:week", "data/pos_daily.csv#P1"], self.facts, self.sources),
            ["data/pos_daily.csv#P1"])
        for refs in [["made-up#ID"], ["set:missing"]]:
            with self.assertRaises(ValueError):
                resolve_sources(refs, self.facts, self.sources)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-not-a-real-key"})
    @patch("assistant.OpenAI")
    def test_one_request_no_retry_and_current_facts_with_history(self, sdk):
        usage = SimpleNamespace(input_tokens=1000,
                                input_tokens_details=SimpleNamespace(cached_tokens=100),
                                output_tokens=100)
        usage.model_dump = lambda: {"input_tokens": 1000, "output_tokens": 100}
        parse = sdk.return_value.__enter__.return_value.responses.parse
        parse.return_value = SimpleNamespace(
            status="completed", output_parsed=Report(title="Answer", items=[self.item]),
            usage=usage, model="test-model",
        )
        result = generate(self.facts, self.sources, "What changed?", [
            {"role": "assistant", "content": "Historical answer under older rules."}])
        parse.assert_called_once()
        self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)
        self.assertFalse(parse.call_args.kwargs["store"])
        self.assertNotIn("tools", parse.call_args.kwargs)
        report_schema = parse.call_args.kwargs["text_format"].model_json_schema()
        self.assertEqual(
            set(report_schema["$defs"]["SourcedClaim"]["properties"]["sources"]["items"]["enum"]),
            {"data/pos_daily.csv#P1", "set:week"},
        )
        self.assertIn("CURRENT_FACTS", result["request"][1]["content"])
        self.assertEqual(result["request"][-1]["content"], "What changed?")
        self.assertEqual(result["estimated_cost_usd"], 0.003775)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-not-a-real-key"})
    @patch("assistant.OpenAI")
    def test_bad_citation_and_incomplete_responses_are_not_answers(self, sdk):
        parse = sdk.return_value.__enter__.return_value.responses.parse
        parse.return_value = SimpleNamespace(status="incomplete", output_parsed=None)
        with self.assertRaises(RuntimeError):
            generate(self.facts, self.sources, "Question")
        self.item.evidence[0].sources = ["invented#ID"]
        parse.return_value = SimpleNamespace(
            status="completed", output_parsed=Report(title="Bad", items=[self.item]))
        with self.assertRaises(ValueError):
            generate(self.facts, self.sources, "Question")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-not-a-real-key"})
    @patch("assistant.OpenAI")
    def test_brief_requires_three_to_five_items(self, sdk):
        sdk.return_value.__enter__.return_value.responses.parse.return_value = SimpleNamespace(
            status="completed", output_parsed=Report(title="Too short", items=[self.item]))
        with self.assertRaises(ValueError):
            generate(self.facts, self.sources)


if __name__ == "__main__":
    unittest.main()
