# ADR-001: Aristotle — Adaptive Tutor Architecture

**Date:** 2026-06-18
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen
**Supersedes:** ADL-ARISTOTLE (2026-06-07)

---

## Context

ARISTOTLE was proposed by Ramesh during a live AIP demo in June 2026. It is
an adaptive tutor — not new infrastructure, but an application layer over
AIP Brain: a pedagogical state machine and purpose-built prompts riding on
the multi-corpus foundation (ADR-008), the actor framework, the graph store,
and the Phase 0 extension contract (ADR-014). It is the cleanest test of
whether that foundation composes into something a non-technical teacher in
Faisalabad can actually use.

The design question: how do you build a tutor that remembers yesterday's
struggle, teaches in the learner's home language, never introduces a concept
whose foundations are unmastered, and surfaces the living field around the
curriculum — all while being a single voice the learner trusts, not a
teaching staff?

The answer is five internal orchestration modes (not personas), a
prerequisite DAG that gates concept introduction, one persistent diagnostic
sentence per student, and a phased build that ships the tutoring loop first
and layers field awareness on later.

## Decision

### 1. The single-voice principle

**Aristotle is the only character the learner ever meets.** Alexander had
Aristotle — one mind that became Socratic when questioning, exacting when
drilling, and the same continuous presence that remembered yesterday's
struggle. Not a teaching staff; one tutor shifting register.

