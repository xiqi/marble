"""Persistent supplier delivery rules with explicit, dated corrections."""

import csv
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
ZONE = ZoneInfo("America/New_York")
BUSINESS_NOW = datetime(2026, 10, 5, 6, tzinfo=ZONE)
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD.")
    return date.fromisoformat(value)


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Recorded times must be ISO timestamps with a timezone.")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Recorded times must include a timezone.")
    return parsed.astimezone(ZONE)


def _rules_on(rules: list[dict], day: date) -> list[dict]:
    applicable = [
        rule for rule in rules
        if _date(rule["effective_from"]) <= day
        and (rule["effective_to"] is None or day <= _date(rule["effective_to"]))
    ]
    superseded = {rule_id for rule in applicable for rule_id in rule["supersedes"]}
    return [rule for rule in applicable if rule["id"] not in superseded]


def initialize(db_path: Path, root: Path = ROOT) -> None:
    with (root / "data/vendors.csv").open(newline="", encoding="utf-8") as handle:
        vendors = list(csv.DictReader(handle))
    log = (root / "GM_OPS_LOG.md").read_text(encoding="utf-8")
    note = re.search(r"^### N09 \| ([^|]+) \| [^\n]+\n(.+?)(?=\n###|\Z)", log, re.M | re.S)
    if note is None:
        raise ValueError("GM_OPS_LOG.md must contain N09.")
    learned_at = datetime.fromisoformat(note.group(1).strip()).replace(tzinfo=ZONE)
    initial = []
    for vendor in vendors:
        reviewed = _date(vendor["last_reviewed"])
        weekdays = [DAY_NAMES.index(day.strip()) for day in vendor["delivery_days"].split(",")]
        initial.append((
            f"BASE-{vendor['vendor_id']}", vendor["vendor_id"], vendor["vendor_name"],
            json.dumps(weekdays), None, None,
            datetime.combine(reviewed, time.min, ZONE).isoformat(),
            reviewed.isoformat(), None, f"data/vendors.csv#{vendor['vendor_id']}", "[]",
        ))
    alder = next(vendor for vendor in vendors if vendor["vendor_id"] == "V1")
    initial.append((
        "N09-V1", "V1", alder["vendor_name"], "[0, 2]", 1, "12:00",
        learned_at.isoformat(), "2026-09-28", None, "GM_OPS_LOG.md#N09", '["BASE-V1"]',
    ))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS source_updates (
                    id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL,
                    learned_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS delivery_rules (
                    id TEXT PRIMARY KEY,
                    vendor_id TEXT NOT NULL,
                    vendor_name TEXT NOT NULL,
                    weekdays TEXT NOT NULL,
                    lead_days INTEGER,
                    cutoff_time TEXT,
                    learned_at TEXT NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    source_ref TEXT NOT NULL UNIQUE,
                    supersedes TEXT NOT NULL
                )
            """)
            connection.executemany(
                "INSERT OR IGNORE INTO delivery_rules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                initial,
            )


def save_rule(
    db_path: Path,
    *,
    vendor_id: str,
    weekdays: list[int],
    lead_days: int | None,
    cutoff_time: str | None,
    effective_from: str,
    effective_to: str | None,
    source_text: str,
    kind: str,
    learned_at: str = BUSINESS_NOW.isoformat(),
) -> str:
    if (
        not isinstance(weekdays, list) or not weekdays
        or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
        or len(set(weekdays)) != len(weekdays)
    ):
        raise ValueError("Select distinct delivery weekdays from 0 (Monday) to 6 (Sunday).")
    if lead_days is not None and (type(lead_days) is not int or lead_days < 0):
        raise ValueError("Lead days must be a non-negative integer or unknown.")
    if cutoff_time is not None:
        if not isinstance(cutoff_time, str) or not re.fullmatch(r"\d{2}:\d{2}", cutoff_time):
            raise ValueError("Cutoff time must use HH:MM or remain unknown.")
        time.fromisoformat(cutoff_time)
    start = _date(effective_from)
    end = _date(effective_to) if effective_to is not None else None
    if end is not None and end < start:
        raise ValueError("The effective end cannot precede the start.")
    recorded = _timestamp(learned_at)
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("The original supplier message is required.")
    if kind not in ("correction", "conflict"):
        raise ValueError("Choose an explicit correction or an unresolved conflict.")

    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM delivery_rules WHERE vendor_id = ? ORDER BY rowid", (vendor_id,)
                )
            ]
            if not existing:
                raise ValueError("Unknown supplier.")
            supersedes = []
            if kind == "correction":
                supersedes = [
                    rule["id"] for rule in existing
                    if _timestamp(rule["learned_at"]) <= recorded
                    and (end is None or _date(rule["effective_from"]) <= end)
                    and (rule["effective_to"] is None or start <= _date(rule["effective_to"]))
                ]
            cursor = connection.execute(
                "INSERT INTO source_updates (content, learned_at) VALUES (?, ?)",
                (source_text.strip(), recorded.isoformat()),
            )
            update_id = f"UPD-{cursor.lastrowid:03d}"
            connection.execute(
                "INSERT INTO delivery_rules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"RULE-{update_id}", vendor_id, existing[0]["vendor_name"],
                    json.dumps(sorted(weekdays)), lead_days, cutoff_time, recorded.isoformat(),
                    start.isoformat(), end.isoformat() if end is not None else None,
                    f"source_updates#{update_id}", json.dumps(supersedes),
                ),
            )
    return update_id


def delivery_context(db_path: Path, now: datetime = BUSINESS_NOW) -> dict:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The business clock must include a timezone.")
    now = now.astimezone(ZONE)
    today = now.date()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        rules = []
        for row in connection.execute("SELECT * FROM delivery_rules ORDER BY rowid"):
            rule = dict(row)
            if _timestamp(rule["learned_at"]) <= now:
                rule["weekdays"] = json.loads(rule["weekdays"])
                rule["supersedes"] = json.loads(rule["supersedes"])
                rules.append(rule)
        sources = {}
        for row in connection.execute("SELECT * FROM source_updates ORDER BY id"):
            if _timestamp(row["learned_at"]) <= now:
                update_id = f"UPD-{row['id']:03d}"
                sources[f"source_updates#{update_id}"] = {
                    "file": "source_updates", "id": update_id, "content": row["content"],
                    "learned_at": row["learned_at"],
                }

    vendors = []
    for vendor_id in dict.fromkeys(rule["vendor_id"] for rule in rules):
        vendor_rules = [rule for rule in rules if rule["vendor_id"] == vendor_id]
        current = _rules_on(vendor_rules, today)
        current_ids = {rule["id"] for rule in current}
        for rule in vendor_rules:
            rule["active"] = rule["id"] in current_ids
            rule["next_delivery"] = None
            rule["deadline"] = None
            rule["next_delivery_label"] = None
            rule["deadline_label"] = None

        # Rules are constant between their effective-date boundaries.
        boundaries = {today}
        for rule in vendor_rules:
            start = _date(rule["effective_from"])
            if start > today:
                boundaries.add(start)
            if rule["effective_to"] is not None:
                end = _date(rule["effective_to"])
                if end >= today and end < date.max:
                    boundaries.add(end + timedelta(days=1))
        boundaries = sorted(boundaries)
        unknown_deliveries = []
        for index, start in enumerate(boundaries):
            end = boundaries[index + 1] if index + 1 < len(boundaries) else None
            for rule in _rules_on(vendor_rules, start):
                if rule["lead_days"] is None or rule["cutoff_time"] is None:
                    earliest = max(start, today + timedelta(days=rule["lead_days"] or 0))
                    if rule["cutoff_time"] is not None and time.fromisoformat(rule["cutoff_time"]) <= now.time():
                        earliest = max(earliest, today + timedelta(days=(rule["lead_days"] or 0) + 1))
                    delivery = min(
                        earliest + timedelta(days=(weekday - earliest.weekday()) % 7)
                        for weekday in rule["weekdays"]
                    )
                    if end is None or delivery < end:
                        unknown_deliveries.append(delivery)
                    continue
                earliest = max(start, today + timedelta(days=rule["lead_days"]))
                cutoff = time.fromisoformat(rule["cutoff_time"])
                candidates = []
                for weekday in rule["weekdays"]:
                    delivery = earliest + timedelta(days=(weekday - earliest.weekday()) % 7)
                    deadline = datetime.combine(
                        delivery - timedelta(days=rule["lead_days"]), cutoff, ZONE,
                    )
                    if deadline <= now:
                        delivery += timedelta(days=7)
                        deadline += timedelta(days=7)
                    if end is None or delivery < end:
                        candidates.append((delivery, deadline))
                if candidates:
                    delivery, deadline = min(candidates)
                    if rule["next_delivery"] is None or delivery.isoformat() < rule["next_delivery"]:
                        rule["next_delivery"] = delivery.isoformat()
                        rule["deadline"] = deadline.isoformat()
                        rule["next_delivery_label"] = delivery.strftime("%A, %B %d, %Y")
                        rule["deadline_label"] = deadline.strftime("%A, %B %d, %Y at %I:%M %p %Z")

        signatures = {
            (tuple(rule["weekdays"]), rule["lead_days"], rule["cutoff_time"])
            for rule in current
        }
        conflicted = len(signatures) > 1
        possibilities = [rule for rule in vendor_rules if rule["next_delivery"] is not None]
        next_rule = min(possibilities, key=lambda rule: rule["next_delivery"]) if possibilities else None
        upcoming_days = [day for day in boundaries if day > today]
        if next_rule is not None:
            next_day = _date(next_rule["next_delivery"])
            upcoming_days = [day for day in upcoming_days if day < next_day] + [next_day]
        next_conflicted = False
        next_rules = []
        upcoming_ids = set()
        for day in upcoming_days:
            applicable = _rules_on(vendor_rules, day)
            next_conflicted |= len({
                (tuple(rule["weekdays"]), rule["lead_days"], rule["cutoff_time"])
                for rule in applicable
            }) > 1
            for rule in applicable:
                if rule["id"] not in upcoming_ids:
                    next_rules.append(rule)
                    upcoming_ids.add(rule["id"])
        unknown_before_next = bool(unknown_deliveries) and (
            next_rule is None or min(unknown_deliveries) <= _date(next_rule["next_delivery"])
        )
        safe_next = next_rule if not conflicted and not next_conflicted and not unknown_before_next else None
        vendors.append({
            "vendor_id": vendor_id,
            "vendor_name": vendor_rules[0]["vendor_name"],
            "rules": current,
            "upcoming_rules": next_rules,
            "conflicted": conflicted,
            "next_conflicted": next_conflicted,
            "today_is_delivery": (
                today.weekday() in current[0]["weekdays"] if current and not conflicted else None
            ),
            "cutoff_known": not unknown_before_next and bool(current) and all(
                rule["lead_days"] is not None and rule["cutoff_time"] is not None for rule in current
            ),
            "next_delivery": safe_next["next_delivery"] if safe_next is not None else None,
            "deadline": safe_next["deadline"] if safe_next is not None else None,
            "next_delivery_label": safe_next["next_delivery_label"] if safe_next is not None else None,
            "deadline_label": safe_next["deadline_label"] if safe_next is not None else None,
            "next_source_ref": safe_next["source_ref"] if safe_next is not None else None,
        })
    return {"vendors": vendors, "history": rules, "sources": sources}
