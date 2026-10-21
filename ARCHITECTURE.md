# Architecture

One Streamlit app reads local records. Python calculates totals, units, and
deadlines. GPT-6 Astra explains the results and cites sources. SQLite saves
delivery updates without a separate server. The dataset is small enough to read
directly. I left out search infrastructure, forecasting, and automatic ordering.

Memory keeps supplier messages, delivery rules, and their recorded and effective
dates. Corrections replace overlapping schedules while preserving history.
Conflicts stay unresolved until confirmed. Temporary rules expire, allowing the
applicable permanent schedule to resume. On the next page rerun, delivery changes
mark old briefs and answers outdated. Briefs need an explicit refresh. Chat and
briefs disappear when the session ends; they never become saved facts.

Likely failures include stale records, incorrect delivery updates, slow model
calls, and answers that cite records but draw unsupported conclusions. The clock
is fixed, and file edits do not invalidate old answers. Before production, I
would test changing dates and source files, conflicting updates, database recovery,
and API failures. I would check real responses against their sources, including
misleading questions and instructions hidden in records. Invented deadlines or
unsupported ordering advice would block release. To test usefulness, kitchen
staff would perform comparable planning tasks with the app and their usual
records. Compare time taken, missed priorities, wrong decisions, and whether
staff can verify claims and identify missing information. Repeat after model or
prompt changes. Current local tests do not prove usefulness in a working kitchen.
