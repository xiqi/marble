import csv
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo


TABLES = {
    "vendors": ("vendor_id", "vendor_name account_number delivery_days last_reviewed"),
    "vendor_catalog": ("catalog_row_id", "vendor_id sku description pack_quantity pack_unit order_unit"),
    "invoice_headers": ("invoice_id", "vendor_name vendor_account invoice_date delivery_date status tax delivery_fee invoice_total currency"),
    "invoice_lines": ("row_id", "invoice_id sku item_description quantity unit unit_price line_total"),
    "menu": ("pos_item_id", "name menu_price available_from service"),
    "recipes": ("row_id", "pos_item_id ingredient_name quantity unit yield_portions effective_from"),
    "pos_daily": ("row_id", "business_date pos_item_id item_name quantity_sold gross_sales discounts net_sales"),
    "inventory_counts": ("row_id", "counted_at ingredient_name quantity unit counted_by comment"),
}


def _number(row, field, source, positive=False):
    try:
        value = Decimal(row[field])
    except (InvalidOperation, ValueError):
        raise ValueError(f"{source}: invalid {field}") from None
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f"{source}: invalid {field}")
    return value


def _percent(numerator, denominator):
    if denominator == 0:
        return None
    return (numerator / denominator * 100).quantize(Decimal("0.0001"))


def _change(current, previous):
    return {"absolute": current - previous, "percent": _percent(current - previous, previous)}


def _json_values(value):
    if isinstance(value, Decimal):
        result = format(value, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_values(item) for item in value]
    return value


