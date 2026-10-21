from datetime import datetime
from decimal import Decimal

import streamlit as st


SOURCE_LABELS = {
    "data/pos_daily.csv": "Sales transactions",
    "data/invoice_headers.csv": "Invoice headers",
    "data/invoice_lines.csv": "Invoice lines",
    "data/inventory_counts.csv": "Inventory counts",
    "data/menu.csv": "Menu",
    "data/recipes.csv": "Recipe cards",
    "data/vendor_catalog.csv": "Supplier packs and units",
    "data/vendors.csv": "Supplier order sheet",
    "GM_OPS_LOG.md": "Manager notebook",
    "source_updates": "Saved supplier messages",
}


SOURCE_COLUMN_ORDER = {
    "data/pos_daily.csv": ["item_name", "business_date", "quantity_sold", "net_sales", "discounts", "gross_sales", "pos_item_id", "row_id"],
    "data/invoice_headers.csv": ["vendor_name", "delivery_date", "invoice_date", "status", "invoice_total", "tax", "delivery_fee", "currency", "vendor_account", "invoice_id"],
    "data/invoice_lines.csv": ["item_description", "quantity", "unit", "unit_price", "line_total", "sku", "invoice_id", "row_id"],
    "data/inventory_counts.csv": ["ingredient_name", "quantity", "unit", "counted_at", "counted_by", "comment", "row_id"],
    "data/menu.csv": ["name", "menu_price", "available_from", "service", "pos_item_id"],
    "data/recipes.csv": ["ingredient_name", "quantity", "unit", "yield_portions", "effective_from", "pos_item_id", "row_id"],
    "data/vendor_catalog.csv": ["description", "pack_quantity", "pack_unit", "order_unit", "sku", "vendor_id", "catalog_row_id"],
    "data/vendors.csv": ["vendor_name", "delivery_days", "last_reviewed", "account_number", "vendor_id"],
}

SOURCE_COLUMN_LABELS = {
    "row_id": "Row ID", "sku": "SKU", "catalog_row_id": "Catalog ID", "pos_item_id": "Menu ID",
    "invoice_id": "Invoice ID", "vendor_id": "Vendor ID", "item_name": "Menu item", "name": "Menu item",
    "ingredient_name": "Ingredient", "description": "Product", "item_description": "Product",
    "vendor_name": "Supplier", "business_date": "Sale date", "quantity_sold": "Portions sold",
    "net_sales": "Net sales (USD)", "gross_sales": "Gross sales (USD)", "discounts": "Discounts (USD)",
    "unit_price": "Unit price (USD)", "line_total": "Line total (USD)", "invoice_total": "Invoice total (USD)",
    "tax": "Tax (USD)", "delivery_fee": "Delivery fee (USD)", "menu_price": "Menu price (USD)",
    "counted_at": "Counted at (local time)", "counted_by": "Counted by", "comment": "Count note",
    "quantity": "Quantity", "available_from": "Planned menu start", "effective_from": "Recipe effective from",
    "yield_portions": "Recipe yield (portions)", "pack_quantity": "Pack quantity", "pack_unit": "Pack unit",
    "order_unit": "Order unit", "service": "Planned service", "status": "Entered receipt status",
    "invoice_date": "Invoice issue date", "delivery_date": "Delivery date",
}


def _table(rows):
    st.dataframe(
        [{key: "—" if value is None else value for key, value in row.items()} for row in rows],
        hide_index=True, width="stretch",
    )


def _money(value):
    return f"${Decimal(value):,.2f}"


def _missing_sales(items):
    if items:
        st.markdown("**Sales without an effective recipe**")
        _table([
            {"Menu item": item["name"], "Portions": item["quantity_sold"],
             "Net sales (USD)": item["net_sales"], "Menu ID": item_id}
            for item_id, item in items.items()
        ])


