import hashlib
import json
import os
import sqlite3
from datetime import date
from html import escape
from pathlib import Path

import streamlit as st
from openai import OpenAIError

from analysis import build_facts
from assistant import generate, resolve_sources
from memory import delivery_context, initialize, save_rule
from presentation import SOURCE_LABELS, render_analysis, render_sources

ROOT = Path(__file__).resolve().parent
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def rule_summary(rule):
    days = ", ".join(WEEKDAYS[day] for day in rule["weekdays"])
    if rule["lead_days"] is None or rule["cutoff_time"] is None:
        cutoff = "Order cutoff not recorded"
    elif rule["lead_days"] == 0:
        cutoff = f"Order by {rule['cutoff_time']} on delivery day"
    elif rule["lead_days"] == 1:
        cutoff = f"Order by {rule['cutoff_time']} the day before"
    else:
        cutoff = f"Order by {rule['cutoff_time']}, {rule['lead_days']} days before"
    return days, cutoff


def render_report(result, facts, sources, *, key, ranked=False):
    report = result["report"]
    if ranked:
        st.subheader("Your morning priorities")
        st.caption(f"{len(report['items'])} items, ranked for this morning's ordering decisions.")
    else:
        st.subheader(report["title"].replace("$", r"\$"))
    for index, item in enumerate(report["items"], 1):
        with st.container(border=True, key=f"report_{key}_{index}"):
            number = f'<span class="priority-number">{index:02}</span>' if ranked else ""
            st.html(f'<div class="priority-heading">{number}<h3>{escape(item["title"])}</h3></div>')
            st.html(
                '<div class="next-step"><span class="eyebrow">NEXT STEP</span>'
                f'<p>{escape(item["next_step"])}</p></div>'
            )
            st.markdown(f"**Why it matters** · {item['interpretation']}".replace("$", r"\$"))
            if item["evidence"] and not ranked:
                st.html('<div class="eyebrow evidence-label">FROM THE RECORDS</div>')
                st.markdown("\n".join(
                    f"{i}. {claim['text']}" for i, claim in enumerate(item["evidence"], 1)
                ).replace("$", r"\$"))
            if item["unknowns"]:
                st.html(
                    '<div class="unknowns"><strong>Still to confirm</strong><ul>'
                    + "".join(f"<li>{escape(value)}</li>" for value in item["unknowns"])
                    + '</ul></div>'
                )
            if item["evidence"]:
                label = "Evidence & sources" if ranked else "Inspect sources"
                count = len(item["evidence"])
                with st.expander(f"{label} · {count} {'observation' if count == 1 else 'observations'}", icon=":material/description:"):
                    if ranked:
                        st.markdown("\n".join(
                            f"{i}. {claim['text']}" for i, claim in enumerate(item["evidence"], 1)
                        ).replace("$", r"\$"))
                    selected = st.selectbox(
                        "Which observation?", range(len(item["evidence"])),
                        format_func=lambda i: f"{i + 1}. {item['evidence'][i]['text']}",
                        key=f"sources_{key}_{index}",
                    )
                    refs = resolve_sources(item["evidence"][selected]["sources"], facts, sources)
                    st.caption(f"{len(refs)} original {'record' if len(refs) == 1 else 'records'} · complete sources for this observation.")
                    render_sources(refs, sources)
    with st.expander(f"Generation details · estimated ${result['estimated_cost_usd']:.4f}"):
        st.caption(f"Model: {result['model']} · Costs are estimates, not billing confirmation.")
        st.write(
            f"{result['usage']['input_tokens']:,} input tokens · "
            f"{result['usage']['output_tokens']:,} output tokens"
        )


