import hashlib
import json
import os
import sqlite3
from datetime import date, datetime
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


def clear_delivery_draft():
    st.session_state.update({
        "update_kind": None,
        "update_message": "",
        "update_weekdays": [],
        "update_cutoff_known": False,
        "update_lead_days": None,
        "update_cutoff_time": None,
        "update_start": date(2026, 10, 5),
        "update_expires": False,
    })
    st.session_state.pop("update_end", None)


def render_report(result, facts, sources, *, key, ranked=False):
    report = result["report"]
    for index, item in enumerate(report["items"], 1):
        with st.container(border=ranked, key=f"{'report' if ranked else 'answer'}_{key}_{index}"):
            number = f'<span class="priority-number">{index:02}</span>' if ranked else ""
            st.html(f'<div class="priority-heading">{number}<h3>{escape(item["title"])}</h3></div>')
            if ranked:
                st.html(
                    '<div class="next-step"><span class="eyebrow">NEXT STEP</span>'
                    f'<p>{escape(item["next_step"])}</p></div>'
                )
                st.markdown(f"**Why it matters** · {item['interpretation']}".replace("$", r"\$"))
            else:
                st.markdown(item["interpretation"].replace("$", r"\$"))
            if item["evidence"] and not ranked:
                st.html('<div class="eyebrow evidence-label">FROM THE RECORDS</div>')
                st.markdown("\n".join(
                    f"{i}. {claim['text']}" for i, claim in enumerate(item["evidence"], 1)
                ).replace("$", r"\$"))
            if not ranked:
                st.markdown(f"**Next step:** {item['next_step']}".replace("$", r"\$"))
            if item["unknowns"]:
                st.html(
                    '<div class="unknowns"><strong>Still to confirm</strong><ul>'
                    + "".join(f"<li>{escape(value)}</li>" for value in item["unknowns"])
                    + '</ul></div>'
                )
            if item["evidence"]:
                count = len(item["evidence"])
                with st.expander(f"Supporting records · {count} {'observation' if count == 1 else 'observations'}", icon=":material/description:"):
                    if ranked:
                        st.markdown("\n".join(
                            f"{i}. {claim['text']}" for i, claim in enumerate(item["evidence"], 1)
                        ).replace("$", r"\$"))
                    selected = st.selectbox(
                        "Show records for", range(len(item["evidence"])),
                        format_func=lambda i: f"Observation {i + 1}",
                        key=f"sources_{key}_{index}",
                    )
                    refs = resolve_sources(item["evidence"][selected]["sources"], facts, sources)
                    render_sources(refs, sources)


st.set_page_config(page_title="Lantern Yard · Operations", page_icon="☀", layout="wide")
st.html("""
<style>
[data-testid="stMainBlockContainer"] {max-width: 1120px; padding: 3.5rem 2rem 4rem;}
h1 {font-family: Georgia, serif !important; font-size: 2.25rem !important; font-weight: 400 !important; letter-spacing: -.035em; color: #203d32 !important;}
h3 {font-size: 1.12rem !important; line-height: 1.45 !important;}
[role="tablist"] {gap: 1.5rem; margin-bottom: .7rem;}
[role="tab"] {font-weight: 600; padding: .65rem 0;}
[class*="st-key-report_"] {background: #fff; border-color: #dfe5df; border-radius: 12px; padding: 1rem; margin-bottom: .5rem;}
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
[class*="st-key-question_turn_"] {margin-bottom: 1.5rem;}
.question {background: #eef4ee; border-radius: 10px; padding: .8rem 1rem; margin-bottom: .5rem;}
.question p {margin: .3rem 0 0; color: #243c2d; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere;}
[class*="st-key-answer_"] .priority-heading {margin-bottom: 0;}
[data-testid="stChatInput"] > div {border-color: #9eafa3; background: #fff;}
[data-testid="stChatInput"]:focus-within > div {border-color: #237a57; box-shadow: 0 0 0 1px #237a57;}
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] {padding: 3.5rem 1rem 3rem;}
  h1 {font-size: 1.8rem !important;}
  [role="tablist"] {gap: .5rem;}
  [class*="st-key-report_"] {padding: 1rem;}
  .priority-heading h3 {font-size: 1.05rem;}
}
</style>
""")
st.title("Lantern Yard Kitchen")
st.caption("Monday, October 5, 2026 · 6:00 AM · New York")

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
if "brief" not in st.session_state:
    st.session_state.brief = None
    st.session_state.brief_revision = None
    st.session_state.brief_error = None