def build_facts(root: Path = Path(__file__).parent) -> tuple[dict, dict]:
    root = Path(root)
    sources = {}
    tables = {}
    refs = {}
    for name, (id_field, fields) in TABLES.items():
        filename = f"data/{name}.csv"
        with (root / filename).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {id_field, *fields.split()}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"{filename}: missing required columns")
            rows = list(reader)
        seen = set()
        for row in rows:
            if None in row or any(row[field] is None or row[field] == "" for field in required - {"comment"}):
                raise ValueError(f"{filename}: incomplete row")
            row_id = row[id_field]
            if row_id in seen:
                raise ValueError(f"{filename}: duplicate ID {row_id}")
            seen.add(row_id)
            reference = f"{filename}#{row_id}"
            sources[reference] = {"file": filename, "id": row_id, "content": row.copy()}
            refs[(name, row_id)] = reference
        if not rows:
            raise ValueError(f"{filename}: empty table")
        tables[name] = rows

    documents = {}
    notes = {}
    for filename in ("SITE_PROFILE.md", "GM_OPS_LOG.md", "README.md", "ABOUT_MARBLE.md", "CANDIDATE_BRIEF.md"):
        text = (root / filename).read_text(encoding="utf-8")
        documents[filename] = text
        for section in re.split(r"(?m)(?=^#{1,3} )", text):
            if not section.strip():
                continue
            heading = section.splitlines()[0].lstrip("# ").strip()
            if filename == "GM_OPS_LOG.md" and re.match(r"N\d+ \|", heading):
                section_id = heading.split(" | ")[0]
                notes[section_id] = {
                    "recorded_at": heading.split(" | ")[1],
                    "text": section.strip(),
                    "sources": [f"{filename}#{section_id}"],
                }
            else:
                section_id = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
            reference = f"{filename}#{section_id}"
            if reference in sources:
                raise ValueError(f"{filename}: duplicate section {section_id}")
            sources[reference] = {"file": filename, "id": section_id, "content": section.strip()}
    for required_note in ("N03", "N05", "N06", "N07", "N09", "N10", "N11", "N13", "N14", "N15"):
        if required_note not in notes:
            raise ValueError(f"GM_OPS_LOG.md: missing mapped note {required_note}")

    match = re.search(
        r"It is \*\*(\w+, \w+ \d+, \d+) at (\d+) (a\.m\.|p\.m\.), ([\w/]+)\*\*",
        documents["SITE_PROFILE.md"],
    )
    if match is None:
        raise ValueError("SITE_PROFILE.md: unrecognized business snapshot")
    hour = int(match[2]) % 12 + (12 if match[3] == "p.m." else 0)
    now = datetime.strptime(match[1], "%A, %B %d, %Y").replace(hour=hour, tzinfo=ZoneInfo(match[4]))
    opening = {}
    closing = {}
    for row in tables["inventory_counts"]:
        timestamp = datetime.fromisoformat(row["counted_at"])
        if timestamp.tzinfo is None:
            raise ValueError(f"{row['row_id']}: count must include timezone")
        quantity = _number(row, "quantity", row["row_id"])
        if row["row_id"].startswith("COUNT-OPEN-"):
            target = opening
        elif row["row_id"].startswith("COUNT-CLOSE-"):
            target = closing
        else:
            raise ValueError(f"{row['row_id']}: expected opening or closing count")
        if row["ingredient_name"] in target:
            raise ValueError(f"{row['row_id']}: duplicate ingredient count")
        target[row["ingredient_name"]] = {**row, "value": quantity, "time": timestamp}
    if not opening or opening.keys() != closing.keys():
        raise ValueError("inventory_counts: opening and closing ingredients must match")
    opening_times = {row["time"] for row in opening.values()}
    closing_times = {row["time"] for row in closing.values()}
    if len(opening_times) != 1 or len(closing_times) != 1:
        raise ValueError("inventory_counts: expected one opening and one closing count")
    opening_time = next(iter(opening_times))
    closing_time = next(iter(closing_times))
    start, end = opening_time.date(), closing_time.date()
    if end < start or closing_time > now or start.weekday() != 0 or end.weekday() != 6:
        raise ValueError("inventory_counts: invalid complete-week period")
    if (end - start).days != 20:
        raise ValueError("inventory_counts: this analysis expects three complete weeks")
    for ingredient in opening:
        if opening[ingredient]["unit"] != closing[ingredient]["unit"]:
            raise ValueError(f"{ingredient}: count units disagree")

    vendors = {row["vendor_id"]: row for row in tables["vendors"]}
    accounts = {}
    for row in vendors.values():
        date.fromisoformat(row["last_reviewed"])
        if row["account_number"] in accounts:
            raise ValueError("vendors: duplicate account number")
        accounts[row["account_number"]] = row["vendor_id"]
    catalog = {}
    catalog_ids = {row["catalog_row_id"]: row for row in tables["vendor_catalog"]}
    if not {"CAT-CHICKEN", "CAT-CHICKEN20"}.issubset(catalog_ids):
        raise ValueError("vendor_catalog: missing mapped chicken packs")
    chicken_name = catalog_ids["CAT-CHICKEN"]["description"]
    for row in tables["vendor_catalog"]:
        key = (row["vendor_id"], row["sku"])
        if row["vendor_id"] not in vendors or key in catalog:
            raise ValueError(f"{row['catalog_row_id']}: unknown vendor or duplicate vendor SKU")
        ingredient = chicken_name if row["catalog_row_id"] == "CAT-CHICKEN20" else row["description"]
        if ingredient not in opening or row["pack_unit"] != opening[ingredient]["unit"]:
            raise ValueError(f"{row['catalog_row_id']}: ingredient or stock unit cannot be mapped")
        catalog[key] = {
            **row, "ingredient": ingredient,
            "pack_value": _number(row, "pack_quantity", row["catalog_row_id"], positive=True),
        }

    menu = {row["pos_item_id"]: row for row in tables["menu"]}
    for row in menu.values():
        _number(row, "menu_price", row["pos_item_id"])
        date.fromisoformat(row["available_from"])
    recipes = defaultdict(list)
    recipe_versions = set()
    for row in tables["recipes"]:
        effective = date.fromisoformat(row["effective_from"])
        version = (row["pos_item_id"], row["ingredient_name"], effective)
        if row["pos_item_id"] not in menu or row["ingredient_name"] not in opening:
            raise ValueError(f"{row['row_id']}: unknown menu item or ingredient")
        if row["unit"] != opening[row["ingredient_name"]]["unit"] or version in recipe_versions:
            raise ValueError(f"{row['row_id']}: invalid unit or duplicate recipe version")
        recipe_versions.add(version)
        recipes[row["pos_item_id"]].append({
            **row, "effective": effective,
            "per_portion": _number(row, "quantity", row["row_id"]) /
            _number(row, "yield_portions", row["row_id"], positive=True),
        })

    evidence_sets = {}
    weeks = []
    for index in range(3):
        first = start + timedelta(days=index * 7)
        weeks.append({
            "id": f"week_{index + 1}", "start": first.isoformat(),
            "end": (first + timedelta(days=6)).isoformat(),
            "net_sales": Decimal(0), "gross_sales": Decimal(0),
            "discounts": Decimal(0), "quantity_sold": Decimal(0), "menu": {},
        })
    sales_rows = []
    availability_conflicts = []
    for row in tables["pos_daily"]:
        day = date.fromisoformat(row["business_date"])
        if row["pos_item_id"] not in menu or not start <= day <= end:
            raise ValueError(f"{row['row_id']}: unknown item or date outside count period")
        values = {key: _number(row, key, row["row_id"]) for key in
                  ("net_sales", "gross_sales", "discounts", "quantity_sold")}
        if values["gross_sales"] - values["discounts"] != values["net_sales"]:
            raise ValueError(f"{row['row_id']}: gross minus discounts must equal net sales")
        week = weeks[(day - start).days // 7]
        item_id = row["pos_item_id"]
        if values["quantity_sold"] > 0 and day < date.fromisoformat(menu[item_id]["available_from"]):
            availability_conflicts.append({
                "pos_item_id": item_id, "planned_available_from": menu[item_id]["available_from"],
                "business_date": row["business_date"], "quantity_sold": values["quantity_sold"],
                "sources": [refs[("menu", item_id)], refs[("pos_daily", row["row_id"])]],
            })
        if item_id not in week["menu"]:
            group = f"sales.{week['id']}.{item_id}"
            evidence_sets[group] = [refs[("menu", item_id)]]
            week["menu"][item_id] = {
                "name": menu[item_id]["name"], **{key: Decimal(0) for key in values},
                "evidence_sets": [group],
            }
        item = week["menu"][item_id]
        evidence_sets[item["evidence_sets"][0]].append(refs[("pos_daily", row["row_id"])])
        for key, value in values.items():
            item[key] += value
            week[key] += value
        sales_rows.append({**row, "day": day, "values": values, "week_id": week["id"]})
    for week in weeks:
        group = f"sales.{week['id']}"
        evidence_sets[group] = list(dict.fromkeys(
            reference for item in week["menu"].values()
            for reference in evidence_sets[item["evidence_sets"][0]]
        ))
        week["evidence_sets"] = [group]
        for item in week["menu"].values():
            item["net_sales_share_percent"] = _percent(item["net_sales"], week["net_sales"])
            item["evidence_sets"] = [group]
    changes = []
    for previous, current in zip(weeks, weeks[1:]):
        items = {}
        for item_id in menu:
            prior, latest = previous["menu"].get(item_id), current["menu"].get(item_id)
            if prior is None and latest is None:
                continue
            group = f"sales.change.{previous['id']}.{current['id']}.{item_id}"
            input_groups = ([f"sales.{previous['id']}.{item_id}"] if prior else []) + (
                [f"sales.{current['id']}.{item_id}"] if latest else []
            )
            evidence_sets[group] = list(dict.fromkeys(
                reference for input_group in input_groups for reference in evidence_sets[input_group]
            ))
            items[item_id] = {
                "name": menu[item_id]["name"],
                **{key: _change(latest[key] if latest else Decimal(0), prior[key] if prior else Decimal(0))
                   for key in ("net_sales", "quantity_sold")},
                "evidence_sets": [group],
            }
        group = f"sales.change.{previous['id']}.{current['id']}"
        evidence_sets[group] = list(dict.fromkeys(
            reference for input_group in previous["evidence_sets"] + current["evidence_sets"]
            for reference in evidence_sets[input_group]
        ))
        changes.append({
            "from": previous["id"], "to": current["id"],
            **{key: _change(current[key], previous[key]) for key in ("net_sales", "quantity_sold")},
            "menu": items, "evidence_sets": [group],
        })

    headers = {row["invoice_id"]: row for row in tables["invoice_headers"]}
    for row in headers.values():
        if row["vendor_account"] not in accounts or row["currency"] != "USD":
            raise ValueError(f"{row['invoice_id']}: unknown account or unsupported currency")
        date.fromisoformat(row["invoice_date"])
        date.fromisoformat(row["delivery_date"])
        for key in ("tax", "delivery_fee", "invoice_total"):
            _number(row, key, row["invoice_id"])
    if "INV-099" not in headers:
        raise ValueError("invoice_headers: missing mapped disputed invoice INV-099")
    invoice_lines = defaultdict(list)
    anomalies = []
    for row in tables["invoice_lines"]:
        if row["invoice_id"] not in headers:
            raise ValueError(f"{row['row_id']}: unknown invoice")
        header = headers[row["invoice_id"]]
        vendor_id = accounts[header["vendor_account"]]
        if (vendor_id, row["sku"]) not in catalog:
            raise ValueError(f"{row['row_id']}: unknown vendor SKU")
        product = catalog[(vendor_id, row["sku"])]
        if row["unit"] == product["order_unit"]:
            factor = product["pack_value"]
        elif row["unit"] == product["pack_unit"]:
            factor = Decimal(1)
        else:
            raise ValueError(f"{row['row_id']}: unsupported invoice unit")
        quantity = _number(row, "quantity", row["row_id"], positive=True)
        price = _number(row, "unit_price", row["row_id"])
        amount = _number(row, "line_total", row["row_id"])
        line = {
            "row_id": row["row_id"], "invoice_id": row["invoice_id"], "vendor_id": vendor_id,
            "sku": row["sku"], "ingredient": product["ingredient"], "unit": product["pack_unit"],
            "quantity": quantity * factor, "unit_price": price / factor, "amount": amount,
            "day": date.fromisoformat(header["delivery_date"]),
            "sources": [refs[("invoice_lines", row["row_id"])], refs[("invoice_headers", row["invoice_id"])],
                        refs[("vendor_catalog", product["catalog_row_id"])], refs[("vendors", vendor_id)]],
        }
        if product["catalog_row_id"] == "CAT-CHICKEN20":
            line["sources"] += [refs[("vendor_catalog", "CAT-CHICKEN")], "GM_OPS_LOG.md#N11"]
        invoice_lines[row["invoice_id"]].append(line)
        if quantity * price != amount:
            anomalies.append({
                "kind": "line_arithmetic_mismatch", "invoice_id": row["invoice_id"], "row_id": row["row_id"],
                "recorded_amount": amount, "quantity_times_price": quantity * price,
                "difference": amount - quantity * price, "sources": line["sources"],
            })
    for invoice_id, header in headers.items():
        if invoice_id not in invoice_lines:
            raise ValueError(f"{invoice_id}: invoice has no lines")
        expected = (sum((line["amount"] for line in invoice_lines[invoice_id]), Decimal(0)) +
                    Decimal(header["tax"]) + Decimal(header["delivery_fee"]))
        if Decimal(header["invoice_total"]) != expected:
            anomalies.append({
                "kind": "invoice_total_mismatch", "invoice_id": invoice_id,
                "header_total": Decimal(header["invoice_total"]), "lines_plus_tax_and_fee": expected,
                "difference": Decimal(header["invoice_total"]) - expected,
                "sources": [refs[("invoice_headers", invoice_id)]] +
                [refs[("invoice_lines", line["row_id"])] for line in invoice_lines[invoice_id]],
            })

    excluded = []
    included = []
    for invoice_id, header in headers.items():
        day = date.fromisoformat(header["delivery_date"])
        reasons = []
        if day > end:
            reasons.append("Delivery date is after the closing count period.")
        if day < start:
            reasons.append("Delivery date is before the opening count period.")
        if header["status"] != "received":
            reasons.append("Source status is not received.")
        if invoice_id == "INV-099":
            reasons.append("The manager's note disputes physical receipt; the office status is not confirmation.")
        group = f"invoice.{invoice_id}"
        evidence_sets[group] = list(dict.fromkeys(reference for line in invoice_lines[invoice_id] for reference in line["sources"]))
        if reasons:
            evidence_sets[group] += ["SITE_PROFILE.md#time-context", "SITE_PROFILE.md#kitchen-conventions"]
            if invoice_id == "INV-099":
                evidence_sets[group].append("GM_OPS_LOG.md#N15")
            excluded.append({
                "invoice_id": invoice_id, "invoice_date": header["invoice_date"],
                "delivery_date": header["delivery_date"], "source_status": header["status"],
                "header_total": Decimal(header["invoice_total"]), "reasons": reasons,
                "items": [{"ingredient": line["ingredient"], "quantity": line["quantity"], "unit": line["unit"]}
                          for line in invoice_lines[invoice_id]],
                "evidence_sets": [group],
                "sources": ["GM_OPS_LOG.md#N15"] if invoice_id == "INV-099" else [],
            })
        else:
            included.extend(invoice_lines[invoice_id])

    purchase_weeks = []
    for week in weeks:
        selected = [line for line in included if week["start"] <= line["day"].isoformat() <= week["end"]]
        invoice_ids = list(dict.fromkeys(line["invoice_id"] for line in selected))
        line_total = sum((line["amount"] for line in selected), Decimal(0))
        header_total = sum((Decimal(headers[invoice_id]["invoice_total"]) for invoice_id in invoice_ids), Decimal(0))
        ingredient_totals = {}
        vendor_totals = {}
        for line in selected:
            for key, groups in ((line["ingredient"], ingredient_totals), (line["vendor_id"], vendor_totals)):
                if key not in groups:
                    groups[key] = {"amount": Decimal(0), "evidence_sets": []}
                groups[key]["amount"] += line["amount"]
                group = f"invoice.{line['invoice_id']}"
                if group not in groups[key]["evidence_sets"]:
                    groups[key]["evidence_sets"].append(group)
            ingredient_totals[line["ingredient"]].setdefault("quantity", Decimal(0))
            ingredient_totals[line["ingredient"]]["quantity"] += line["quantity"]
            ingredient_totals[line["ingredient"]]["unit"] = line["unit"]
        for kind, totals in (("ingredient", ingredient_totals), ("vendor", vendor_totals)):
            for key, total in totals.items():
                group = f"purchasing.{week['id']}.{kind}.{key}"
                evidence_sets[group] = list(dict.fromkeys(
                    reference for input_group in total["evidence_sets"] for reference in evidence_sets[input_group]
                ))
                total["evidence_sets"] = [group]
        group = f"purchasing.{week['id']}"
        input_groups = [f"invoice.{invoice_id}" for invoice_id in invoice_ids] + week["evidence_sets"]
        evidence_sets[group] = list(dict.fromkeys(
            reference for input_group in input_groups for reference in evidence_sets[input_group]
        ))
        purchase_weeks.append({
            "id": week["id"], "start": week["start"], "end": week["end"],
            "line_purchases": line_total, "header_purchases": header_total,
            "line_purchases_net_sales_percent": _percent(line_total, week["net_sales"]),
            "header_purchases_net_sales_percent": _percent(header_total, week["net_sales"]),
            "ingredients": ingredient_totals, "vendors": vendor_totals,
            "evidence_sets": [group],
        })
    purchase_changes = []
    for previous, current in zip(purchase_weeks, purchase_weeks[1:]):
        ingredient_changes = {}
        for ingredient in sorted(previous["ingredients"].keys() | current["ingredients"].keys()):
            old = previous["ingredients"].get(ingredient, {"amount": Decimal(0), "quantity": Decimal(0), "evidence_sets": []})
            new = current["ingredients"].get(ingredient, {"amount": Decimal(0), "quantity": Decimal(0), "evidence_sets": []})
            group = f"purchasing.change.{previous['id']}.{current['id']}.ingredient.{ingredient}"
            evidence_sets[group] = list(dict.fromkeys(
                reference for input_group in old["evidence_sets"] + new["evidence_sets"]
                for reference in evidence_sets[input_group]
            ))
            ingredient_changes[ingredient] = {
                "amount": _change(new["amount"], old["amount"]),
                "quantity": _change(new["quantity"], old["quantity"]),
                "unit": opening[ingredient]["unit"], "evidence_sets": [group],
            }
        group = f"purchasing.change.{previous['id']}.{current['id']}"
        evidence_sets[group] = list(dict.fromkeys(
            reference for input_group in previous["evidence_sets"] + current["evidence_sets"]
            for reference in evidence_sets[input_group]
        ))
        purchase_changes.append({
            "from": previous["id"], "to": current["id"],
            "line_purchases": _change(current["line_purchases"], previous["line_purchases"]),
            "ratio_change_percentage_points": current["line_purchases_net_sales_percent"] - previous["line_purchases_net_sales_percent"]
            if current["line_purchases_net_sales_percent"] is not None and previous["line_purchases_net_sales_percent"] is not None else None,
            "ingredients": ingredient_changes,
            "evidence_sets": [group],
        })
    prices = {}
    for ingredient in opening:
        selected = sorted((line for line in included if line["ingredient"] == ingredient), key=lambda line: (line["day"], line["row_id"]))
        observations = []
        for line in selected:
            observations.append({
                "date": line["day"].isoformat(), "sku": line["sku"], "vendor_id": line["vendor_id"],
                "normalized_quantity": line["quantity"], "unit_price": line["unit_price"],
                "sources": line["sources"],
            })
        group = f"purchasing.prices.{ingredient}"
        evidence_sets[group] = list(dict.fromkeys(
            reference for line in selected for reference in line["sources"]
        ))
        prices[ingredient] = {
            "unit": opening[ingredient]["unit"], "observations": observations,
            "first_to_last_change": _change(selected[-1]["unit_price"], selected[0]["unit_price"]) if selected else None,
            "evidence_sets": [group],
        }
    by_sku = defaultdict(list)
    for line in included:
        by_sku[(line["vendor_id"], line["sku"])].append(line)
    for selected in by_sku.values():
        typical = median(line["unit_price"] for line in selected)
        for line in selected:
            if line["unit_price"] != typical:
                anomalies.append({
                    "kind": "price_differs_from_same_sku_median", "invoice_id": line["invoice_id"],
                    "row_id": line["row_id"], "ingredient": line["ingredient"], "unit": line["unit"],
                    "unit_price": line["unit_price"], "same_sku_median_unit_price": typical,
                    "multiple": line["unit_price"] / typical if typical else None,
                    "difference_from_median_amount": (line["unit_price"] - typical) * line["quantity"],
                    "interpretation": "A comparison to recorded same-vendor SKU prices; not a confirmed error or refund.",
                    "sources": list(dict.fromkeys(reference for other in selected for reference in other["sources"])),
                })
    for anomaly in anomalies:
        group = f"purchasing.anomaly.{anomaly['kind']}.{anomaly.get('row_id', anomaly['invoice_id'])}"
        evidence_sets[group] = anomaly["sources"].copy()
        anomaly["evidence_sets"] = [group]

    scopes = {
        "whole_period": sales_rows,
        "full_week": [row for row in sales_rows if row["week_id"] == weeks[-1]["id"]],
        "weekend": [row for row in sales_rows if row["week_id"] == weeks[-1]["id"] and row["day"].weekday() >= 5],
    }
    usage = {}
    missing = {}
    for scope, selected in scopes.items():
        totals = {ingredient: Decimal(0) for ingredient in opening}
        ingredient_refs = {ingredient: [] for ingredient in opening}
        unmodeled = {}
        for sale in selected:
            active = {}
            for recipe in recipes[sale["pos_item_id"]]:
                if recipe["effective"] <= sale["day"]:
                    old = active.get(recipe["ingredient_name"])
                    if old is None or old["effective"] < recipe["effective"]:
                        active[recipe["ingredient_name"]] = recipe
            if not active:
                item_id = sale["pos_item_id"]
                if item_id not in unmodeled:
                    unmodeled[item_id] = {
                        "name": menu[item_id]["name"], "quantity_sold": Decimal(0), "net_sales": Decimal(0),
                        "sources": [refs[("menu", item_id)]] +
                        [refs[("recipes", recipe["row_id"])] for recipe in recipes[item_id]],
                    }
                    if item_id == "M99":
                        unmodeled[item_id]["sources"].append("GM_OPS_LOG.md#N10")
                unmodeled[item_id]["quantity_sold"] += sale["values"]["quantity_sold"]
                unmodeled[item_id]["net_sales"] += sale["values"]["net_sales"]
                unmodeled[item_id]["sources"].append(refs[("pos_daily", sale["row_id"])])
            for ingredient, recipe in active.items():
                totals[ingredient] += sale["values"]["quantity_sold"] * recipe["per_portion"]
                ingredient_refs[ingredient] += [refs[("pos_daily", sale["row_id"])], refs[("recipes", recipe["row_id"])]]
        for ingredient, references in ingredient_refs.items():
            group = f"usage.{scope}.{ingredient}"
            evidence_sets[group] = list(dict.fromkeys(references))
        for item_id, item in unmodeled.items():
            group = f"missing_recipes.{scope}.{item_id}"
            evidence_sets[group] = item["sources"].copy()
            item["evidence_sets"] = [group]
        usage[scope] = totals
        missing[scope] = unmodeled

    consumption = {}
    scenarios = []
    for ingredient in opening:
        selected = [line for line in included if line["ingredient"] == ingredient]
        received = sum((line["quantity"] for line in selected), Decimal(0))
        inferred = opening[ingredient]["value"] + received - closing[ingredient]["value"]
        count_sources = [refs[("inventory_counts", opening[ingredient]["row_id"])],
                         refs[("inventory_counts", closing[ingredient]["row_id"])]]
        group = f"consumption.whole_period.{ingredient}"
        evidence_sets[group] = list(dict.fromkeys(
            count_sources + [reference for line in selected for reference in line["sources"]] +
            evidence_sets[f"usage.whole_period.{ingredient}"]
        ))
        consumption[ingredient] = {
            "unit": opening[ingredient]["unit"], "opening": opening[ingredient]["value"],
            "period_receipts_without_known_dispute": received, "closing": closing[ingredient]["value"],
            "inferred_inventory_outflow": inferred, "known_recipe_usage": usage["whole_period"][ingredient],
            "unexplained_difference": inferred - usage["whole_period"][ingredient],
            "sources": count_sources,
            "evidence_sets": [group],
        }
    for scope in ("full_week", "weekend"):
        scenario_ingredients = {}
        for ingredient in opening:
            demand = usage[scope][ingredient]
            stock = closing[ingredient]["value"]
            gap = max(demand - stock, Decimal(0))
            options = [product for product in catalog.values() if product["ingredient"] == ingredient]
            bought = sorted((line for line in included if line["ingredient"] == ingredient),
                            key=lambda line: (line["day"], line["row_id"]))
            product = catalog[(bought[-1]["vendor_id"], bought[-1]["sku"])] if bought else options[0]
            stock_and_pack_sources = [
                refs[("inventory_counts", closing[ingredient]["row_id"])],
                refs[("vendor_catalog", product["catalog_row_id"])],
            ]
            group = f"restocking.{scope}.{ingredient}"
            evidence_sets[group] = list(dict.fromkeys(
                stock_and_pack_sources + evidence_sets[f"usage.{scope}.{ingredient}"] +
                (bought[-1]["sources"] if bought else []) + ["SITE_PROFILE.md#time-context"]
            ))
            scenario_ingredients[ingredient] = {
                "unit": opening[ingredient]["unit"], "known_recipe_demand": demand,
                "closing_count_quantity": stock, "shortfall_against_closing_count": gap,
                "vendor_id": product["vendor_id"], "sku": product["sku"],
                "pack_quantity": product["pack_value"], "order_unit": product["order_unit"],
                "minimum_whole_packs_for_known_gap": (gap / product["pack_value"]).to_integral_value(rounding=ROUND_CEILING),
                "sources": stock_and_pack_sources,
                "evidence_sets": [group],
            }
        scenarios.append({
            "id": scope,
            "baseline_start": (end - timedelta(days=6 if scope == "full_week" else 1)).isoformat(),
            "baseline_end": end.isoformat(),
            "scenario_start": (end + timedelta(days=1 if scope == "full_week" else 6)).isoformat(),
            "scenario_end": (end + timedelta(days=7)).isoformat(),
            "ingredients": scenario_ingredients, "unmodeled_menu_sales": missing[scope],
        })

    breakfast_date = datetime.fromisoformat(notes["N14"]["recorded_at"]).date()
    breakfast_sales = [refs[("pos_daily", row["row_id"])] for row in sales_rows
                       if row["day"] == breakfast_date and row["pos_item_id"] == "M05"]
    facts = {
        "snapshot": {
            "business_now": now.isoformat(), "timezone": match[4],
            "next_saturday": (now.date() + timedelta(days=(5 - now.weekday()) % 7 or 7)).isoformat(),
            "counted_at": closing_time.isoformat(), "period_start": start.isoformat(), "period_end": end.isoformat(),
            "inventory_status": "Last closing physical count, not a live inventory balance.",
            "sources": ["SITE_PROFILE.md#time-context", "SITE_PROFILE.md#kitchen-conventions"],
        },
        "sales": {
            "basis": "Net sales exclude tax and tips. Quantities are fulfilled menu portions, not customers.",
            "weeks": weeks, "changes": changes, "planned_availability_conflicts": availability_conflicts,
        },
        "purchasing": {
            "basis": "Recorded invoice line purchases / net sales, assigned by delivery_date; excludes future and disputed receipts. Not COGS or gross margin.",
            "weeks": purchase_weeks, "changes": purchase_changes, "price_history": prices,
            "anomalies": anomalies, "excluded_invoices": excluded,
            "limitations": "No opening inventory valuation or full cost ledger. Header totals are shown separately; amounts are not silently corrected.",
            "sources": ["SITE_PROFILE.md#kitchen-conventions"],
        },
        "consumption": {
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "formula": "opening count + receipts without known dispute - closing count - known recipe usage = unexplained difference",
            "ingredients": consumption, "missing_recipes": missing["whole_period"],
            "limitations": [
                "Only beginning and ending counts exist; no weekly or daily actual usage or waste can be derived.",
                "Unexplained differences are not measured waste. Specials have no recipe and reported chicken/pepper waste was unweighed.",
                "The records do not establish a causal link to Ivo or any employee; a zero difference does not prove zero waste.",
                "Received is an office-entered status, not loading-door proof; included receipts have no known contradiction in this packet.",
            ],
            "sources": ["SITE_PROFILE.md#kitchen-conventions", "GM_OPS_LOG.md#N05", "GM_OPS_LOG.md#N06", "GM_OPS_LOG.md#N10"],
        },
        "restocking": {
            "basis": "Scenario only: repeat the prior week's full-week or weekend menu quantities using known recipes.",
            "scenarios": scenarios, "unconfirmed_receipts": excluded,
            "limitations": [
                "Full-week and weekend scenarios are alternatives, not additive.",
                "Weekend gaps compare weekend demand to the unchanged closing count; they do not include intervening weekday consumption or confirmed replenishment.",
                "No growth factor, safety stock or unrecorded special ingredients is invented. Whole packs are a scenario minimum, not an order recommendation.",
                "Historical purchases are already reflected in closing stock and are not added again.",
                "Supply availability, exact arrival time and demand from the neighborhood market are unknown.",
            ],
            "sources": ["SITE_PROFILE.md#kitchen-conventions", "GM_OPS_LOG.md#N15"],
        },
        "operational_context": {
            "notes": list(notes.values()),
            "breakfast_correction": {
                "applies_on": breakfast_date.isoformat(), "status": "Breakfast restored; previous cancellation explicitly corrected.",
                "scope": "This correction concerns that day's service, not an open-ended cancellation or guarantee of future service.",
                "sources": ["GM_OPS_LOG.md#N13", "GM_OPS_LOG.md#N14"] + breakfast_sales,
            },
            "refrigeration": {
                "status": "A door seal was adjusted and one closing reading was normal; no current temperature is supplied.",
                "sources": ["GM_OPS_LOG.md#N06", "GM_OPS_LOG.md#N07"],
            },
            "sku_mapping": {
                "ingredient": chicken_name, "catalog_rows": ["CAT-CHICKEN", "CAT-CHICKEN20"],
                "sources": [refs[("vendor_catalog", "CAT-CHICKEN")], refs[("vendor_catalog", "CAT-CHICKEN20")], "GM_OPS_LOG.md#N11"],
            },
        },
        "evidence_sets": evidence_sets,
    }
    for references in evidence_sets.values():
        for reference in references:
            if reference not in sources:
                raise ValueError(f"Unknown evidence reference: {reference}")
    referenced_groups = set()
    pending = [facts]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "sources":
                    for reference in item:
                        if reference not in sources:
                            raise ValueError(f"Unknown evidence reference: {reference}")
                elif key == "evidence_sets":
                    if isinstance(item, list):
                        for group in item:
                            if group not in evidence_sets:
                                raise ValueError(f"Unknown evidence set: {group}")
                        referenced_groups.update(item)
                else:
                    pending.append(item)
        elif isinstance(value, list):
            pending.extend(value)
    facts["evidence_sets"] = {
        group: references for group, references in evidence_sets.items() if group in referenced_groups
    }
    return _json_values(facts), sources
