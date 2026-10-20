import hashlib
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

import streamlit as st
from openai import OpenAIError

from analysis import build_facts
from assistant import generate, resolve_sources
from memory import delivery_context, initialize, save_rule

ROOT = Path(__file__).resolve().parent
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def render_report(result, facts, sources, *, ranked=False):
    report = result["report"]
    st.subheader(report["title"].replace("$", r"\$"))
    for index, item in enumerate(report["items"], 1):
        with st.container(border=True):
            st.markdown(f"**{str(index) + '. ' if ranked else ''}{item['title']}**".replace("$", r"\$"))
            for claim in item["evidence"]:
                st.markdown(claim["text"].replace("$", r"\$"))
            st.markdown(f"**Why it matters:** {item['interpretation']}".replace("$", r"\$"))
            st.markdown(f"**Next step:** {item['next_step']}".replace("$", r"\$"))
            if item["unknowns"]:
                st.markdown(("**Still unknown:** " + " ".join(item["unknowns"])).replace("$", r"\$"))
            refs = list(dict.fromkeys(
                ref for claim in item["evidence"]
                for ref in resolve_sources(claim["sources"], facts, sources)
            ))
            if refs:
                with st.expander(f"Evidence · {len(refs)} source records"):
                    for ref in refs:
                        record = sources[ref]
                        st.caption(f"{record['file']} · {record['id']}")
                        if isinstance(record["content"], str):
                            st.text(record["content"])
                        else:
                            st.json(record["content"], expanded=False)
    usage = result["usage"]
    st.caption(
        f"{result['model']} · {usage['input_tokens']:,} input / "
        f"{usage['output_tokens']:,} output tokens · "
        f"estimated ${result['estimated_cost_usd']:.4f}"
    )


st.set_page_config(page_title="Lantern Yard · Operations", page_icon="☀", layout="wide")
st.title("Lantern Yard")
st.caption("OPERATIONS BRIEF · MONDAY, OCTOBER 5, 2026 · 6:00 AM NEW YORK")
st.markdown("Know what needs attention before placing the next order.")
st.caption("Source period: September 14–October 4. Stock is the closing count after October 4 service.")

db_path = Path(os.environ.get("MARBLE_DB_PATH", str(ROOT / ".local" / "memory.sqlite3")))
initialize(db_path)
facts, sources = build_facts()
memory = delivery_context(db_path)
facts["delivery"] = memory["vendors"]
sources.update(memory["sources"])
revision = hashlib.sha256(json.dumps(memory["history"], sort_keys=True).encode()).hexdigest()

if "messages" not in st.session_state:
    st.session_state.messages = []
if st.session_state.get("brief_revision") != revision:
    st.session_state.brief_revision = revision
    st.session_state.brief = None
    st.session_state.brief_error = None
    with st.spinner("Preparing the morning brief…"):
        try:
            st.session_state.brief = generate(facts, sources)
        except (OpenAIError, ValueError, RuntimeError) as exc:
            st.session_state.brief_error = str(exc)

brief_tab, chat_tab, evidence_tab = st.tabs(["Morning brief", "Ask a question", "Evidence & delivery rules"])

with brief_tab:
    if st.session_state.brief is not None:
        render_report(st.session_state.brief, facts, sources, ranked=True)
    else:
        st.error(st.session_state.brief_error)
        if st.button("Retry morning brief"):
            st.session_state.brief_revision = None
            st.rerun()

with chat_tab:
    st.caption("Ask about weekly changes, purchase prices, count differences, or the last-week demand baseline.")
    st.caption("Custom date ranges and custom sales forecasts are outside this prototype's calculated scope.")
    for index, turn in enumerate(st.session_state.messages):
        with st.chat_message("user"):
            st.write(turn["question"].replace("$", r"\$"))
        with st.chat_message("assistant"):
            if turn["revision"] != revision:
                st.caption("Historical answer — delivery rules have changed since this question.")
            if turn["result"] is not None:
                render_report(turn["result"], facts, sources)
            else:
                st.error(turn["error"])
                if st.button("Retry this answer", key=f"retry_{index}"):
                    st.session_state.retry_question = index
                    st.rerun()
    question = st.chat_input("What should I check before ordering?")
    retry = st.session_state.pop("retry_question", None)
    if question is not None or retry is not None:
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
                history.append({
                    "role": "assistant",
                    "content": prefix + json.dumps(old["result"]["report"]),
                })
        turn = st.session_state.messages[target]
        turn["revision"] = revision
        with st.spinner("Checking the evidence…"):
            try:
                turn["result"] = generate(facts, sources, turn["question"], history)
                turn["error"] = None
            except (OpenAIError, ValueError, RuntimeError) as exc:
                turn["error"] = str(exc)
        st.rerun()

