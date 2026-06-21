# AIP Aristotle — Adaptive Tutor

> *"The roots of education are bitter, but the fruit is sweet." — Aristotle*

Aristotle is an adaptive tutor — the first extension built on the
[AIP Brain](https://github.com/freedomgeneration1111-sudo/AIP_Brain) platform.
It is a pedagogical state machine that teaches, probes, quizzes, evaluates,
and remediates — with concept-aware chunking, bilingual content (English +
Urdu), spaced repetition (SM-2), and a persistent diagnostic sentence per
student that feeds every re-teaching prompt.

**Aristotle is the only character the learner ever meets.** The five
internal modes (SOCRATES, EXAMINER, VIGIL, MENTOR, HERALD) are
orchestration, not personas. The learner feels rhythm, not handoffs.
The decomposition surfaces in exactly one place: the teacher dashboard.

---

## Quick Start

### Prerequisites

- [AIP Brain](https://github.com/freedomgeneration1111-sudo/AIP_Brain) installed and running
- An OpenAI-compatible model provider configured (OpenRouter, OpenAI, Ollama)

### Install

```bash
# Install the platform first (if not already installed)
pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Brain.git

# Install Aristotle
pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git
```

The platform discovers Aristotle automatically at startup via the
`aip.extensions` entry-point group. No manual configuration needed.

### Verify Installation

```bash
python -c "
from importlib.metadata import entry_points
print('extensions:', [e.name for e in entry_points(group='aip.extensions')])
print('gui pages:', [e.name for e in entry_points(group='aip.extension_gui')])
"
# Expected:
# extensions: ['aristotle']
# gui pages: ['aristotle']
```

### Start the System

```bash
# From the AIP_Brain directory
./start.sh

# Backend API: http://localhost:8000
# GUI:         http://localhost:8080
```

### Load Concepts + Run a Session

```bash
# Check extension health
python -m aristotle.cli health

# Load sample concepts (Newton's Three Laws, bilingual English + Urdu)
python -m aristotle.cli ingest concepts_sample.yaml

# List loaded concepts
python -m aristotle.cli list-concepts

# Run a non-interactive tutoring session
python -m aristotle.cli session newton_first_law \
  --answer "objects resist changes in motion"

# Or run an interactive session (step-by-step with prompts)
python -m aristotle.cli session newton_first_law
```

### Open the GUI

- **Learner view**: http://localhost:8080/learn — concept selector + tutoring session
- **Teacher dashboard**: http://localhost:8080/dashboard — mastery stats, struggle pattern, due items
- **Operator console**: http://localhost:8080 — main AIP Brain dashboard

The left nav shows "Learn" (icon: school) and "Teach" (icon: school_outlined)
dynamically — discovered via the platform's `/health/extensions` endpoint,
no hardcoded extension names.

---

## Development Setup

```bash
git clone -b feat/multi-corpus https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
git clone https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git
cd AIP_Brain && pip install -e .
cd ../AIP_Aristotle && pip install -e .
```

Editable install means changes to either repo are picked up immediately —
no reinstall on every edit.

### Run Tests

```bash
cd AIP_Aristotle
pytest tests/ -v
```

---

## How It Works

### The Tutoring State Machine (ADR-001 §3)

```
TEACH ──► PROBE ──► QUIZ ──► EVALUATE ──┬─► (mastered) ─► next concept
  ▲                                      │
  └──────────── REMEDIATE ◄──────────────┘ (struggling → different framing)
```

The session opens itself. The system already knows where the learner left
off, what's due, and what they never quite grasped — so it begins
mid-stride, like a tutor who has been waiting. No menu, no "what would you
like to study." The learner only feels rhythm.

| Step | Mode | What happens |
|------|------|-------------|
| **TEACH** | SOCRATES | Explain the concept. Pull the passage from the textbook corpus. On retry, use a different framing. |
| **PROBE** | EXAMINER | Low-stakes "tell me in your own words." Not graded — checks the explanation landed. |
| **QUIZ** | EXAMINER | A real question at the concept's Bloom's taxonomy level. |
| **EVALUATE** | EXAMINER + MENTOR | Score the answer. Update mastery + struggle_pattern. Schedule next review via SM-2. |
| **REMEDIATE** | SOCRATES | Re-teach from a new angle, informed by the struggle_pattern. |

Branching is invisible. The student never sees the mode names.

### The Five Modes (ADR-001 §2)

| Mode | Role | Status | How it works |
|------|------|--------|-------------|
| **SOCRATES** | Teach / explain / re-explain | ✅ Active | Calls `model_provider.call("beast", ...)` to generate explanations. Pulls concept content from `aristotle_concept` table. Retry mode uses different framing informed by struggle_pattern. |
| **EXAMINER** | Probe / quiz / evaluate | ✅ Active | Calls `model_provider.call("evaluation", ...)` to generate questions + score answers. Returns JSON with score, mastery_achieved, feedback. |
| **MENTOR** | Long-arc tracking | ✅ Active | Calls `model_provider.call("sexton", ...)` to write AI-diagnostic struggle_pattern sentences. Reads/writes `aristotle_struggle_pattern` table. |
| **SM-2** | Spaced repetition | ✅ Active | Implemented directly in `aristotle/sm2.py` (the platform's Vigil actor is quality evaluation, not SM-2 — platform gap logged). Schedules reviews based on the SuperMemo 2 algorithm. |
| **HERALD** | Field awareness | ⏳ Phase C | Source-first news tied to curriculum. Blocked on platform web/feed layer (ADR-014 §3.4). |

### Bilingual (ADR-001 §7)

Urdu and English side by side. A concept can be taught in English and
probed in Urdu, or the whole session can live in the learner's home
language. For Freedom Generation students this is the difference between
the tool meeting them where they are and not.

The schema uses `content_primary` + `content_alt` + `content_alt_lang`
(ISO 639-1) — generalizes to any bilingual pair without schema changes.

### Concept-Aware, Not Byte-Aware (ADR-001 §4)

Standard RAG token-chunking is pedagogically wrong. Aristotle chunks by
concept with a prerequisite DAG:

```
concept: { id, textbook_chapter, topic, subtopic, bloom_target(1-6),
           content_primary, content_alt, content_alt_lang,
           prerequisite_concept_id }
```

Before selecting the next concept, the session consults the prerequisite
graph — a student never meets a concept whose foundations are unmastered.

### The Struggle Pattern (ADR-001 §2 MENTOR)

One persistent AI-written diagnostic sentence per student — the tutor's
memory of *who this learner is*. After each EVALUATE, MENTOR calls a model
to update the sentence based on the student's recent performance. The
sentence feeds every REMEDIATE prompt, so the re-teaching addresses the
specific gap.

### Teacher Dashboard (ADR-001 §8)

The ONE place the actor decomposition is visible. Komal's scarce human
time goes where only a human can go; the tutor absorbs infinite patient
repetition. Three panels:
1. **Stats header** — total concepts, mastered count, due count
2. **Struggle pattern** — the diagnostic sentence, prominent
3. **Mastery table** — concept, topic, mastered, last score, next due date
   (sorted by due date — what needs attention is at the top)

---

## Architecture

Aristotle conforms to the [ADR-014](https://github.com/freedomgeneration1111-sudo/AIP_Brain/blob/feat/multi-corpus/docs/decisions/ADR-014-phase0-extension-host.md)
extension contract. The import boundary is machine-enforced:
- Aristotle imports from `aip.foundation.protocols.actors` only (the Actor Protocol)
- The platform imports nothing from Aristotle — discovery is dynamic via entry points
- The boundary is checked by `tests/test_import_boundary.py` in both repos

See [ADR-001](docs/decisions/ADR-001-aristotle-architecture.md) for the
full architecture spec.

### Package Structure

```
AIP_Aristotle/
├── aristotle/
│   ├── extension.yaml              # Manifest v1 (declares corpus, actors, migrations)
│   ├── entrypoint.py               # get_manifest() — entry point the host discovers
│   ├── config.py                   # AristotleSettings (bilingual defaults: en/ur)
│   ├── hooks.py                    # on_load: 3 actors + 2 GUI pages + API router
│   ├── api.py                      # 7 FastAPI routes (concepts, ingest, session, dashboard, health)
│   ├── cli.py                      # HTTP client CLI (health, list-concepts, ingest, session)
│   ├── gui.py                      # 2 NiceGUI pages: /learn (learner) + /dashboard (teacher)
│   ├── ingestor.py                 # YAML → aristotle_concept (concept-aware chunking)
│   ├── session.py                  # TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine
│   ├── sm2.py                      # SuperMemo 2 spaced repetition algorithm
│   ├── actors/
│   │   ├── socrates.py             # teach() — calls model_provider.call("beast")
│   │   ├── examiner.py             # probe(), quiz(), evaluate() — calls "evaluation"
│   │   └── mentor.py               # update_struggle_pattern(), get_struggle_pattern() — calls "sexton"
│   ├── migrations/
│   │   ├── M001_aristotle.sql      # aristotle_concept + aristotle_struggle_pattern
│   │   └── M002_aristotle_mastery.sql  # aristotle_mastery (SM-2 state per concept)
│   └── workflows/
│       └── tutoring_session_v1.yaml  # 7-node state machine (declared, actor-driven execution)
├── tests/                           # 150 tests collected, 0 errors (boundary + conformance + behavior + integration)
├── docs/decisions/
│   ├── ADR-000-template.md
│   └── ADR-001-aristotle-architecture.md
├── concepts_sample.yaml            # Newton's Three Laws (bilingual, prerequisite DAG)
├── AGENTS.md, CONTRIBUTING.md, PLANNED_FEATURES.md, TECH_DEBT.md, STATUS.md, ROADMAP.md
└── pyproject.toml                  # Entry points: aip.extensions + aip.extension_gui
```

### API Routes

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/aristotle/health` | Extension health check |
| GET | `/aristotle/concepts` | List all ingested concepts |
| POST | `/aristotle/ingest` | Ingest concepts from YAML |
| POST | `/aristotle/session/start` | Start a tutoring session |
| POST | `/aristotle/session/step` | Advance a session one step (interactive) |
| POST | `/aristotle/session/run` | Run a full session (non-interactive) |
| GET | `/aristotle/dashboard` | Teacher dashboard data (mastery, struggle, due items) |

---

## Pilot Protocol (ADR-001 §10)

1. **Ramesh** (idea originator) — self-tutors a chapter he already knows. He'll break it in the right ways first.
2. **Sameer** (SAICH, health IT) — loads pharmacy/HIS material; HERALD feeds Pakistani medical-records industry news.
3. **Moses** (DEFINER) — physics + AI foundations; HERALD serves source-first field awareness with credibility tiering.
4. **Freedom Generation School** — supervised classroom pilot with Komal's dashboard.

---

## Roadmap

- **Phase A** ✅ — Tutoring loop (actors, session coordinator, SM-2, content ingestor, CLI, API, GUI learning view)
- **Phase B** ✅ — Teacher dashboard (mastery stats, struggle pattern, due items, LEFT JOIN for all concepts)
- **Phase C** ⏳ — HERALD (field awareness — blocked on platform web/feed layer)

See [ROADMAP.md](ROADMAP.md) for the full plan.

---

## Documentation

- [`STATUS.md`](STATUS.md) — Current operational state
- [`PLANNED_FEATURES.md`](PLANNED_FEATURES.md) — Feature tracker (Phase A/B/C)
- [`TECH_DEBT.md`](TECH_DEBT.md) — Debt register (8 items, including 2 platform gaps)
- [`ROADMAP.md`](ROADMAP.md) — Phase A/B/C roadmap
- [`docs/decisions/ADR-001-aristotle-architecture.md`](docs/decisions/ADR-001-aristotle-architecture.md) — Full architecture spec
- [`AGENTS.md`](AGENTS.md) — Coding cycle protocol + extension boundary discipline

---

## License

BUSL-1.1 — same as AIP Brain. See [LICENSE](https://github.com/freedomgeneration1111-sudo/AIP_Brain/blob/feat/multi-corpus/LICENSE).
