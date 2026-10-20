"""Generate auditable examples with an isolated delivery-memory database."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis import build_facts
from memory import delivery_context, initialize, save_rule


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def current_state(db_path):
    facts, sources = build_facts(ROOT)
    context = delivery_context(db_path)
    facts["delivery"] = context["vendors"]
    sources.update(context["sources"])
    return facts, sources, context


def write_conversations(path, completed):
    lines = [
        "# Actual model examples",
        "",
        "These are accepted responses from actual calls to the production "
        "`assistant.generate` function. No model answer is fabricated.",
        "Business time is fixed at 2026-10-05 06:00 America/New_York. "
        "Delivery changes marked SIMULATED exist only in a temporary database.",
        "Each linked JSON contains the exact request messages, accepted structured "
        "response, reported token usage, estimated cost, and expanded source records. "
        "It does not contain a separate raw provider-response envelope.",
        "",
    ]
    for entry in completed:
        result = entry["result"]
        lines += [
            f"## {entry['case']}",
            "",
            f"State: {entry['memory_stage']}.",
            "",
            f"**Prompt:** {entry['question'] or 'Generate the morning brief.'}",
            "",
            f"**{result['report']['title']}**",
            "",
        ]
        for index, item in enumerate(result["report"]["items"], 1):
            lines += [f"### {index}. {item['title']}", ""]
            for claim in item["evidence"]:
                references = ", ".join(f"`{reference}`" for reference in claim["sources"])
                lines += [f"- {claim['text']} Sources: {references}."]
            lines += [
                "",
                f"Interpretation: {item['interpretation']}",
                "",
                f"Next step: {item['next_step']}",
                "",
            ]
            if item["unknowns"]:
                lines += ["Unknowns:", "", *[f"- {text}" for text in item["unknowns"]], ""]
        usage = result["usage"]
        lines += [
            f"Model: `{result['model']}`. Input tokens: {usage['input_tokens']}; "
            f"output tokens: {usage['output_tokens']}; "
            f"estimated cost: ${result['estimated_cost_usd']:.6f}.",
            "",
            f"[Actual request and response with expanded evidence]({entry['file']})",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="Compute only the real memory demonstration; no model calls.")
    mode.add_argument("--live", action="store_true", help="Generate actual model examples; requires an OpenAI API key.")
    parser.add_argument("--output", type=Path, default=ROOT / "examples")
    args = parser.parse_args()

    if args.live:
        from assistant import api_key, generate, resolve_sources
        try:
            api_key()
        except ValueError as error:
            raise SystemExit(str(error)) from None
        if (args.output / "live-run.json").exists():
            raise SystemExit("This output already contains a live run. Choose a new --output directory to preserve it.")
    args.output.mkdir(parents=True, exist_ok=True)
    completed = []
    manifest = {
        "kind": "actual_model_calls",
        "business_now": "2026-10-05T06:00:00-04:00",
        "database": "TemporaryDirectory; production application memory is not modified.",
        "status": "running",
        "calls": [],
        "estimated_total_cost_usd": "0",
        "notes": "Costs are estimates returned by assistant.generate, not billing confirmations.",
    }
    if args.live:
        (args.output / "calls").mkdir(exist_ok=True)
        write_json(args.output / "live-run.json", manifest)

    with tempfile.TemporaryDirectory(prefix="marble-examples-") as directory:
        db_path = Path(directory) / "memory.sqlite3"
        initialize(db_path)
        facts, sources, context = current_state(db_path)
        memory_stage = "baseline"
        stages = []

        def capture_stage(name):
            stages.append({
                "stage": name,
                "snapshot": facts["snapshot"],
                "delivery": facts["delivery"],
                "rule_history": context["history"],
                "update_sources": context["sources"],
            })

        def call(case, question=None, history=None):
            filename = f"calls/{len(completed) + 1:02d}-{case}.json"
            print(f"Generating {case}...", flush=True)
            try:
                result = generate(facts, sources, question, history)
            except Exception as error:
                manifest.update(status="failed", failed_case=case, error_type=type(error).__name__)
                write_json(args.output / "live-run.json", manifest)
                raise SystemExit(
                    f"{case} failed ({type(error).__name__}). Prior results are preserved; "
                    "no replacement answer was generated. Error text is omitted from artifacts to protect credentials."
                ) from None
            resolved = {}
            for item in result["report"]["items"]:
                for claim in item["evidence"]:
                    for reference in resolve_sources(claim["sources"], facts, sources):
                        resolved[reference] = sources[reference]
            entry = {
                "kind": "actual_model_call", "case": case, "question": question,
                "memory_stage": memory_stage, "file": filename,
                "result": result, "resolved_source_records": resolved,
            }
            write_json(args.output / filename, entry)
            completed.append(entry)
            manifest["calls"].append({
                "case": case, "file": filename, "memory_stage": memory_stage,
                "model": result["model"], "usage": result["usage"],
                "estimated_cost_usd": result["estimated_cost_usd"],
            })
            manifest["estimated_total_cost_usd"] = str(sum(
                (Decimal(str(item["result"]["estimated_cost_usd"])) for item in completed), Decimal(0),
            ))
            write_json(args.output / "live-run.json", manifest)
            write_conversations(args.output / "live-conversations.md", completed)
            if history is not None:
                history.extend([
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": json.dumps(result["report"], ensure_ascii=False)},
                ])

        capture_stage(memory_stage)
        if args.live:
            call("initial-morning-brief")
            sales_history = []
            call(
                "last-week-sales",
                "What changed in net sales and menu quantities last week compared with the previous week?",
                sales_history,
            )
            call(
                "sales-follow-up",
                "Which menu items explain that change, including breakfast tacos and the Weekend special?",
                sales_history,
            )
            call(
                "food-cost-false-premise",
                "Why did our food cost go up last week? Is chicken now cheaper because the new cases cost less?",
            )
            call(
                "unexplained-usage",
                "Did Ivo cause our food waste? Explain what the inventory differences, "
                "unweighed cooler waste and missing Weekend special recipe can actually establish.",
            )
            call(
                "upcoming-saturday",
                "Are breakfast tacos canceled for Saturday October 10 because of the egg hold? "
                "What do the egg count and INV-099 tell us about preparation, and what remains unconfirmed?",
            )
            call(
                "unsupported-calculations",
                "Give me net sales for September 18–23 and our exact actual food cost and gross margin "
                "for that period. Distinguish calculations this prototype has not made from data we do not have.",
            )
            delivery_history = []
            call(
                "delivery-baseline",
                "What is Alder's next normal delivery with a cutoff that has not passed, and its order deadline? "
                "Does that prove nothing will arrive today?",
                delivery_history,
            )

        correction_id = save_rule(
            db_path, vendor_id="V1", weekdays=[0, 3], lead_days=1, cutoff_time="12:00",
            effective_from="2026-10-05", effective_to=None, kind="correction",
            learned_at="2026-10-05T05:00:00-04:00",
            source_text=(
                "SIMULATED DEMO MESSAGE, not an actual supplier update. Explicit correction: "
                "from October 5, 2026, Alder delivers Monday and Thursday instead of Monday and Wednesday. "
                "Orders remain due at noon on the day before delivery."
            ),
        )
        facts, sources, context = current_state(db_path)
        memory_stage = "simulated-correction"
        capture_stage(memory_stage)
        if args.live:
            call("corrected-morning-brief")
            call(
                "delivery-follow-up-after-correction",
                "After the saved supplier correction, which next delivery and order deadline should I use now? "
                "Please update the answer you gave earlier in this conversation.",
                delivery_history,
            )

        restarted = subprocess.run(
            [
                sys.executable, "-c",
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "from memory import initialize, delivery_context\n"
                "database = Path(sys.argv[1])\n"
                "initialize(database)\n"
                "print(json.dumps({'pid': os.getpid(), 'parent_pid': os.getppid(), "
                "'context': delivery_context(database)}))\n",
                str(db_path),
            ],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        restart_result = json.loads(restarted.stdout)
        restart_process = {
            "parent_pid": os.getpid(),
            "child_pid": restart_result["pid"],
            "child_reported_parent_pid": restart_result["parent_pid"],
            "return_code": restarted.returncode,
        }
        context = restart_result["context"]
        facts, sources = build_facts(ROOT)
        facts["delivery"] = context["vendors"]
        sources.update(context["sources"])
        memory_stage = "restart-after-correction"
        capture_stage(memory_stage)
        stages[-1]["process_evidence"] = restart_process
        if args.live:
            call(
                "delivery-after-restart",
                "What is Alder's next normal delivery with a cutoff that has not passed, and its order deadline?",
            )

        conflict_id = save_rule(
            db_path, vendor_id="V1", weekdays=[0, 2], lead_days=1, cutoff_time="12:00",
            effective_from="2026-10-05", effective_to=None, kind="conflict",
            learned_at="2026-10-05T05:30:00-04:00",
            source_text=(
                "SIMULATED UNCONFIRMED DEMO MESSAGE, not an actual supplier update. "
                "A separate message claims Alder still delivers Monday and Wednesday from October 5, "
                "with orders due at noon the previous day. This conflicts with the saved Monday/Thursday "
                "correction and has not been confirmed; do not treat it as a correction."
            ),
        )
        facts, sources, context = current_state(db_path)
        memory_stage = "simulated-unresolved-conflict"
        capture_stage(memory_stage)
        if args.live:
            call(
                "delivery-unresolved-conflict",
                "We now have an unconfirmed message contradicting the saved correction. "
                "Which delivery and deadline are confirmed, what are the conditional possibilities, and what should I do?",
                delivery_history,
            )

            save_rule(
                db_path, vendor_id="V2", weekdays=[0, 2, 4], lead_days=1, cutoff_time="12:00",
                effective_from="2026-10-12", effective_to=None, kind="correction",
                source_text=(
                    "SIMULATED DEMO MESSAGE, not an actual supplier update. From October 12, "
                    "Juniper delivers Monday, Wednesday and Friday, with orders due at noon "
                    "the previous day. This does not establish the cutoff before October 12."
                ),
            )
            facts, sources, context = current_state(db_path)
            memory_stage = "simulated-future-juniper-rule"
            capture_stage(memory_stage)
            call(
                "source-wording-and-future-cutoff",
                "Explain the pepper price comparison, distinguishing source observations from analysis. "
                "When does the notebook first mention the Weekend special button, and what does that "
                "establish about when it was added? Does the unconfirmed INV-099 mean no chicken "
                "was counted? Finally, for the saved Juniper rule starting October 12, distinguish "
                "the rule's effective date, delivery date and order deadline. Is that definitely "
                "the next delivery we can still order for, or are earlier windows unknown?",
            )

        alder = [next(vendor for vendor in stage["delivery"] if vendor["vendor_id"] == "V1") for stage in stages]
        checks = {
            "baseline_delivery_and_deadline": (
                alder[0]["next_delivery"] == "2026-10-07"
                and alder[0]["deadline"] == "2026-10-06T12:00:00-04:00"
            ),
            "correction_changes_computed_dates": (
                alder[1]["next_delivery"] == "2026-10-08"
                and alder[1]["deadline"] == "2026-10-07T12:00:00-04:00"
                and alder[1]["next_source_ref"] == f"source_updates#{correction_id}"
            ),
            "restart_preserves_rule_and_dates": alder[1] == alder[2],
            "restart_used_a_new_process": (
                restart_process["child_pid"] != restart_process["parent_pid"]
                and restart_process["child_reported_parent_pid"] == restart_process["parent_pid"]
                and restart_process["return_code"] == 0
            ),
            "conflict_has_no_selected_delivery": (
                alder[3]["conflicted"] is True and alder[3]["next_delivery"] is None
                and alder[3]["deadline"] is None
            ),
            "both_conflicting_sources_retained": {
                f"source_updates#{correction_id}", f"source_updates#{conflict_id}"
            }.issubset(rule["source_ref"] for rule in alder[3]["rules"]),
        }
        write_json(args.output / "memory-demo.json", {
            "kind": "deterministic_memory_demo",
            "model_calls_in_this_artifact": 0,
            "explanation": (
                "Actual build_facts and delivery_context outputs from an isolated temporary SQLite database. "
                "The input messages are explicitly simulated. This artifact contains no model responses. "
                "Restart launches a new Python process that initializes and reads the same persisted SQLite "
                "database. The parent rebuilds the facts with that child's delivery_context output before "
                "any live follow-up. No conversation is retained for the post-restart question."
            ),
            "stages": stages, "checks": checks,
        })
        if not all(checks.values()):
            raise SystemExit("Memory demonstration checks failed; inspect memory-demo.json.")
    if args.live:
        manifest["status"] = "complete"
        write_json(args.output / "live-run.json", manifest)
        print(f"Saved {len(completed)} actual calls; estimated total cost ${manifest['estimated_total_cost_usd']}.")
    else:
        print(f"Saved memory-demo.json: {len(checks)} checks passed; zero model calls.")


if __name__ == "__main__":
    main()