st.set_page_config(page_title="Lantern Yard · Operations", page_icon="☀", layout="wide")
st.html("""
<style>
[data-testid="stMainBlockContainer"] {max-width: 1120px; padding: 4.5rem 2rem 5rem;}
h1 {font-family: Georgia, serif !important; font-weight: 400 !important; letter-spacing: -.035em; color: #203d32 !important;}
h3 {font-size: 1.12rem !important; line-height: 1.45 !important;}
[role="tablist"] {gap: 1.5rem; margin-bottom: 1.4rem;}
[role="tab"] {font-weight: 600; padding: .65rem 0;}
[class*="st-key-report_"] {background: #fff; border-color: #dfe5df; border-radius: 14px; padding: 1.5rem; margin-bottom: .75rem;}
.priority-heading {display: flex; align-items: baseline; gap: .8rem; margin-bottom: .8rem;}
.priority-heading h3 {margin: 0; padding: 0; font-size: 1.2rem; font-weight: 650;}
.priority-number {font-size: .85rem; font-weight: 700; color: #547361; font-variant-numeric: tabular-nums;}
.eyebrow {font-size: .7rem; font-weight: 700; letter-spacing: .12em; color: #486657;}
.next-step {background: #eef4ee; border-left: 3px solid #397652; border-radius: 0 8px 8px 0; padding: .8rem 1rem; margin-bottom: .6rem;}
.next-step p {margin: .25rem 0 0; font-size: 1.02rem; line-height: 1.6; color: #243c2d;}
.evidence-label {margin-top: .5rem;}
.unknowns {font-size: .88rem; line-height: 1.6; color: #5d665e; padding: .55rem 0;}
.unknowns ul {margin: .25rem 0 0; padding-left: 1.2rem;}
[data-testid="stCaptionContainer"] {opacity: 1;}
[data-testid="stCaptionContainer"] p {color: #59685e;}
[data-testid="stExpander"] {border-color: #dfe5df;}
[data-testid="stChatMessage"] {background: transparent; padding: 1rem 0;}
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] {padding: 4rem 1rem 4rem;}
  h1 {font-size: 2rem !important;}
  [role="tablist"] {gap: .5rem;}
  [class*="st-key-report_"] {padding: 1rem;}
  .priority-heading h3 {font-size: 1.05rem;}
}
</style>
<div class="eyebrow">LANTERN YARD KITCHEN · OPERATIONS</div>
""")
st.title("Morning operations")
st.caption("Working snapshot · Monday, October 5, 2026 · 6:00 AM New York")
st.caption("Records: September 14–October 4 · Stock: October 4 closing count, not live inventory")

db_path = Path(os.environ.get("MARBLE_DB_PATH", str(ROOT / ".local" / "memory.sqlite3")))
initialize(db_path)
facts, sources = build_facts()
memory = delivery_context(db_path)
facts["delivery"] = memory["vendors"]
sources.update(memory["sources"])
revision = hashlib.sha256(json.dumps(memory["history"], sort_keys=True).encode()).hexdigest()

brief_tab, chat_tab, delivery_tab, records_tab = st.tabs([
    "Morning brief", "Questions", "Delivery", "Records",
])
if "messages" not in st.session_state:
    st.session_state.messages = []

with brief_tab:
    if st.session_state.get("brief_revision") != revision:
        st.session_state.brief_revision = revision
        st.session_state.brief = None
        st.session_state.brief_error = None
        with st.spinner("Preparing your morning brief. This can take a minute…", show_time=True):
            try:
                st.session_state.brief = generate(facts, sources)
            except (OpenAIError, ValueError, RuntimeError) as exc:
                st.session_state.brief_error = str(exc)
    if st.session_state.brief is not None:
        render_report(st.session_state.brief, facts, sources, key="brief", ranked=True)
    else:
        st.error("The morning brief could not be generated. Your records and delivery rules are still available.")
        st.caption(st.session_state.brief_error)
        if st.button("Retry morning brief"):
            st.session_state.brief_revision = None
            st.rerun()

with chat_tab:
    st.subheader("Ask about your operation")
    st.caption("Answers use the supplied records. Ask a follow-up to keep the conversation going.")
    suggested = None
    if not st.session_state.messages:
        st.markdown("Start with a question")
        questions = {
            "What changed last week?": "What changed in sales last week compared with the previous week?",
            "Why did purchasing change?": "Why did purchasing as a share of sales change last week?",
            "What needs checking before Saturday?": "What should I check before Saturday October 10, and what is still unknown?",
        }
        for label, prompt in questions.items():
            if st.button(label, width="stretch"):
                suggested = prompt
        st.caption("Covers weekly sales, purchase prices, stock differences and repeat-last-week demand scenarios. Custom date ranges and forecasts are not calculated.")
    for index, turn in enumerate(st.session_state.messages):
        with st.chat_message("user"):
            st.write(turn["question"].replace("$", r"\$"))
        with st.chat_message("assistant"):
            if turn["revision"] != revision:
                st.caption("Historical answer — delivery rules have changed since this question.")
            if turn["result"] is not None:
                render_report(turn["result"], facts, sources, key=f"chat_{index}")
            else:
                st.error(turn["error"])
                if st.button("Retry this answer", key=f"retry_{index}"):
                    st.session_state.retry_question = index
                    st.rerun()
    question = st.chat_input("Ask a question about these records…") or suggested
    retry = st.session_state.pop("retry_question", None)
    if question is not None or retry is not None:
        if retry is not None and retry < len(st.session_state.messages) - 1:
            question = st.session_state.messages[retry]["question"]
            retry = None
        if retry is None:
            st.session_state.messages.append({
                "question": question, "result": None, "error": None, "revision": revision,
            })
            target = len(st.session_state.messages) - 1
        else:
            target = retry
        history = []
        for old in st.session_state.messages[:target]:
            history.append({"role": "user", "content": old["question"]})
            if old["result"] is not None:
                prefix = "Historical answer under now-obsolete delivery rules: " if old["revision"] != revision else ""
                history.append({"role": "assistant", "content": prefix + json.dumps(old["result"]["report"])})
        turn = st.session_state.messages[target]
        turn["revision"] = revision
        with st.spinner("Checking the records. This can take a minute…", show_time=True):
            try:
                turn["result"] = generate(facts, sources, turn["question"], history)
                turn["error"] = None
            except (OpenAIError, ValueError, RuntimeError) as exc:
                turn["error"] = str(exc)
        st.rerun()

