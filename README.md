# Lantern Yard Kitchen — candidate packet

Start with `CANDIDATE_BRIEF.md` and `SITE_PROFILE.md`. All operational records are fictional and were generated for this exercise. No customer data or real supplier prices are included.

**Snapshot: October 5, 2026, 6 a.m. America/New_York.** Three weeks of service: September 14–October 4. The files are the complete supplied packet, not a promise that every operational question has a definitive answer. Treat exports and notes as evidence. You may make assumptions if you label them; you do not need outside data or access to Marble systems.

## File guide

| File | Contents |
| --- | --- |
| `SITE_PROFILE.md` | People, service, tools, time context, and measurement conventions |
| `ABOUT_MARBLE.md` | Product direction and assignment context |
| `GM_OPS_LOG.md` | Fifteen dated shift notes with stable note IDs |
| `data/invoice_headers.csv` | One source record per invoice, including entered receipt status |
| `data/invoice_lines.csv` | Item rows linked by invoice ID |
| `data/pos_daily.csv` | Daily quantities and sales by POS item; days without sales of an item omit that item |
| `data/inventory_counts.csv` | Opening and closing physical counts |
| `data/vendors.csv` | Order sheet last reviewed September 14 |
| `data/vendor_catalog.csv` | Product descriptions and pack quantities from supplier catalogs |
| `data/menu.csv` | POS menu buttons and listed prices |
| `data/recipes.csv` | Available recipe cards, with effective dates |

## Reading the exports

- Money is USD; decimal points use `.`. POS net sales are gross sales less discounts, excluding sales tax and tips. Quantities are fulfilled item portions; the supplied period has no POS refunds.
- Invoice unit price is per stated invoice unit; line total is the amount recorded in the source. Header total is the amount entered for the invoice, inclusive of the explicit tax and delivery fee columns. No other adjustment fields exist in this export. Dates are local calendar dates.
- A catalog pack quantity is the number of `pack_unit` units in one case. A line billed in pounds is already priced per pound. SKUs belong to vendors. Supplier descriptions and kitchen ingredient names come from different systems.
- Inventory quantities use the unit on each row. Recipe quantities use the unit on each recipe row and `yield_portions` states the number of portions that row describes. Recipe `effective_from` is the stated start of that card's applicability.
- `vendors.csv` is a dated order sheet, not a live supplier API. Menu availability describes the planned menu; notes and transactions may provide additional context.
- Cite filenames and row/note IDs (invoice IDs for headers, POS item IDs for menu entries, vendor IDs for the vendor sheet). You are free to organize or transform the data internally.

Keep the assignment within the brief's 2–4 hour timebox. There is no requirement to clean every record or build a production ingestion pipeline.
