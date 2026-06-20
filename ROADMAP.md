# AIP_Aristotle Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-20
# Process: Update this document after each significant build session or architectural decision.
# Release: 0.1.0-alpha (Phase A + B + B.5 + Phase D backend complete)

---

## How to Read This Document

Status indicators:
- ✅ COMPLETE — built, tested, in use
- ⏳ IN PROGRESS — actively being built
- 🔲 PLANNED — decided, not yet started
- 💡 PROPOSED — under consideration, not yet decided
- ❌ DEFERRED — decided to defer, reason noted

Architecture decisions are recorded in `docs/decisions/`.

---

## Current State (verified, not reconstructed)

**Test count:** 124 passed, 0 warnings (last verified run 2026-06-20).

**What is built and passing:**

| Feature | Status | Commit(s) | Notes |
|---------|--------|-----------|-------|
| Phase A — Tutoring loop (TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE) | ✅ | 6dfcb5d+ | CLI, API, GUI learning view, sample concepts. |
| Phase B — Teacher dashboard | ✅ | — | GET /aristotle/dashboard, /dashboard GUI, nav registration. |
| Phase B.5 — 9 pedagogical upgrades | ✅ | 6dfcb5d..d20fd3a | PREDICT, HINT ladder, error diagnosis, faded examples, interleaving, transfer questions, misconception log, mastery_probability, cold-start check. |
| Phase D backend — Onboarding | ✅ | fc7c89d..a72e3db | M004 schema, INTAKE actor, PLACER actor, plan executor bridge, MENTOR pattern recognition. |
| ActorResult.data migration | ✅ | — | All actors use data= (error-as-payload eliminated — ARISTOTLE-DEBT-011 resolved). |

**Phase B.5 deliverable map (all ✅):**

| # | Deliverable | Commit |
|---|-------------|--------|
| 1 | PREDICT step | 6dfcb5d |
| 2 | HINT ladder | e75906e |
| 3 | Error diagnosis | 95d00d2+a6cd987 |
| 4 | Faded worked examples | b803ef9 |
| 5 | Session interleaving | 2079f0c |
| 6 | Transfer questions | 0352708 |
| 7 | Misconception log wiring | 1be28f7 |
| 8 | Extended mastery model | d20fd3a |
| 9 | Cold-start check | d20fd3a |

**Phase D backend deliverable map (all ✅):**

| # | Deliverable | Commit |
|---|-------------|--------|
| 1 | M004 schema | fc7c89d |
| 2 | INTAKE actor + API routes | 5128caa |
| 6 | PLACER actor + placement calibration | 2322f0f |
| 8 | Plan executor bridge (long-arc) | 228d440 |
| — | MENTOR pattern recognition (ADR-002 §7) | a72e3db |

---

## Immediate Next — GUI Phase (no blockers)

Ordered by dependency. Items 1–4 are Brain-side (AIP_Brain repo);
items 5–11 are ARISTOTLE-side.

  [Brain] 1. ADR-014 A1: Extension UI sidebar visibility (ui.timer + ui.refreshable)
  [Brain] 2. Three-panel shell restructure (left/right drawers + main chat)
  [Brain] 3. + menu (Upload PDF, Upload Image, Voice, Settings)
  [Brain] 4. Extension mode shift (accent color, mode label)
  [Arst]  5. ARISTOTLE stats page (/aristotle/stats)
  [Arst]  6. ARISTOTLE learning map (/aristotle/map)
  [Arst]  7. ARISTOTLE settings page (/aristotle/settings)
  [Arst]  8. Right panel: mastery state + concept progress
  [Arst]  9. OCR path via pytesseract (pypdf fix already done — not blocked)
  [Arst] 10. Voice mode toggle (Web Speech API via Brain core + menu)
  [Arst] 11. Teacher dashboard (Komal's interface — revised per UI_CONVENTIONS.md)

**Intake via Brain chat:** The INTAKE interview runs in the main Brain
chat — NOT a separate /intake page (per UI_CONVENTIONS.md). ARISTOTLE
registers three pages only: /aristotle/stats, /aristotle/map,
/aristotle/settings.

**+ menu:** ARISTOTLE does not register new + menu items. Upload PDF
and Voice mode are Brain core features.

---

## Blocked (do not schedule until unblocked)

- **HERALD Phase C:** blocked on Brain web/feed layer (ADR-014 §3.4 — not started)
- **Web-search material sourcing:** same block as HERALD

---

## Deferred (conscious decisions, not forgotten)

- Self-registration protocol (ADR-014 A1 — deferred until third-party extensions)
- Desktop shell migration (NiceGUI → PyWebView → Tauri)
- MCP transport (stdio/SSE)
- Loom as extension
- CodeForge as extension
- Agent Studio, Company Brain, Federation, Praxis, Chronicle, Astra (not yet specced)
- Third-party extension support
- Multi-tenant / enterprise features
- Intent detection classifier actor (v2 — currently keyword-based)
- mastery_probability driving SM-2 intervals (currently read-only diagnostic)
- Pre-ADR platform features (Extension Corpus Isolation, Actor Prompt Customization)

---

## Pilot Protocol (ADR-001 §10)

1. **Ramesh** — self-tutors a chapter he already knows. **Gate: Phase A ✅**
2. **Sameer** — loads pharmacy/HIS material + HERALD feeds. **Gate: Phase A ✅ + Phase C**
3. **Moses** — physics + AI foundations + HERALD. **Gate: Phase A ✅ + Phase C**
4. **Freedom Generation School** — supervised classroom with Komal's dashboard. **Gate: Phase A ✅ + Phase B ✅ + GUI Phase**

---

## Version History

| Date | Change | Author |
|------|--------|--------|
| 2026-06-18 | Created roadmap. Seeded with Phase A/B/C from ADR-001 §11. | Super Z (main) |
| 2026-06-19 | ADR-002 Rev 2 committed. Phase A and Phase B marked COMPLETE. Phase B.5 added. Phase D added. Phase C unchanged. | Super Z (main) |
| 2026-06-19 | **Phase B.5 ✅ COMPLETE.** All 9 deliverables shipped. ActorResult.data migration complete. 89 tests, 0 warnings. | Super Z (main) |
| 2026-06-19 | **Phase D backend ✅ COMPLETE.** 5 deliverables shipped: M004, INTAKE, PLACER, plan executor, pattern recognition. 124 tests, 0 warnings. | Super Z (main) |
| 2026-06-20 | Phase D surface layer revised per UI_CONVENTIONS.md: no /intake page — intake via Brain chat. Three registered pages only (stats, map, settings). Roadmap rewritten to reflect current state + GUI phase as immediate next. | Super Z (main) |
| 2026-06-20 | **GUI Phase ✅ COMPLETE.** Stats, map, settings, teacher dashboard, session history, curiosity path (ADR-002 A1), upload expansion, Brain + menu wired, voice mode (Web Speech API). 149 tests, 0 warnings. ADR-001 research annex filed. | Moses + Claude (main) |

---

## Ongoing / Evergreen

- Keep `PLANNED_FEATURES.md` current
- Keep `STATUS.md` current after each build session
- Keep `TECH_DEBT.md` current (file new debt, mark resolved debt)
- Write ADRs for each significant architectural decision
- Log every platform-reach as a Phase 0 protocol gap