with delivery_tab:
    st.subheader("Delivery & ordering")
    st.caption("All times are New York time. A recorded route is not a confirmed order or arrival.")
    saved_update = st.session_state.pop("saved_delivery_update", None)
    if saved_update:
        if st.session_state.brief is not None:
            st.success(f"Delivery update {saved_update} saved. Your morning brief has been updated.")
        else:
            st.warning(f"Delivery update {saved_update} saved. The brief could not be refreshed; retry it in Morning brief.")
    for vendor in memory["vendors"]:
        with st.container(border=True):
            st.subheader(vendor["vendor_name"])
            if vendor["conflicted"] or vendor["next_conflicted"]:
                st.warning("Conflicting delivery information. Confirm the schedule with the supplier before planning an order.")
            elif not vendor["cutoff_known"]:
                st.info("Confirm the order cutoff with this supplier.")
            if vendor["next_delivery"]:
                delivery, deadline = st.columns(2)
                delivery.markdown("**Next orderable delivery**")
                delivery.write(vendor["next_delivery_label"])
                deadline.markdown("**Order by**")
                deadline.write(vendor["deadline_label"])
            elif not vendor["cutoff_known"]:
                st.write("The next orderable delivery is unknown until the missing cutoff is confirmed.")
            else:
                st.write("No single upcoming order window is confirmed.")
            for rule in vendor["rules"]:
                days, cutoff_text = rule_summary(rule)
                st.markdown(f"**{days}** · {cutoff_text}")
            if vendor["today_is_delivery"]:
                st.caption("Today is a scheduled delivery day. Check existing orders and physical arrivals separately.")
            rules = {rule["id"]: rule for rule in [*vendor["rules"], *vendor["upcoming_rules"]]}
            with st.expander(f"Schedule details & sources · {len(rules)} recorded {'rule' if len(rules) == 1 else 'rules'}"):
                for rule in rules.values():
                    days, cutoff_text = rule_summary(rule)
                    end_text = f" through {rule['effective_to']}" if rule["effective_to"] else " · no end date recorded"
                    st.markdown(f"**{days}** · {cutoff_text}")
                    st.caption(f"Effective {rule['effective_from']}{end_text} · Recorded {rule['learned_at']}")
                    if (vendor["conflicted"] or vendor["next_conflicted"]) and rule["next_delivery"]:
                        st.write(f"If confirmed: {rule['next_delivery_label']} · order by {rule['deadline_label']}")
                    render_sources([rule["source_ref"]], sources)

    with st.expander("Add a supplier update", icon=":material/edit_note:"):
        st.caption("Record a confirmed change or keep a conflicting message for review. Saving updates the brief; it does not place an order.")
        vendors = {item["vendor_id"]: item["vendor_name"] for item in memory["vendors"]}
        vendor_id = st.selectbox("Supplier", list(vendors), format_func=vendors.__getitem__, key="update_supplier")
        kind = st.radio(
            "Message type", ["Confirmed correction", "Unresolved conflicting message"],
            index=None, key="update_kind",
            captions=["Replace overlapping rules with this confirmed information.", "Keep both versions until the supplier confirms which is correct."],
        )
        source_text = st.text_area("Original supplier message", key="update_message", placeholder="Paste or transcribe the supplier's exact message…")
        weekdays = st.multiselect("Delivery days", WEEKDAYS, key="update_weekdays")
        cutoff_known = st.checkbox("I have a confirmed order cutoff", key="update_cutoff_known")
        lead_days, cutoff = None, None
        if cutoff_known:
            lead_col, time_col = st.columns(2)
            lead_days = lead_col.number_input("Days before delivery", min_value=0, max_value=7, value=None, step=1, key="update_lead_days", placeholder="0 = delivery day, 1 = day before")
            cutoff = time_col.time_input("Cutoff time · New York", value=None, key="update_cutoff_time")
        else:
            st.caption("The cutoff will remain unknown. No ordering deadline will be invented.")
        start = st.date_input("Effective from", value=date(2026, 10, 5), key="update_start")
        expires = st.checkbox("This is a temporary rule", key="update_expires")
        end = st.date_input("Last day this rule applies", value=max(start, date(2026, 10, 11)), key="update_end") if expires else None
        if st.button("Save delivery update", type="primary"):
            try:
                if kind is None:
                    raise ValueError("Choose whether this is a confirmed correction or a conflicting message.")
                if not weekdays:
                    raise ValueError("Choose at least one delivery day.")
                if cutoff_known and (lead_days is None or cutoff is None):
                    raise ValueError("Enter both the number of days before delivery and the cutoff time.")
                update_id = save_rule(
                    db_path, vendor_id=vendor_id, weekdays=[WEEKDAYS.index(day) for day in weekdays],
                    lead_days=int(lead_days) if cutoff_known else None,
                    cutoff_time=cutoff.strftime("%H:%M") if cutoff_known else None,
                    effective_from=start.isoformat(), effective_to=end.isoformat() if expires else None,
                    source_text=source_text,
                    kind="correction" if kind == "Confirmed correction" else "conflict",
                )
            except (ValueError, sqlite3.Error) as exc:
                st.error(f"Update not saved: {exc}")
            else:
                st.session_state.saved_delivery_update = update_id
                st.rerun()

    with st.expander(f"Rule history · {len(memory['history'])} recorded versions"):
        st.caption("Earlier versions stay available as evidence. Recorded time and effective dates describe different things.")
        rows = []
        for rule in reversed(memory["history"]):
            days, cutoff_text = rule_summary(rule)
            rows.append({
                "Supplier": rule["vendor_name"], "Delivery days": days, "Order cutoff": cutoff_text,
                "Applies now": "Yes" if rule["active"] else "No", "Effective from": rule["effective_from"],
                "Effective through": rule["effective_to"] or "No end date", "Recorded": rule["learned_at"],
                "Source": rule["source_ref"], "Rule ID": rule["id"],
                "Replaces": ", ".join(rule["supersedes"]) or "None",
            })
        st.dataframe(rows, hide_index=True, width="stretch")
        history_rule = st.selectbox(
            "Read a version's original source", list(reversed(memory["history"])),
            format_func=lambda rule: f"{rule['vendor_name']} · effective {rule['effective_from']} · {rule['source_ref'].split('#')[-1]}",
        )
        render_sources([history_rule["source_ref"]], sources)