def render_sources(refs, sources):
    grouped = {}
    for ref in dict.fromkeys(refs):
        record = sources[ref]
        if record["file"] not in SOURCE_LABELS:
            continue
        if record["file"] == "GM_OPS_LOG.md" and not record["id"].startswith("N"):
            continue
        grouped.setdefault(record["file"], []).append(record)
    for filename, records in grouped.items():
        st.markdown(f"**{SOURCE_LABELS[filename]}**")
        rows = [record["content"]
                for record in records if isinstance(record["content"], dict)]
        if rows:
            st.caption(f"{len(rows)} {'record' if len(rows) == 1 else 'records'}")
            fields = list(dict.fromkeys(field for row in rows for field in row))
            order = SOURCE_COLUMN_ORDER[filename] + [field for field in fields if field not in SOURCE_COLUMN_ORDER[filename]]
            columns = {
                field: st.column_config.TextColumn(
                    SOURCE_COLUMN_LABELS.get(field, field.replace("_", " ").capitalize()),
                    width="small" if field.endswith("_id") or field == "sku" else None,
                )
                for field in fields
            }
            st.dataframe(rows, hide_index=True, width="stretch", column_config=columns, column_order=order)
        for record in records:
            if isinstance(record["content"], str):
                content = record["content"]
                if filename == "GM_OPS_LOG.md":
                    heading, content = content.split("\n", 1)
                    _, recorded_at, author = heading.split(" | ")
                    recorded = datetime.fromisoformat(recorded_at)
                    st.caption(f"{recorded:%B %d, %Y · %I:%M %p} · {author}")
                else:
                    recorded = datetime.fromisoformat(record["learned_at"])
                    st.caption(f"Recorded {recorded:%B %d, %Y · %I:%M %p}")
                st.markdown(content.strip().replace("$", r"\$"))