with brief_tab:
    st.subheader("Morning brief")
    st.caption("Priorities for stock, purchasing and deliveries, based on records through October 4.")
    if st.session_state.brief is not None and st.session_state.brief_revision != revision:
        st.warning("Delivery schedules have changed. Refresh the morning brief before using its ordering advice.")
    action = "Prepare morning brief" if st.session_state.brief is None else "Refresh morning brief"
    if st.session_state.brief_error:
        action = "Retry morning brief"
    brief_action = st.empty()
    if brief_action.button(action, type="primary", key="prepare_brief"):
        brief_action.empty()
        with st.spinner("Preparing your morning brief…", show_time=True):
            try:
                st.session_state.brief = generate(facts, sources)
            except (OpenAIError, ValueError, RuntimeError) as exc:
                st.session_state.brief_error = str(exc)
            else:
                st.session_state.brief_revision = revision
                st.session_state.brief_error = None
        st.rerun()
    if st.session_state.brief_error:
        st.error("The morning brief could not be updated. Your records and delivery schedules are still available.")
    if st.session_state.brief is not None:
        render_report(st.session_state.brief, facts, sources, key="brief", ranked=True)

with chat_tab:
    st.subheader("Ask about your operation")
    st.caption("Type your own question about sales, stock, purchasing or deliveries.")
    conversation = st.container()
    composer = st.empty()
    with composer.container():
        question = st.chat_input("What would you like to know?")
        if not st.session_state.messages:
            with st.expander("Example questions"):
                questions = {
                    "What changed last week?": "What changed in sales last week compared with the previous week?",
                    "Why did purchasing change?": "Why did purchasing as a share of sales change last week?",
                    "What needs checking before Saturday?": "What should I check before Saturday October 10, and what is still unknown?",
                }
                for label, prompt in questions.items():
                    if st.button(label):
                        question = prompt
    retry = st.session_state.pop("retry_question", None)
    target = None
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
        composer.empty()
    with conversation:
        for index, turn in enumerate(st.session_state.messages):
            with st.container(key=f"question_turn_{index}"):
                st.html(
                    '<div class="question"><span class="eyebrow">YOUR QUESTION</span>'
                    f'<p>{escape(turn["question"])}</p></div>'
                )
                if index == target:
                    history = []
                    for old in st.session_state.messages[:target]:
                        history.append({"role": "user", "content": old["question"]})
                        if old["result"] is not None:
                            prefix = "Historical answer under now-obsolete delivery rules: " if old["revision"] != revision else ""
                            history.append({"role": "assistant", "content": prefix + json.dumps(old["result"]["report"])})
                    turn["revision"] = revision
                    with st.spinner("Checking the records…", show_time=True):
                        try:
                            turn["result"] = generate(facts, sources, turn["question"], history)
                            turn["error"] = None
                        except (OpenAIError, ValueError, RuntimeError) as exc:
                            turn["error"] = str(exc)
                if turn["revision"] != revision:
                    st.caption("Historical answer — delivery schedules have changed since this question.")
                if turn["result"] is not None:
                    render_report(turn["result"], facts, sources, key=f"chat_{index}")
                else:
                    st.error("Your question could not be answered. Please try again.")
                    if st.button("Retry this answer", key=f"retry_{index}"):
                        st.session_state.retry_question = index
                        st.rerun()
    if target is not None:
        st.rerun()