with records_tab:
    st.subheader("Explore the records")
    st.caption("Calculated summaries and the original records behind them. All amounts are USD.")
    render_analysis(facts)
    with st.expander("Trace a calculation to its inputs", icon=":material/account_tree:"):
        labels = {
            "sales": "Sales", "purchasing": "Purchases", "change": "Change", "ingredient": "Ingredient",
            "vendor": "Supplier", "prices": "Unit prices", "consumption": "Stock reconciliation",
            "restocking": "Restocking", "whole_period": "Sep 14–Oct 4", "full_week": "Full-week baseline",
            "weekend": "Weekend-only baseline", "missing_recipes": "Missing recipes", "invoice": "Invoice",
            "anomaly": "Invoice review", "invoice_total_mismatch": "Header / line mismatch",
            "price_differs_from_same_sku_median": "Price comparison",
            "week_1": "Sep 14–20", "week_2": "Sep 21–27", "week_3": "Sep 28–Oct 4",
        }
        labels.update({record["id"]: record["content"]["name"] for record in sources.values() if record["file"] == "data/menu.csv"})
        labels.update({vendor["vendor_id"]: vendor["vendor_name"] for vendor in memory["vendors"]})
        group_labels = {name: " · ".join(labels.get(part, part) for part in name.split(".")) for name in facts["evidence_sets"]}
        group = st.selectbox("Calculation", list(group_labels), format_func=group_labels.__getitem__)
        render_sources(facts["evidence_sets"][group], sources)
    st.divider()
    st.subheader("Original records")
    files = list(dict.fromkeys(record["file"] for record in sources.values()))
    filename = st.selectbox("Record collection", files, format_func=SOURCE_LABELS.__getitem__)
    refs = [ref for ref, record in sources.items() if record["file"] == filename]
    render_sources(refs, sources)