with evidence_tab:
    st.subheader("Delivery rules")
    st.caption("Schedules are recorded rules, not confirmed orders or promised deliveries.")
    for vendor in memory["vendors"]:
        with st.container(border=True):
            st.markdown(f"**{vendor['vendor_name']}**")
            if vendor["conflicted"] or vendor["next_conflicted"]:
                st.warning("Conflicting delivery information. Confirm with the supplier before relying on a date.")
            if vendor["today_is_delivery"]:
                st.write("Today is a scheduled delivery day; existing orders and arrivals still need confirmation.")
            if vendor["next_delivery"]:
                st.write(f"Next scheduled delivery you can still order for: {vendor['next_delivery_label']}")
                st.write(f"Order by: {vendor['deadline_label']}")
            elif not vendor["cutoff_known"]:
                st.write("The order cutoff is not recorded. Confirm today's arrangements and ordering deadline.")
            else:
                st.write("No unambiguous upcoming order window is available. Confirm the schedule and cutoff.")
            with st.expander("Current rules and their sources"):
                st.json({"current": vendor["rules"], "upcoming": vendor["upcoming_rules"]})

    with st.expander("Update a delivery rule"):
        st.caption("Save a supplier message as a rule change or an unresolved conflict. This does not place an order.")
        with st.form("delivery_update"):
            vendors = {item["vendor_id"]: item["vendor_name"] for item in memory["vendors"]}
            vendor_id = st.selectbox("Supplier", list(vendors), format_func=vendors.__getitem__)
            kind = st.radio("Message type", ["Confirmed correction", "Unresolved conflicting message"])
            source_text = st.text_area("Supplier message / source")
            weekdays = st.multiselect("Delivery days", WEEKDAYS)
            cutoff_known = st.checkbox("The order cutoff is known", value=False)
            lead_days = st.number_input("Days before delivery", min_value=0, max_value=7, value=1)
            cutoff = st.text_input("Local cutoff time (HH:MM)", value="12:00")
            start = st.date_input("Effective from", value=date(2026, 10, 5))
            expires = st.checkbox("This rule has an end date", value=False)
            end = st.date_input("Effective through (inclusive)", value=date(2026, 10, 11))
            submitted = st.form_submit_button("Save delivery update")
        if submitted:
            try:
                save_rule(
                    db_path, vendor_id=vendor_id,
                    weekdays=[WEEKDAYS.index(day) for day in weekdays],
                    lead_days=int(lead_days) if cutoff_known else None,
                    cutoff_time=cutoff if cutoff_known else None,
                    effective_from=start.isoformat(),
                    effective_to=end.isoformat() if expires else None,
                    source_text=source_text,
                    kind="correction" if kind == "Confirmed correction" else "conflict",
                )
            except (ValueError, sqlite3.Error) as exc:
                st.error(f"Update not saved: {exc}")
            else:
                st.rerun()

    with st.expander("Delivery rule history"):
        st.json(memory["history"])
    st.subheader("Calculated facts")
    st.caption("Computed in Python. Demand baselines are scenarios; count differences are not measured waste.")
    section = st.selectbox("Analysis", ["sales", "purchasing", "consumption", "restocking", "operational_context"])
    st.json(facts[section], expanded=1)
    with st.expander("Calculation input groups"):
        for name, refs in facts["evidence_sets"].items():
            st.markdown(f"**{name}**")
            st.caption(" · ".join(ref.replace("#", " · ") for ref in refs))
    st.subheader("Original source records")
    ref = st.selectbox("Source", list(sources), format_func=lambda value: value.replace("#", " · "))
    record = sources[ref]
    if isinstance(record["content"], str):
        st.text(record["content"])
    else:
        st.json(record["content"])