with delivery_tab:
    st.subheader("Delivery & ordering")
    st.caption("All times are New York time.")
    saved_update = st.session_state.pop("saved_delivery_update", None)
    if saved_update:
        clear_delivery_draft()
        st.success("Delivery schedule saved. Refresh the morning brief to include this change.")
    update_form = st.container()
    for vendor in memory["vendors"]:
        with st.container(border=True):
            st.subheader(vendor["vendor_name"])
            if vendor["conflicted"] or vendor["next_conflicted"]:
                st.warning("Conflicting delivery information. Confirm delivery days and the order cutoff with the supplier before planning an order.")
            elif vendor["next_delivery"]:
                delivery, deadline = st.columns(2)
                delivery.markdown("**Next orderable delivery**")
                delivery.write(vendor["next_delivery_label"])
                deadline.markdown("**Order by**")
                deadline.write(vendor["deadline_label"])
            elif not vendor["cutoff_known"]:
                st.info("Ask the supplier for the order cutoff to confirm the next available delivery.")
            else:
                st.write("No single upcoming order window is confirmed.")
            for rule in vendor["rules"]:
                days, cutoff_text = rule_summary(rule)
                st.markdown(f"**{days}** · {cutoff_text}")
            if vendor["today_is_delivery"]:
                st.caption("Today is a regular delivery day. Check with the supplier for any expected arrival.")
            rules = {rule["id"]: rule for rule in [*vendor["rules"], *vendor["upcoming_rules"]]}
            with st.expander("Schedule details and messages"):
                for rule in rules.values():
                    days, cutoff_text = rule_summary(rule)
                    starts = date.fromisoformat(rule["effective_from"])
                    if rule["active"]:
                        status = "Current schedule"
                    elif starts > date(2026, 10, 5):
                        status = f"Starts {starts:%b %d, %Y}"
                    else:
                        status = "Upcoming schedule"
                    unconfirmed = vendor["conflicted"] if rule["active"] else vendor["next_conflicted"]
                    if unconfirmed:
                        status = f"Unconfirmed · {status.lower()}"
                    st.markdown(f"**{status} · {days}** · {cutoff_text}")
                    end_text = f" through {date.fromisoformat(rule['effective_to']):%b %d, %Y}" if rule["effective_to"] else ""
                    st.caption(f"Effective {starts:%b %d, %Y}{end_text} · Recorded {datetime.fromisoformat(rule['learned_at']):%b %d, %Y at %I:%M %p}")
                    if rule["next_delivery"]:
                        window_label = "If confirmed" if vendor["conflicted"] or vendor["next_conflicted"] else "Next available delivery"
                        st.write(f"{window_label}: {rule['next_delivery_label']} · order by {rule['deadline_label']}")
                    render_sources([rule["source_ref"]], sources)

    with update_form.expander("Add a supplier update", icon=":material/edit_note:"):
        st.caption("Save a supplier's schedule change or a conflicting message. This does not place an order.")
        vendors = {item["vendor_id"]: item["vendor_name"] for item in memory["vendors"]}
        vendor_id = st.selectbox(
            "Supplier", list(vendors), format_func=vendors.__getitem__, key="update_supplier",
            on_change=clear_delivery_draft,
        )
        kind = st.radio(
            "Message type", ["Confirmed schedule change", "Conflicting supplier message"],
            index=None, key="update_kind",
            captions=["Update the schedule with confirmed information.", "Keep both messages until the supplier confirms the schedule."],
        )
        source_text = st.text_area("Original supplier message", key="update_message", placeholder="Paste or transcribe the supplier's exact message…")
        weekdays = st.multiselect("Delivery days", WEEKDAYS, key="update_weekdays")
        cutoff_known = st.checkbox("The message includes an order cutoff", key="update_cutoff_known")
        lead_days, cutoff = None, None
        if cutoff_known:
            lead_col, time_col = st.columns(2)
            lead_days = lead_col.number_input("Days before delivery", min_value=0, max_value=7, value=None, step=1, key="update_lead_days", placeholder="0 = delivery day, 1 = day before")
            cutoff = time_col.time_input("Cutoff time · New York", value=None, key="update_cutoff_time")
        else:
            st.caption("Order cutoff not recorded.")
        start = st.date_input("Effective from", value=date(2026, 10, 5), key="update_start")
        expires = st.checkbox("Temporary schedule change", key="update_expires")
        end = st.date_input("Last day", value=max(start, date(2026, 10, 11)), key="update_end") if expires else None
        if st.button("Save delivery update", type="primary"):
            try:
                if kind is None:
                    raise ValueError("Choose whether the schedule change is confirmed or the message conflicts with an existing schedule.")
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
                    kind="correction" if kind == "Confirmed schedule change" else "conflict",
                )
            except ValueError as exc:
                st.error(f"Update not saved: {exc}")
            except sqlite3.Error as exc:
                st.session_state.delivery_error = str(exc)
                st.error("The delivery update could not be saved. Please try again.")
            else:
                st.session_state.saved_delivery_update = update_id
                st.rerun()

    with st.expander(f"Schedule history · {len(memory['history'])} updates"):
        rows = []
        for rule in reversed(memory["history"]):
            days, cutoff_text = rule_summary(rule)
            rows.append({
                "Supplier": rule["vendor_name"], "Delivery days": days, "Order cutoff": cutoff_text,
                "Applies now": "Yes" if rule["active"] else "No", "Effective from": date.fromisoformat(rule["effective_from"]),
                "Effective through": date.fromisoformat(rule["effective_to"]) if rule["effective_to"] else None,
                "Recorded": datetime.fromisoformat(rule["learned_at"]),
            })
        st.dataframe(rows, hide_index=True, width="stretch", placeholder="—", column_config={
            "Effective from": st.column_config.DateColumn(format="MMM D, YYYY"),
            "Effective through": st.column_config.DateColumn(format="MMM D, YYYY"),
            "Recorded": st.column_config.DatetimeColumn(format="MMM D, YYYY, h:mm a", timezone="America/New_York"),
        })
        history_rule = st.selectbox(
            "Schedule record", list(reversed(memory["history"])),
            format_func=lambda rule: f"{rule['vendor_name']} · {rule_summary(rule)[0]} · starts {date.fromisoformat(rule['effective_from']):%b %d, %Y} · recorded {datetime.fromisoformat(rule['learned_at']):%b %d, %Y at %I:%M %p}",
        )
        render_sources([history_rule["source_ref"]], sources)