def render_analysis(facts, sources):
    section = st.selectbox(
        "Analysis", ["Sales", "Purchasing", "Stock reconciliation", "Restocking", "Kitchen notes"],
        key="analysis_section",
    )
    if section == "Sales":
        sales = facts["sales"]
        st.caption(sales["basis"])
        latest = sales["weeks"][-1]
        change = sales["changes"][-1]
        columns = st.columns(3)
        columns[0].metric("Last week's net sales", _money(latest["net_sales"]))
        columns[1].metric("Last week's portions", latest["quantity_sold"])
        columns[2].metric("Change from previous week", _money(change["net_sales"]["absolute"]))
        st.markdown("**Week by week**")
        _table([
            {"Week": week["id"].replace("_", " ").title(), "From": week["start"], "Through": week["end"],
             "Net sales (USD)": week["net_sales"], "Portions": week["quantity_sold"],
             "Gross sales (USD)": week["gross_sales"], "Discounts (USD)": week["discounts"]}
            for week in sales["weeks"]
        ])
        st.markdown("**Changes between weeks**")
        _table([
            {"From": row["from"].replace("_", " ").title(), "To": row["to"].replace("_", " ").title(),
             "Net sales change (USD)": row["net_sales"]["absolute"],
             "Net sales change (%)": row["net_sales"]["percent"],
             "Portions change": row["quantity_sold"]["absolute"],
             "Portions change (%)": row["quantity_sold"]["percent"]}
            for row in sales["changes"]
        ])
        index = st.selectbox("Menu detail for", range(len(sales["weeks"])), index=len(sales["weeks"]) - 1,
                             format_func=lambda i: f"{sales['weeks'][i]['start']} – {sales['weeks'][i]['end']}",
                             key="sales_week")
        _table([
            {"Menu item": item["name"], "Portions": item["quantity_sold"],
             "Net sales (USD)": item["net_sales"], "Share of net sales (%)": item["net_sales_share_percent"],
             "Gross sales (USD)": item["gross_sales"], "Discounts (USD)": item["discounts"], "Menu ID": item_id}
            for item_id, item in sales["weeks"][index]["menu"].items()
        ])
        if index:
            st.markdown("**Menu changes from the previous week**")
            _table([
                {"Menu item": item["name"],
                 "Net sales change (USD)": item["net_sales"]["absolute"],
                 "Net sales change (%)": item["net_sales"]["percent"],
                 "Portions change": item["quantity_sold"]["absolute"],
                 "Portions change (%)": item["quantity_sold"]["percent"], "Menu ID": item_id}
                for item_id, item in sales["changes"][index - 1]["menu"].items()
            ])
        st.caption("In change (%) columns, — means there is no nonzero prior value for comparison.")
        if sales["planned_availability_conflicts"]:
            st.warning("Recorded sales precede the planned menu start date. Sales remain included.")
            _table([
                {"Planned start": row["planned_available_from"],
                 "Sale date": row["business_date"], "Portions": row["quantity_sold"],
                 "Menu ID": row["pos_item_id"]}
                for row in sales["planned_availability_conflicts"]
            ])

    elif section == "Purchasing":
        purchasing = facts["purchasing"]
        vendor_names = {vendor["vendor_id"]: vendor["vendor_name"] for vendor in facts["delivery"]}
        st.caption("Purchases by delivery date, excluding future and unconfirmed receipts. Purchase share is not actual food cost or gross margin.")
        st.info("Opening inventory values and a complete cost ledger are missing.")
        st.markdown("**Recorded purchases by week**")
        _table([
            {"From": week["start"], "Through": week["end"], "Line purchases (USD)": week["line_purchases"],
             "Header purchases (USD)": week["header_purchases"],
             "Lines / net sales (%)": week["line_purchases_net_sales_percent"],
             "Headers / net sales (%)": week["header_purchases_net_sales_percent"]}
            for week in purchasing["weeks"]
        ])
        st.markdown("**Changes between weeks**")
        _table([
            {"From": row["from"].replace("_", " ").title(), "To": row["to"].replace("_", " ").title(),
             "Line purchases change (USD)": row["line_purchases"]["absolute"],
             "Line purchases change (%)": row["line_purchases"]["percent"],
             "Purchase share change (percentage points)": row["ratio_change_percentage_points"]}
            for row in purchasing["changes"]
        ])
        index = st.selectbox("Purchase detail for", range(len(purchasing["weeks"])), index=len(purchasing["weeks"]) - 1,
                             format_func=lambda i: f"{purchasing['weeks'][i]['start']} – {purchasing['weeks'][i]['end']}",
                             key="purchasing_week")
        week = purchasing["weeks"][index]
        _table([
            {"Ingredient": name, "Quantity": row["quantity"], "Unit": row["unit"], "Purchases (USD)": row["amount"]}
            for name, row in week["ingredients"].items()
        ])
        st.markdown("**Supplier totals**")
        _table([{"Supplier": vendor_names[vendor_id], "Purchases (USD)": row["amount"], "Vendor ID": vendor_id}
                for vendor_id, row in week["vendors"].items()])
        if index:
            st.markdown("**Ingredient changes from the previous week**")
            _table([
                {"Ingredient": name, "Quantity change": row["quantity"]["absolute"], "Unit": row["unit"],
                 "Quantity change (%)": row["quantity"]["percent"], "Spend change (USD)": row["amount"]["absolute"],
                 "Spend change (%)": row["amount"]["percent"]}
                for name, row in purchasing["changes"][index - 1]["ingredients"].items()
            ])
        st.caption("In change (%) columns, — means there is no nonzero prior value for comparison.")
        st.markdown("**Unit price history**")
        name = st.selectbox("Ingredient", list(purchasing["price_history"]), key="price_ingredient")
        price = purchasing["price_history"][name]
        if price["first_to_last_change"] is not None:
            change = price["first_to_last_change"]
            percentage = f"{change['percent']}%" if change["percent"] is not None else "percentage unavailable"
            st.caption(f"First to last recorded price change: {change['absolute']} USD/{price['unit']}; {percentage}.")
        _table([
            {"Delivery date": row["date"], "Supplier": vendor_names[row["vendor_id"]],
             f"Price (USD/{price['unit']})": row["unit_price"],
             "Quantity": row["normalized_quantity"], "Unit": price["unit"], "SKU": row["sku"],
             "Vendor ID": row["vendor_id"], "Invoice line": row["sources"][0].split("#")[1]}
            for row in price["observations"]
        ])
        st.markdown("**Invoice exceptions**")
        exceptions = []
        for row in purchasing["anomalies"]:
            if row["kind"] == "invoice_total_mismatch":
                detail = {"Issue": "Header / lines mismatch", "Recorded": _money(row["header_total"]),
                          "Comparison": _money(row["lines_plus_tax_and_fee"]) + " lines + tax + fee",
                          "Difference (USD)": row["difference"], "Status": "Needs reconciliation"}
            elif row["kind"] == "line_arithmetic_mismatch":
                detail = {"Issue": "Line arithmetic mismatch", "Recorded": _money(row["recorded_amount"]),
                          "Comparison": _money(row["quantity_times_price"]) + " quantity × price",
                          "Difference (USD)": row["difference"], "Status": "Needs reconciliation"}
            else:
                detail = {"Issue": "Same-SKU price comparison", "Recorded": f"{row['unit_price']} USD/{row['unit']}",
                          "Comparison": f"{row['same_sku_median_unit_price']} USD/{row['unit']} median",
                          "Difference (USD)": row["difference_from_median_amount"],
                          "Multiple": row["multiple"], "Status": row["interpretation"]}
            exceptions.append({"Invoice": row["invoice_id"], "Line": row.get("row_id", ""), **detail,
                               "Multiple": detail.get("Multiple")})
        _table(exceptions)
        st.caption("Multiple is shown only for price comparisons with a nonzero comparison price.")
        st.markdown("**Receipts excluded from the counted period**")
        _table([
            {"Invoice": row["invoice_id"], "Invoice date": row["invoice_date"], "Delivery date": row["delivery_date"],
             "Source status": row["source_status"], "Invoice total (USD)": row["header_total"],
             "Ingredient": item["ingredient"], "Quantity": item["quantity"], "Unit": item["unit"],
             "Why excluded": " ".join(row["reasons"])}
            for row in purchasing["excluded_invoices"] for item in row["items"]
        ])

    elif section == "Stock reconciliation":
        consumption = facts["consumption"]
        st.caption(f"{consumption['period_start']} – {consumption['period_end']} · complete count interval")
        st.info("Stock differences need reconciliation; they are not measured waste.")
        _table([
            {"Ingredient": name, "Unit": row["unit"], "Opening count": row["opening"],
             "Included receipts": row["period_receipts_without_known_dispute"], "Closing count": row["closing"],
             "Inferred outflow": row["inferred_inventory_outflow"], "Known recipe usage": row["known_recipe_usage"],
             "Unexplained difference": row["unexplained_difference"]}
            for name, row in consumption["ingredients"].items()
        ])
        st.caption(consumption["formula"])
        _missing_sales(consumption["missing_recipes"])
        st.caption("Only opening and closing counts are available; daily and weekly usage cannot be determined.")
        st.caption("Specials are missing recipes and discarded food was not weighed. Check receiving, recipe and waste records to explain the differences.")

    elif section == "Restocking":
        restocking = facts["restocking"]
        vendor_names = {vendor["vendor_id"]: vendor["vendor_name"] for vendor in facts["delivery"]}
        st.caption("Based on last week's menu sales and recorded recipes.")
        index = st.radio("Planning period", range(len(restocking["scenarios"])), horizontal=True,
                         format_func=lambda i: {"full_week": "Full week", "weekend": "Weekend only"}[
                             restocking["scenarios"][i]["id"]], key="restocking_scenario")
        scenario = restocking["scenarios"][index]
        st.caption(f"Plan for {scenario['scenario_start']} – {scenario['scenario_end']} · "
                   f"Sales from {scenario['baseline_start']} – {scenario['baseline_end']}")
        st.info("These estimates repeat last week's sales. Confirm current stock and incoming deliveries before ordering.")
        _table([
            {"Ingredient": name, "Unit": row["unit"], "Known demand": row["known_recipe_demand"],
             "Closing count": row["closing_count_quantity"], "Shortfall": row["shortfall_against_closing_count"],
             "Minimum whole packs": row["minimum_whole_packs_for_known_gap"],
             "Pack size": f"{row['pack_quantity']} {row['unit']} / {row['order_unit']}",
             "Supplier": vendor_names[row["vendor_id"]], "SKU": row["sku"], "Vendor ID": row["vendor_id"]}
            for name, row in scenario["ingredients"].items()
        ])
        _missing_sales(scenario["unmodeled_menu_sales"])
        st.caption("Weekend estimates do not include intervening weekday consumption. The full-week and weekend plans should not be added together.")
        st.caption("Quantities exclude buffer stock, changes in demand and specials without recipes. Confirm supplier availability and arrival times.")
        for receipt in restocking["unconfirmed_receipts"]:
            st.warning(f"{receipt['invoice_id']} remains excluded: {' '.join(receipt['reasons'])}")

    else:
        context = facts["operational_context"]
        correction = context["breakfast_correction"]
        st.markdown("**Breakfast service**")
        st.write(f"{correction['applies_on']}: {correction['status']}")
        st.markdown("**Refrigeration**")
        st.write(context["refrigeration"]["status"])
        st.markdown("**Chicken case sizes**")
        st.write(context["sku_mapping"]["ingredient"])
        render_sources(context["sku_mapping"]["sources"], sources)
        st.markdown("**Manager notebook**")
        notes = context["notes"]
        index = st.selectbox("Notebook entry", range(len(notes)), index=len(notes) - 1,
                             format_func=lambda i: " · ".join(notes[i]["text"].splitlines()[0].split(" | ")[1:]),
                             key="notebook_entry")
        render_sources(notes[index]["sources"], sources)
