# Spec quality checklist — kernel/005-finding-provenance

| # | Check | Verdict |
|---|---|---|
| 1 | Every requirement is testable | PASS — each FR maps to at least one scenario or an integrity property |
| 2 | No unresolved placeholders | PASS — D-A/D-B/D-C closed with Repo B; B-1/B-2 resolved in-spec |
| 3 | Vague adjectives avoided | PASS — "grounded" is defined by exclusion (FR-004) rather than left to the reader |
| 4 | Constitution conflicts surfaced, not diluted | PASS — FR-004 narrows a consumer request rather than accommodating it; D005-4 declines to relax `_enforce_status_rules` |
| 5 | Out-of-scope stated | PASS — pack/005, `write_poc`, `PackContext.memory`, retro-marking, unifying `operation_id` |
| 6 | Consumer contract identified | PASS — `contracts/finding-provenance.md`, owed on the pin |
| 7 | Known limits recorded rather than closed by inference | PASS — capacity (D005-8), the two-`operation_id` naming hazard (D005-5), legacy = unknown (D005-7) |
| 8 | Amendment debris cleared | PASS — analyze pass 2026-08-22 found nine issues across the three negotiation rounds; all applied. The load-bearing one was FR-010 vs `data-model.md`: an intermediate draft left the provenance fields visible to the model, which would have opened a manipulation surface while closing an evidence gap. Resolved in FR-010's favour. |
