# Marble Founding Engineer Take-Home — Build the Operations Brain

## The problem

A fast-casual restaurant runs on invoices, POS exports, inventory counts, and whatever the GM remembers to write down. The GM is sharp but drowning. The owner wants to understand what’s happening without reading spreadsheets.

**Build something that helps them know what matters and what to do next.**

We’re deliberately leaving the product and architecture open. Deciding what deserves attention, what the data actually supports, and what to build in the time available is part of the assignment.

## What you’ll get

A synthetic restaurant data packet containing:

- A short site profile: menu concept, hours, team, and current tools.
- Three weeks of invoices from a few vendors and daily POS sales.
- Inventory counts at the beginning and end of the period.
- Vendor information and a recipe/menu export.
- A short GM operations log.
- A one-page introduction to Marble.

Treat these as operational records: useful, incomplete, and not necessarily consistent. All names, prices, and business details are fictional.

## What to build

**An agent an operator can ask questions of.** For example:

- “What changed last week, and should I care?”
- “Why might food cost be going up?”
- “What should I worry about before Saturday?”

These are starting points, not a required feature checklist. Choose the questions and interactions you think are most valuable. Answers should point back to specific source rows or notes, and distinguish evidence from inference. Sometimes the useful answer is explaining what we don’t know.

**Memory that earns its place.** Decide what the system should remember across questions or runs and how that context helps it. Demonstrate what happens when a fact changes or new information conflicts with something it already knows. The implementation is yours to choose.

**A morning brief.** Surface the three to five things you would put in front of the GM without being asked. Rank them and explain why they deserve attention and what, if anything, the operator should do.

A CLI, notebook, or web app is equally welcome. We care about a working path from messy inputs to useful decisions. Choose a narrow scope and make it convincing.

## What to submit

- **Your repository**, with commit history and straightforward setup instructions. Include dependencies, required credentials, and any expected API costs. A hosted demo is optional.
- **A sample morning brief and a few representative conversations**, including one where the evidence is incomplete or contradictory.
- **A short `ARCHITECTURE.md`** explaining your choices, what you left out, and how memory works: what is retained, how changing facts are represented, and what is forgotten or invalidated. Include roughly half a page on likely production failures and how you would test whether the system is useful and trustworthy.
- **Your AI chat transcripts from the assignment**, including relevant coding-agent sessions. Exports or readable transcripts are fine. Include your prompts and the assistant’s responses, not just a summary. Redact credentials and unrelated personal information, and identify any missing sessions. Briefly highlight one AI suggestion you changed or rejected and how you checked the work.

Use whatever AI tools you normally use—we expect it. We want to understand your judgment while working with them.

## Time and follow-up

Spend **2–4 hours of actual work**, within a 48-hour submission window. Stop at the timebox and tell us what remains unfinished. Any language, stack, or model is welcome.

We’ll follow up with a 30-minute conversation: you’ll demonstrate what works, walk through a decision you made, and discuss how your approach would handle new information.

We’re evaluating founding-engineer judgment: choosing a useful problem, connecting operational context to evidence, making sound technical tradeoffs, and knowing where confidence ends. We’re not scoring framework choices, feature count, or visual polish.

This is a synthetic exercise. We will not use your submission as work product in Marble.