The five roles below are **internal orchestration modes**, not personas the
student encounters. They exist in code for separation of concerns (distinct
prompts, jobs, data) — exactly as Beast/Vigil/Sexton are distinct in core
while the user talks to one interface. Mode *transitions* may be felt as
natural signposting in one voice ("let me check that landed", "let me try
that differently") — that is teaching, not a handoff. The decomposition
surfaces in exactly one place: **the teacher dashboard**, where the operator
benefits from seeing the parts. Single voice forward; full decomposition for
whoever is running it.

### 2. The five modes

| Mode | Role | Notes |
|---|---|---|
| **SOCRATES** | Teach / explain / re-explain | Pulls the passage + alternate framings from the textbook corpus when the first explanation misses. |
| **EXAMINER** | Probe / quiz / evaluate | Generates questions, scores answers, decides mastery. |
| **VIGIL** | Spaced repetition scheduler | **Reused from core.** SM-2; decides what comes due and when. |
| **MENTOR** | Track the long arc | Mastery per concept + the **`struggle_pattern`** field (one persistent AI-written diagnostic sentence per student — the tutor's memory of *who this learner is*; feeds every REMEDIATE prompt). |
| **HERALD** | Field awareness | Source-first news tied to the curriculum; builds the who's-who/what's-what field map (§6). |

### 3. Session experience — "the student's only job is to show up"

The session opens itself. The system already knows where the learner left
off, what's due, and what they never quite grasped — so it begins mid-stride,
like a tutor who has been waiting. No menu, no "what would you like to study."

Internally this is the state machine; the learner only feels rhythm:

```
TEACH ──► PROBE ──► QUIZ ──► EVALUATE ──┬─► (mastered) ─► next concept
  ▲                                      │
  └──────────── REMEDIATE ◄──────────────┘ (struggling → different framing)
```

- **TEACH** (SOCRATES): explain, with an alternate framing on retry.
- **PROBE** (EXAMINER): low-stakes "tell me in your own words."
- **QUIZ** (EXAMINER): a real question.
- **EVALUATE** (EXAMINER + MENTOR): score, update mastery + struggle_pattern.
- **REMEDIATE** (SOCRATES, informed by struggle_pattern): re-teach from a new angle.

Branching is invisible. The student never sees the mode names.

### 4. Knowledge model — concept-aware, not byte-aware

Standard RAG token-chunking is pedagogically wrong. The ingestor produces
**concept-aware chunks** with a prerequisite graph:

```
chunk: { textbook, chapter, topic, subtopic, bloom_target(1-6),
         content_primary, content_alt, content_alt_lang,
         related_chunks[], prerequisite_chunks[], example_chunks[] }
```

The `prerequisite_chunks` form a DAG of knowledge dependencies — **the most
important non-obvious element.** Before selecting the next concept, the
session consults the graph; a student never meets a concept whose
foundations are unmastered. This DAG maps directly onto the **graph store +
bridge-edge machinery** built in ADR-008 — prerequisite links are edges. The
**curriculum map** is generated automatically at ingestion (AI analysis of
the textbook), reviewable and correctable by the teacher.

### 5. Data model on the multi-corpus foundation

| ARISTOTLE need | Multi-corpus mapping |
|---|---|
| Textbook content | a `document`-type **corpus** per subject (concept-chunked) |
| Student progress / mastery / struggle_pattern | per-student `document` corpus or namespace (isolation per learner) |
| Field news (HERALD) | a **recency-aware** corpus; stale items use the **ARCHIVED** state (built in ADR-008) to age out of default retrieval while staying queryable for history + the field map |
| Prerequisite graph + field map | the **graph store** (bridge edges) |
| Alternate explanations | hybrid FTS5 + vector RRF (built) |

The isolation ARISTOTLE always needed for multi-student/multi-subject
deployment **is** the multi-corpus work. ARISTOTLE is the first thing that
genuinely requires corpus-per-project — which is why it is the right first
extension.

### 6. HERALD — field awareness from the source

HERALD turns a textbook tutor into a *field apprenticeship*: the learner
absorbs not just concepts but the living landscape they sit in.

1. **Watch the field at the source.** Per-subject curated feeds — *primary*,
   bypassing the YouTube/aggregator filter. (Sameer: HIMSS, Healthcare IT
   News, the Pakistani layer — PITB, instacare, cloudpital, Dawn health,
   regulators. Physics/AI: arXiv cond-mat/quant-ph/cs.AI/cs.LG directly,
   Quanta, lab blogs, researcher feeds.) Scheduled poll → fetch → embed →
   file (uses the Phase 0 feed poller).
2. **Match news to curriculum.** Incoming items scored against the concept
   graph; when SOCRATES teaches a concept, HERALD surfaces a recent, dated,
   real-world example tied to it. Abstract concept → live anchor.
3. **Build the who's-who / what's-what.** Players (institutions, vendors,
   regulators, labs, researchers, camps) thread into a **field-map graph**
   (graph store). Over months the learner develops the situational sense
   that normally takes years in an industry.
4. **Tier sources by credibility.** Each item tagged by provenance
   (peer-reviewed > preprint > reputable journalism > blog > speculative),
   carried into the lesson. This builds *calibration* alongside knowledge —
   the learner sees where ideas sit relative to the field.

HERALD depends on the Phase 0 web layer (ADR-014 §3.4); the tutoring loop
ships without it and HERALD layers on when feeds land.

### 7. Bilingual

Urdu and English side by side, as a core requirement. A concept can be
taught in English and probed in Urdu, or the whole session can live in the
learner's home language. For Freedom Generation students this is the
difference between the tool meeting them where they are and not.

### 8. Teacher dashboard (Komal)

A read-view into every student's state — leverage, not surveillance. This
is the **one place the actor decomposition is visible**: mastery heatmaps
(MENTOR), what's due (VIGIL), struggle-pattern sentences, avoidance,
readiness, and field surfacing (HERALD). Komal's scarce human time goes
where only a human can go; the tutor absorbs infinite patient repetition.
The relationship and curriculum judgment stay fully human.

### 9. How ARISTOTLE consumes Phase 0

| ARISTOTLE contribution | Phase 0 capability |
|---|---|
| `textbook` / `progress` / `field` corpora + project template | corpus management + multi-corpus (ADR-014 §3.2, ADR-008) |
| SOCRATES, EXAMINER, MENTOR, HERALD actors (VIGIL reused) | actor registration hook (ADR-014 §3.6) |
| Curriculum-aware retrieval | optional custom channel (§ channel registry) |
| Teaching/quiz/remediation pipelines | contributed workflow YAMLs (§ workflow registry) |
| student_profile / mastery / struggle_pattern tables | schema/migration hook (§ migration runner) |
| Calculator / step-checker (math, pharmacy calcs) | MCP inbound tools (ADR-014 §3.5) |
| Textbook upload | browser ingestion → corpus (ADR-014 §3.3) |
| Field feeds | web feed poller (ADR-014 §3.4) |
| Learning view + teacher dashboard | host shell mounting (ADR-014 §3.1) |

If anything here forces a reach into core internals, that is a Phase 0 gap
to log — ARISTOTLE is the protocol's first stress test.

### 10. Pilot protocol

1. **Ramesh** (idea originator) — self-tutors a chapter he already knows.
   He'll break it in the right ways first.
2. **Sameer** (SAICH, health IT) — loads pharmacy/HIS material; HERALD feeds
   Pakistani medical-records industry news → he builds the field's who's-who
   from day one.
3. **Moses** (DEFINER) — physics + AI foundations under existing intuitions
   (EZ water, NBCM, AGI); HERALD serves source-first field awareness with
   credibility tiering → foundation + calibration, and the raw material to
   become a primary voice rather than a downstream one.
4. **Freedom Generation School** — supervised classroom pilot with Komal's
   dashboard.

### 11. Phased build

- **A — Tutoring loop (ships first):** ingestor + curriculum map +
  prerequisite graph; student_profile + struggle_pattern; the
  TEACH→…→REMEDIATE state machine + mode prompts; SM-2 via core VIGIL;
  bilingual. Consumes Phase 0 steps 1–4.
- **B — Teacher dashboard:** MENTOR/VIGIL read views.
- **C — HERALD:** once the Phase 0 web/feed layer lands — feeds, relevance
  match, field-map graph, source tiering.

Build A against the Phase 0 contract natively (greenfield), logging every
core-reach as a protocol gap — that list is the gift the reference extension
gives LOOM and CodeForge.

## Alternatives Considered

**Multi-persona tutor (separate voices for teach/quiz/mentor)** — rejected
because it breaks the single-voice principle (§1). Alexander had one
Aristotle, not a teaching staff. The learner trusts one continuous presence;
mode transitions are felt as natural signposting, not handoffs.

**Byte-aware chunking (standard RAG)** — rejected because it's pedagogically
wrong (§4). Token-chunking ignores concept boundaries; a student can't be
gated on prerequisite mastery if chunks don't map to concepts.

**Re-implement SM-2 in ARISTOTLE** — rejected because VIGIL already does
spaced repetition in core (§2). Reusing it avoids duplication and keeps
ARISTOTLE focused on pedagogy, not infrastructure.

**Ship HERALD with the tutoring loop** — rejected because HERALD depends on
the Phase 0 web/feed layer (ADR-014 §3.4), which isn't built yet (§6, §11).
The tutoring loop ships first; HERALD layers on when feeds land.

## Consequences

**What gets easier:**
- The phased build (A → B → C) lets the tutoring loop ship without waiting
  on the web/feed layer.
- Reusing VIGIL + the graph store + the multi-corpus foundation means
  ARISTOTLE is application code, not infrastructure.
- The single-voice principle simplifies UX — one interface, not five.

**What gets harder:**
- The five modes must be carefully orchestrated so the learner never sees
  the seams. Mode transitions are natural signposting, not visible handoffs.
- The prerequisite DAG is the most important non-obvious element — if it's
  wrong, the tutor introduces concepts out of order. The curriculum map is
  AI-generated and must be reviewable/correctable by the teacher.
- HERALD's credibility tiering is load-bearing for contested topics — getting
  it wrong undermines calibration.

**Dependencies:**
- ADR-014 (Phase 0 extension platform) — the contract ARISTOTLE consumes.
- ADR-008 (multi-corpus architecture) — the foundation ARISTOTLE rides on.
- AIP_GOVERNANCE — the invariants binding on all AIP components.

**Upgrade path:**
- If the single-voice principle fails in pilot, the modes can be surfaced
  as separate personas (the decomposition already exists in code).
- If the prerequisite DAG is too rigid, the teacher can override it (the
  curriculum map is reviewable/correctable by design).
- If HERALD's feed layer never lands, ARISTOTLE remains a textbook tutor
  without field awareness — still useful, just less.

## Related

- **ADR-014** (platform) — the Phase 0 extension contract; ARISTOTLE is the
  first consumer. `AIP_Brain/docs/decisions/ADR-014-phase0-extension-host.md`
- **ADR-008** (platform) — the multi-corpus foundation. `AIP_Brain/docs/decisions/ADR-008-multi-corpus-architecture-rev3.md`
- **ADR-011** (platform) — actor role boundaries (Beast/Vigil/Sexton). `AIP_Brain/docs/decisions/ADR-011-actor-role-boundaries.md`
- **AIP_GOVERNANCE** (platform) — binding invariants on all AIP components. `AIP_Brain/AIP_GOVERNANCE.md`
- Source files most affected:
  - `aristotle/actors/{socrates,examiner,mentor}.py` — the three Phase A modes
  - `aristotle/migrations/M001_aristotle.sql` — the concept + struggle_pattern tables
  - `aristotle/workflows/tutoring_session_v1.yaml` — the TEACH→…→REMEDIATE state machine
  - `aristotle/hooks.py` — actor registration at stage 5
  - `aristotle/config.py` — bilingual defaults

---

*"The roots of education are bitter, but the fruit is sweet." — Aristotle*
*Do not modify without DEFINER review.*