with records_tab:
    st.subheader("Explore the records")
    st.caption("September 14–October 4 · Inventory last counted October 4 after closing · All amounts in USD.")
    render_analysis(facts, sources)
    section = st.session_state.analysis_section
    calculation_key = f"calculation_{section}"
    if section == "Restocking":
        scenario = facts["restocking"]["scenarios"][st.session_state.restocking_scenario]["id"]
        prefixes = (f"restocking.{scenario}.", f"missing_recipes.{scenario}.", "invoice.")
        calculation_key += f"_{scenario}"
    else:
        prefixes = {
            "Sales": ("sales.",),
            "Purchasing": ("purchasing.", "invoice."),
            "Stock reconciliation": ("consumption.", "missing_recipes.whole_period."),
            "Kitchen notes": (),
        }[section]
    groups = [name for prefix in prefixes for name in facts["evidence_sets"] if name.startswith(prefix)]
    if groups:
        with st.expander("Supporting records", icon=":material/description:"):
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
            group_labels = {name: " · ".join(labels.get(part, part) for part in name.split(".")) for name in groups}
            group = st.selectbox("Calculation", groups, format_func=group_labels.__getitem__, key=calculation_key)
            render_sources(facts["evidence_sets"][group], sources)
    with st.expander("Browse original records"):
        files = list(dict.fromkeys(record["file"] for record in sources.values() if record["file"] in SOURCE_LABELS))
        filename = st.selectbox("Record collection", files, format_func=SOURCE_LABELS.__getitem__)
        refs = [ref for ref, record in sources.items() if record["file"] == filename]
        if filename == "GM_OPS_LOG.md":
            refs = [ref for ref in refs if sources[ref]["id"].startswith("N")]
            note_labels = {}
            for ref in refs:
                _, recorded_at, author = sources[ref]["content"].splitlines()[0].split(" | ")
                note_labels[ref] = f"{datetime.fromisoformat(recorded_at):%b %d, %Y at %I:%M %p} · {author}"
            selected_note = st.selectbox("Notebook entry", refs, index=len(refs) - 1, format_func=note_labels.__getitem__, key="record_note")
            refs = [selected_note]
        render_sources(refs, sources)
