# ADR-002: Intake, Placement, and Long-Arc Learning Plan
## Revision 2 — Research-Grounded Pedagogy

```
STATUS:   DRAFT — for DEFINER review
REPO:     AIP_Aristotle
DATE:     2026-06-19
SUPERSEDES: ADR-002 Rev 1
DEPENDS:  ADR-001 (ARISTOTLE architecture), ADR-014 (extension platform),
          ADR-008 (multi-corpus)
PHASE:    D — Onboarding (after Phase C HERALD)
```

> **What changed from Rev 1.** Rev 2 incorporates a survey of the current
> learning-science literature and existing AI tutoring systems (Khanmigo,
> Duolingo Birdbrain + Max, Carnegie Learning MATHia, Harvard 2025 RCT,
> Cognitive Load Theory, DKT/BKT research, desirable difficulties literature).
> The result: the state machine gains a PREDICT step and a hint ladder; SOCRATES
> gains faded worked examples; EXAMINER gains error diagnosis; the session
> coordinator gains interleaving logic; MENTOR gains misconception tracking;
> and a cold-start mastery check catches overreliance. The intake, placement,
> long-arc plan, OCR, and voice sections from Rev 1 are preserved unchanged.
>
> **Calibration.** Written against verified codebase
> `AIP_Brain feat/multi-corpus @ a391fb0`, `AIP_Aristotle main @ 7c3c188`.
> All "exists" / "net-new" claims confirmed in tree. See §11 for inventory.

---

## 1. What this ADR specifies

**Part A — Pedagogical core (revised from Rev 1):** The research-grounded
tutoring state machine, actor behaviors, session design, and mastery model
that every session type builds on.

**Part B — Onboarding system (carried from Rev 1):** Intake interview,
placement calibration, and the long-arc versioned learning plan.

**Part C — New capabilities (carried from Rev 1):** OCR, voice, and
file upload.

---

# PART A — PEDAGOGICAL CORE

## 2. What the research says we must do differently

Six findings from the ITS literature are load-bearing enough to change
the design. The others are enhancements. The six:

| Finding | Source | Design change |
|---|---|---|
| Generation effect: being wrong before learning is more effective than passive reading | Bjork, desirable difficulties literature | Add PREDICT step before TEACH |
| Hint ladders outperform immediate full remediation | Carnegie Learning MATHia, ITS research | Add HINT_1→HINT_2 before REMEDIATE |
| Error diagnosis ("why you were wrong") outperforms generic feedback | Duolingo "Explain My Answer" (MIT Sloan, 2023); BEA 2025 shared task | EXAMINER produces misconception diagnosis, not just correctness verdict |
| Faded worked examples: complete → partial → independent | Sweller worked example effect; most replicated finding in CLT | SOCRATES checks mastery level and adapts presentation mode |
| Interleaving within sessions outperforms blocked practice | Contextual interference research; neuroimaging studies | Session coordinator mixes 2-3 concepts per session |
| Transfer problems require explicit design | Harvard 2025 RCT; Carnegie Learning | EXAMINER distinguishes recognition from transfer questions |

**Three things the field does that ARISTOTLE should NOT copy:**
- Streak/guilt-based engagement loops (Duolingo) — manipulative, not educational
- Always-withhold-the-answer Socratic mode for procedural content — right for K-12 homework; wrong for adult learners studying physics derivations
- Emotion detection via camera/biometrics — wrong complexity/payoff ratio for single-learner local-first system

---

## 3. Revised state machine: PREDICT → TEACH → PROBE → QUIZ → EVALUATE → [HINT_1 → HINT_2 →] REMEDIATE

```
PREDICT  ──► TEACH ──► PROBE ──► QUIZ ──► EVALUATE ──┬─► mastered ─► NEXT_CONCEPT
                                                       │
                                              ┌── struggling
                                              ▼
                                           HINT_1 ──► EVALUATE ──┬─► mastered
                                                                  │
                                                         HINT_2 ──► EVALUATE ──┬─► mastered
                                                                               │
                                                                          REMEDIATE ──► PROBE
```

The learner never sees these state names. They feel: anticipation (PREDICT),
explanation (TEACH), low-stakes check (PROBE), real question (QUIZ),
response (EVALUATE), optional nudge (HINT_1/2), re-teaching from a new
angle (REMEDIATE).

### PREDICT (new)

Before any explanation, Aristotle asks the learner to predict:
*"Before we go into this — what do you think [concept] means? Say it
in your own words, even if you're guessing."*

**Why this works:** The generation effect is one of the strongest effects
in learning science. Being wrong in a prediction, then seeing the correct
explanation, produces stronger encoding than reading the explanation cold.
The struggle of generating an answer — even an incorrect one — creates a
retrieval cue that makes the subsequent correct information more memorable.

**What ARISTOTLE does with the prediction:**
- PLACER logs it as a PlacementEvent (CONFIRMED / SHAKY / ABSENT /
  UNEXPECTED_STRENGTH) — free calibration data every session
- MENTOR compares it to the eventual answer for misconception pattern data
- The prediction is fed into SOCRATES' TEACH prompt so the explanation
  can directly address the gap between what the learner believed and what
  is true

**When to skip PREDICT:** On a spaced-repetition review of a concept the
student has already mastered (SessionContext.mastery_level ≥ 3), skip
directly to PROBE. PREDICT is for new concepts and early-stage mastery.

### TEACH (revised)

SOCRATES now adapts presentation mode to mastery level (faded worked
examples — see §4):

- **level 0 (new concept):** complete worked example, then explanation
- **level 1–2 (early mastery):** partial faded example; student completes
  the final step before Aristotle shows it
- **level 3+ (near-mastered):** conceptual explanation only; no worked example

The learner's prediction (from PREDICT) is always woven into the opening
of TEACH: *"You said X. Here's what's actually going on — and you were
closer than you think / here's why that's a common place to start."*

For quantitative/procedural content (physics derivations, pharmacy
calculations), SOCRATES provides full step-by-step worked examples
regardless of mastery level when the student is new to the problem type.
Khanmigo's "never give the answer" principle is correct for K-12 homework;
it is wrong for an adult studying advanced physics. The Harvard 2025 RCT
demonstrated that 83% of students rated AI explanations as good as or
better than human instructors specifically when prompts were enriched with
comprehensive step-by-step solutions. ARISTOTLE follows this for
procedural content while staying Socratic for conceptual understanding.

### PROBE (unchanged from ADR-001)

Low-stakes check: *"Tell me in your own words how you'd explain this."*
No right/wrong verdict. Feeds EVALUATE.

### QUIZ (revised — recognition vs. transfer)

EXAMINER generates two classes of question and tracks which the student
has seen:

- **Recognition:** recall or identify (tests whether the concept is known)
- **Transfer:** apply the concept to a novel situation (tests whether it
  is understood)

Sequencing: recognition first until mastery_level ≥ 2; then introduce
transfer questions. Transfer is the hard target; recognition is the gate.

Example for Newton's First Law:
- Recognition: *"State Newton's First Law in your own words."*
- Transfer: *"You're on a train that brakes suddenly. A cup of coffee
  is on the table in front of you. Describe what happens to it and why."*

EXAMINER labels each question in SessionContext so MENTOR can track whether
the student's weak point is recognition or transfer — they are different
problems and require different remediation.

### EVALUATE (revised — error diagnosis)

When a student answers incorrectly, EXAMINER no longer just marks wrong
and moves to remediation. It produces a three-part error diagnosis:

1. **What you thought:** "It sounds like you were thinking that [X]"
2. **Why that's wrong:** "The issue is [Y] — and this is a common place
   to get stuck because [Z]"
3. **The one thing to hold onto:** A single memorable corrective sentence

This is Duolingo's "Explain My Answer" applied to concept tutoring. Large
language models are well-suited to jargon-free error diagnosis — this is
one of the few places where LLM fluency is a direct pedagogical asset.

MENTOR logs the misconception (the "what you thought" part) to
`aristotle_misconception_log`, which builds the student's misconception
profile over time. After 3+ instances of the same misconception class,
MENTOR updates `struggle_pattern` to explicitly name it.

When the answer is correct, EVALUATE produces a brief confirmation that
names *why* it was right — not just "correct!" but "Exactly — you identified
that the object's inertia resists the change in motion, which is the core
of the law."

### HINT_1 and HINT_2 (new)

Rather than going immediately to full REMEDIATE when EVALUATE is wrong,
ARISTOTLE offers graduated hints that preserve the desirable difficulty of
having to work for the answer.

**HINT_1:** Narrows the search space without giving the answer.
*"Think about what stays constant when no net force is applied."*

**HINT_2:** Closer to the answer but still requires the student to close
the gap.
*"An object already moving — what would it need to change its motion?"*

SessionContext tracks `hint_count: int` (reset per concept per session).
If the student gets it after HINT_1 or HINT_2, that performance is marked
differently in the mastery model than getting it on first attempt — and
differently from getting it only after full REMEDIATE.

REMEDIATE (full re-teach from a new angle) fires only if both hints fail.
This preserves what the research calls the desirable difficulty: partial
success after hints is more effective for retention than being walked
through the answer immediately.

### REMEDIATE (unchanged from ADR-001, but now truly last resort)

SOCRATES re-teaches from a different framing, informed by the specific
misconception EVALUATE diagnosed. The error diagnosis feeds the REMEDIATE
prompt: *"The student thought X. Address this directly while re-explaining
the concept from a different angle."*

### NEXT_CONCEPT

Session coordinator checks: is there an interleaved concept due? (§6).
If yes, routes to the next concept's PREDICT/PROBE (depending on whether
it's new or review). If no, session closes.

---

## 4. SOCRATES — Faded Worked Examples

Worked examples are the single highest-value application of Cognitive Load
Theory to instruction (Sweller, 1985 — one of the most replicated findings
in educational psychology). They are most effective for novices; their
value diminishes as mastery increases (students stop attending to them).
ARISTOTLE must adapt:

```python
def _select_teach_mode(mastery_level: int, content_type: str) -> TeachMode:
    """Choose presentation mode based on mastery and content type."""
    if content_type == "procedural":  # physics math, pharmacy calcs
        # Always show complete worked example for procedural content
        # regardless of mastery. Never Socratic-withhold for procedures.
        return TeachMode.COMPLETE_WORKED_EXAMPLE
    # Conceptual content: fade with mastery
    if mastery_level == 0:
        return TeachMode.COMPLETE_WORKED_EXAMPLE  # full example + explanation
    elif mastery_level <= 2:
        return TeachMode.FADED_EXAMPLE           # partial, student completes
    else:
        return TeachMode.CONCEPTUAL              # no example; explanation only
```

**Complete worked example (level 0):**
Aristotle presents a fully solved concrete instance of the concept,
annotated step by step, then explains the principle. The example precedes
the principle — always concrete before abstract.

**Faded example (level 1-2):**
Aristotle presents a partially solved example and stops. "The next step
from here — what do you do?" The student's answer is evaluated before
Aristotle reveals the completion.

**Conceptual (level 3+):**
Aristotle explains the concept without an example. The concept is known;
an example would be redundant and would reduce germane cognitive load
by giving the student something to pattern-match rather than reason.

---

## 5. EXAMINER — Revised Actor Spec

```python
class ExaminerActor(Actor):
    """PROBE, QUIZ (recognition + transfer), EVALUATE with error diagnosis,
    HINT generation, and cold-start mastery verification.

    Uses the 'evaluation' model slot — same as in current implementation.
    """

    async def probe(self, ctx, concept_id, prediction_text=None) -> ActorResult:
        """Low-stakes comprehension check.
        prediction_text (from PREDICT) is included in the prompt
        so the probe can specifically test whether the prediction gap was closed.
        """

    async def quiz(self, ctx, concept_id, *, mastery_level: int,
                   quiz_type: str = "auto") -> ActorResult:
        """Generate a quiz question.
        quiz_type: "recognition" | "transfer" | "auto"
        "auto" selects based on mastery_level: recognition if < 2, else transfer.
        Returns ActorResult with payload containing {question, quiz_type, expected_elements}.
        expected_elements lets evaluate() know what to look for in the student's answer.
        """

    async def evaluate(self, ctx, concept_id, student_answer: str,
                       expected_elements: list[str],
                       prediction_text: str | None = None) -> ActorResult:
        """Evaluate answer and produce error diagnosis if wrong.
        Returns:
          {
            "correct": bool,
            "misconception": str | None,   # what the student likely thought
            "diagnosis": str | None,       # why that's wrong, jargon-free
            "corrective": str | None,      # one memorable corrective sentence
            "confirmation": str | None,    # if correct: why it was right
            "quiz_type": "recognition"|"transfer"
          }
        """

    async def generate_hint(self, ctx, concept_id, hint_number: int,
                            wrong_answer: str,
                            misconception: str | None) -> ActorResult:
        """Generate HINT_1 or HINT_2.
        hint_number: 1 or 2. Hint 2 is closer to the answer.
        Misconception from evaluate() informs the hint's angle.
        """

    async def cold_start_check(self, ctx, concept_id) -> ActorResult:
        """Unassisted probe — no hints allowed, no preview.
        Called occasionally when SM-2 marks concept as mastered.
        Detects overreliance: student who reached mastery via hints
        may not have independent recall.
        Returns: {independent_recall: bool, confidence: float}
        """
```

---

## 6. Session Coordinator — Interleaving

The current `session.py` takes one concept per session. Research on
interleaved practice (contextual interference effect, Bjork) consistently
shows that mixing concepts from different topics within a single session
produces better long-term retention than blocked practice on one concept.

**Revised session structure:**

```python
@dataclass
class SessionContext:
    student_id: str = "definer"
    state: SessionState = SessionState.PREDICT

    # Interleaved concept queue (new)
    primary_concept_id: str = ""           # new or in-progress concept (main)
    review_concept_ids: list[str] = field(default_factory=list)  # due for retrieval check
    current_concept_id: str = ""           # whichever concept is active right now

    # Mastery tracking per concept in this session
    hint_count: int = 0
    quiz_type: str = ""                    # "recognition" or "transfer"
    last_prediction: str = ""
    last_explanation: str = ""
    last_misconception: str = ""
    retry_count: int = 0
```

**Session opening logic:**

```python
async def open_session(ctx, student_id="definer") -> tuple[str, list[str]]:
    """Select concepts for an interleaved session.

    Returns: (primary_concept_id, review_concept_ids)

    Strategy:
    - primary: the highest-priority new or in-progress concept
      (from learning plan concept_sequence + mastery state)
    - reviews: up to 2 concepts due for spaced-repetition check
      (mastered or mid-mastery, SM-2 due date past or today)

    The session teaches the primary concept first, then does
    retrieval checks on the review concepts. The retrieval checks
    are intentionally brief (PROBE only, no re-teaching unless failed).
    """
```

**Why interleaving works:** Each time the student encounters a different
concept, returning to an earlier one requires extra retrieval effort
(contextual interference). That effort is the "desirable difficulty" that
creates stronger memories than blocked practice. The student will feel the
session is harder; retention measured a week later will be significantly
better.

**For pre-alpha single-concept sessions:** The interleaving logic degrades
gracefully — if only one concept is active or due, the session runs as a
single-concept session. The data model supports interleaving; the session
coordinator enables it when the SM-2 queue has enough content.

---

## 7. MENTOR — Misconception Tracking (revised)

Current MENTOR tracks `struggle_pattern` as one persistent sentence updated
after each session. Revised MENTOR additionally tracks misconceptions at the
instance level:

### New table: aristotle_misconception_log

```sql
-- Part of M003
CREATE TABLE IF NOT EXISTS aristotle_misconception_log (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL DEFAULT 'definer',
    concept_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    quiz_type TEXT NOT NULL,           -- "recognition" | "transfer"
    student_answer TEXT NOT NULL,
    misconception_text TEXT NOT NULL,  -- "what the student thought"
    diagnosis_text TEXT NOT NULL,      -- "why that's wrong"
    hint_count_used INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0,  -- 1 if eventually got it
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_misconception_student_concept
    ON aristotle_misconception_log(student_id, concept_id);
```

**Pattern recognition in MENTOR:** After 3+ log entries with similar
misconception_text for a student, MENTOR updates `struggle_pattern` to
explicitly name the pattern. Example: after three sessions where the
student confuses "velocity" with "speed" across different concepts in
mechanics, struggle_pattern gets updated to include "consistently conflates
scalar speed with vector velocity — requires explicit direction emphasis."

**Cold-start check result logging:** When `cold_start_check()` returns
`independent_recall=False` for a concept marked as mastered, MENTOR logs
it as a `MASTERY_OVERRELIANCE` event and downgrades the SM-2 mastery level
for that concept, re-inserting it into the due queue.

---

## 8. Mastery Model — Probabilistic Extension of SM-2

SM-2 is good but treats each concept in isolation. The research field has
moved to Deep Knowledge Tracing (DKT) and Bayesian Knowledge Tracing (BKT).
For ARISTOTLE's single-learner scale, a pragmatic BKT-inspired extension
of SM-2 is the right step without the overhead of a neural KT model.

**What SM-2 misses that BKT catches:**
- **Slip probability:** student knows the concept but got it wrong anyway
  (test-day mistake, misread, slip). SM-2 penalizes this the same as genuine
  non-mastery. BKT distinguishes them.
- **Guess probability:** student doesn't know but got it right by luck.
  SM-2 rewards this the same as genuine mastery.
- **Skill transfer:** mastering concept A should partially update the
  probability of knowing concept B, if A is a prerequisite of B.

**Pragmatic implementation (not a full DKT model):**

```python
@dataclass
class MasteryState:  # extends current aristotle_mastery
    # existing SM-2 fields
    easiness_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    next_review_at: str | None = None
    last_score: float | None = None
    mastered: int = 0

    # new BKT-inspired fields (M003 migration)
    hint_assisted_correct: int = 0   # correct answers that required hints
    slip_count: int = 0              # wrong answers on known concepts
                                     # (wrong after 3+ consecutive correct)
    cold_start_passed: int = 0       # 1 = passed unassisted cold-start check
    transfer_correct: int = 0        # correct transfer questions
    transfer_attempted: int = 0      # total transfer questions seen
```

**Prerequisite transfer update:** When a student correctly answers a
transfer question on concept B (without hints), MENTOR checks if B has
prerequisites. For each prerequisite concept A not yet mastered, the SM-2
interval for A is extended by 1 day — soft evidence that the student may
know A even if not formally tested.

This is not a trained KT model. It is a rule-based approximation that
captures the most important BKT insights (slip/guess distinction, skill
transfer) without requiring training data or neural infrastructure.

---

# PART B — ONBOARDING SYSTEM (carried from Rev 1)

## 9. The five intake stages

A new learner opens ARISTOTLE and sees one prompt, not a settings page:
**"What are you trying to learn, and why does it matter to you?"**

**Stage 1 — Goal elicitation.** Open question, free-form answer. The intake
actor reads for domain, depth target, time horizon, and prior knowledge
signals. Reflects the reading back for learner confirmation.

**Stage 2 — Credential / course context.** "Are you working toward a
specific course, certification, or degree?" Syllabus upload or URL if yes.
Free-form self-directed if no.

**Stage 3 — Material inventory.** Four paths: PDF upload → direct ingest;
table of contents photo → OCR scaffold; no materials → AI recommends from
internal knowledge (web search blocked until Phase C); job/credential
target → AI sources qualifications (also Phase C).

**Stage 4 — Learning plan generation and review.** AI synthesizes goal,
context, and materials into a proposed learning plan including concept
sequence, corpus structure, HERALD feed recommendations (Phase C), and
first-session recommendation. Learner amends conversationally.

**Stage 5 — Placement calibration.** Before first tutoring session, a
diagnostic conversation using the PREDICT mode (above) against concepts
from the approved plan. PLACER reads each prediction for CONFIRMED /
SHAKY / ABSENT / UNEXPECTED_STRENGTH and adjusts the concept sequence.
The learner never sees the diagnostic labels; they experience it as Aristotle
getting to know them.

---

## 10. Data model (complete)

### 10.1 aristotle_learning_plan (versioned, append-only)

```sql
-- M003_aristotle_onboarding.sql
CREATE TABLE IF NOT EXISTS aristotle_learning_plan (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL DEFAULT 'definer',
    version INTEGER NOT NULL DEFAULT 1,
    goal_text TEXT NOT NULL,
    goal_type TEXT NOT NULL CHECK(goal_type IN (
        'course_completion', 'expertise', 'career_prep',
        'certification', 'self_directed'
    )),
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN (
        'DRAFT', 'APPROVED', 'ACTIVE', 'REVISED', 'ACHIEVED', 'SUPERSEDED'
    )),
    parent_plan_id TEXT REFERENCES aristotle_learning_plan(id),
    change_event_type TEXT CHECK(change_event_type IN (
        'INTAKE', 'PLACEMENT', 'USER_REVISION', 'MILESTONE', 'GOAL_SHIFT'
    )),
    change_reason TEXT,
    concept_sequence_json TEXT,    -- ordered list of concept_ids
    corpus_plan_json TEXT,         -- which corpora to create/use
    herald_feeds_json TEXT,        -- recommended HERALD sources (Phase C)
    external_reading_json TEXT,    -- papers/books to read (not yet ingested)
    credential_target TEXT,
    job_target TEXT,
    job_qualifications_json TEXT,  -- extracted from job postings (Phase C)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at TEXT,
    activated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_plan_student_active
    ON aristotle_learning_plan(student_id, status)
    WHERE status = 'ACTIVE';
```

### 10.2 aristotle_placement_event

```sql
CREATE TABLE IF NOT EXISTS aristotle_placement_event (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL DEFAULT 'definer',
    plan_id TEXT NOT NULL REFERENCES aristotle_learning_plan(id),
    session_id TEXT,
    concept_id TEXT NOT NULL REFERENCES aristotle_concept(id),
    finding TEXT NOT NULL CHECK(finding IN (
        'CONFIRMED', 'SHAKY', 'ABSENT', 'UNEXPECTED_STRENGTH'
    )),
    reasoning TEXT NOT NULL,
    plan_update_id TEXT REFERENCES aristotle_learning_plan(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 10.3 aristotle_intake_session

```sql
CREATE TABLE IF NOT EXISTS aristotle_intake_session (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL DEFAULT 'definer',
    stage TEXT NOT NULL CHECK(stage IN (
        'GOAL', 'CONTEXT', 'MATERIALS', 'PLAN_REVIEW', 'PLACEMENT', 'COMPLETE'
    )),
    conversation_json TEXT,
    draft_plan_id TEXT REFERENCES aristotle_learning_plan(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 10.4 aristotle_predict_event (new — Rev 2)

```sql
CREATE TABLE IF NOT EXISTS aristotle_predict_event (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL DEFAULT 'definer',
    concept_id TEXT NOT NULL REFERENCES aristotle_concept(id),
    session_id TEXT NOT NULL,
    prediction_text TEXT NOT NULL,
    finding TEXT CHECK(finding IN (
        'CONFIRMED', 'SHAKY', 'ABSENT', 'UNEXPECTED_STRENGTH'
    )),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- The finding column is populated by PLACER after comparing the
-- prediction to the concept's content. NULL during session; set after.
```

### 10.5 aristotle_misconception_log (new — Rev 2)

See §7 above.

### 10.6 aristotle_mastery — extended columns (M003 addition)

M003 adds to the existing `aristotle_mastery` table:

```sql
ALTER TABLE aristotle_mastery ADD COLUMN hint_assisted_correct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN slip_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN cold_start_passed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN transfer_correct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN transfer_attempted INTEGER NOT NULL DEFAULT 0;
```

---

## 11. New actors

### INTAKE actor

Drives the five-stage intake conversation. Uses `synthesis` model slot
(conversational quality). Cadence=0 (manual only). Runs
`advance_intake(ctx, session_id, student_input)` → returns next question
or plan summary. Resumes from `aristotle_intake_session.conversation_json`
if interrupted.

### PLACER actor

Drives placement calibration. Uses `evaluation` model slot.
- `probe_concept(ctx, concept_id, prediction_text)` → finding + reasoning
- Runs after INTAKE stage 4 (plan approved)
- Also processes each PREDICT event during ongoing tutoring sessions
  (continuous micro-placement)

---

## 12. Voice architecture

Voice is a UI layer. The tutoring state machine runs identically; I/O changes.

**Input (STT):** Browser Web Speech API via `ui.run_javascript` — zero
new dependencies; Chrome/Edge native. For Urdu and noise-sensitive
environments: `synthesis` model slot configured with Whisper endpoint.

**Output (TTS):** Browser `speechSynthesis` API. For higher quality:
provider TTS endpoint via a new `tts` model slot.

**Mode toggle:** Microphone button in session header. Text input hidden
in voice mode; response spoken automatically. Same session API underneath.

**Single-voice principle:** PREDICT, TEACH, PROBE, QUIZ, EVALUATE, HINT,
REMEDIATE — all delivered in the same voice, different register. No mode
changes audible. The tutor shifts from curious ("what do you think?") to
explanatory to questioning to diagnostic without ever sounding like a
different person.

**Language:** `content_alt_lang` ISO 639-1 code selects the TTS voice.
For bilingual sessions, voice switches per-turn based on response language.

---

## 13. OCR architecture

**What exists:** `pytesseract` 5.3.4, `Pillow` 12.1.1, `pypdf` 5.9.0 all
installed. NiceGUI `ui.upload` supports image and PDF. The ingest pipeline
routes `.pdf` to `_try_parse_pdf`.

**Known bug (fix before Phase D):**
`corpus_ingest_pipeline.py:254` does `from PyPDF2 import PdfReader` —
the old package name. `pypdf` 5.x exports `from pypdf import PdfReader`.
One-word change activates real PDF extraction. Logged as DEBT-012.

**Image → OCR → concept turn:**

```python
async def ingest_image_page(image_bytes: bytes, page_label: str, ...) -> CorpusTurn:
    """OCR a phone photo of a textbook page.
    Metadata: source_type="photographed_page", ocr_confidence=<score>
    """
```

**Table of contents photo → scaffold:**

```python
async def parse_toc_image(image_bytes: bytes, ...) -> list[TOCEntry]:
    """OCR a TOC photo → scaffold concept graph for learner review."""
```

**Metacognitive note for OCR'd content:** ARISTOTLE should signal when
a concept's content came from OCR rather than a clean PDF: "This section
came from a photo — if anything seems unclear or oddly phrased, it may
be an OCR artifact." Trust calibration for learner.

---

## 14. Metacognitive transparency (new — Rev 2)

One finding from the literature that costs almost nothing to implement:
students who understand why the learning method works retain more and
persist longer. Deslauriers et al. found that students in active learning
environments learned more but *felt* they learned less — they misinterpreted
mental effort as failure.

ARISTOTLE should occasionally (not constantly — once per 3-5 sessions)
name what it's doing and why:

- After a hard retrieval: *"That felt difficult — that's the design. Retrieving something when it's hard to access creates a stronger memory trace than re-reading the explanation would."*
- After introducing PREDICT: *"I'm going to ask you what you think before explaining. Getting it wrong first actually makes the explanation stick better."*
- After interleaved review: *"I'm mixing in a question from last week's concept — your brain has to work harder to retrieve it, which is exactly what we want."*

MENTOR tracks session count and triggers these transparency notes at
appropriate intervals. They're short — one or two sentences — and genuine,
not performative.

---

## 15. Phase dependencies and build order

```
Phase A (done)    Tutoring loop — TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE
Phase B (done)    Teacher dashboard
Phase B.5 (new)  Revised tutoring loop — adds PREDICT, HINT ladder,
                  error diagnosis, faded examples, interleaving, transfer
                  questions, misconception log, mastery model extension.
                  Can ship incrementally alongside Phase D.
Phase C           HERALD — blocked on ADR-014 §3.4 web/feed layer
Phase D           Intake + Placement + Long-arc plan (this ADR)
```

**Phase B.5 is net-new and can start immediately.** The pedagogical
improvements (PREDICT, hints, error diagnosis, faded examples, interleaving)
don't require the intake or placement systems. They improve the existing
tutoring loop now, before any new learner walks in.

**Phase B.5 internal order:**
1. Add PREDICT step to session.py + predict_event table (M003 partial)
2. Add HINT_1/HINT_2 to SessionState + EXAMINER.generate_hint()
3. Add error diagnosis to EXAMINER.evaluate()
4. Add faded worked example logic to SOCRATES.teach()
5. Add interleaving to session coordinator
6. Add transfer question type to EXAMINER.quiz()
7. Add misconception_log table + MENTOR misconception tracking
8. Add extended mastery columns (M003 addition to aristotle_mastery)
9. Add cold_start_check() to EXAMINER

**Phase D internal order** (after B.5):
1. M003 full schema (learning_plan, placement_event, intake_session)
2. INTAKE actor + intake session API route
3. INTAKE GUI page at `/intake`
4. `ui.upload` for PDF + image
5. OCR path (after fixing pypdf import bug)
6. PLACER actor + placement API route
7. Voice mode toggle
8. Phase C: HERALD (after ADR-014 web/feed layer)

---

## 16. Open DEFINER decisions

| # | Blocking? | Decision | Recommendation |
|---|---|---|---|
| 1 | **YES** | Backup strategy A/B/C (ADR-014 §9.7) | Option A |
| 2 | No | OCR quality: pytesseract (local, free) vs vision model slot (faster, costs tokens) | pytesseract for Phase D; upgrade when accuracy matters |
| 3 | No | Voice STT: browser Web Speech API (zero-dep) vs Whisper slot (Urdu, noisy) | Browser for Phase D; Whisper when Urdu session quality is tested |
| 4 | No | `ActorResult.data` field: add to platform Protocol (breaking change) or keep error-as-payload | Add `data: Any = None` to ActorResult; update all actors |
| 5 | No | Intake conversation language: English-only for intake, bilingual for tutoring? | English-only intake for Phase D; bilingual in Phase E |
| 6 | No | Cold-start check frequency: how often to run an unassisted verification on "mastered" concepts? | Every 5th session per concept once mastered |
| 7 | No | Faded example for procedural vs conceptual: approve the level mapping in §4 | Approved as specified |

---

## 17. Calibration inventory (verified against tree @ 2026-06-19)

| Capability | Status | Notes |
|---|---|---|
| `ui.upload` (PDF + image) | ✅ Exists | NiceGUI 3.13 |
| `pypdf` 5.9.0 | ✅ Installed | One-word import bug (DEBT-012) |
| `pytesseract` 5.3.4 + `Pillow` 12.1.1 | ✅ Installed + configured | |
| `model_slot` synthesis | ✅ Exists | Right slot for INTAKE, SOCRATES teach |
| `model_slot` evaluation | ✅ Exists | Right slot for EXAMINER, PLACER |
| SM-2 (`sm2.py`) | ✅ Built | Extended in B.5 with BKT-inspired fields |
| `SessionContext` | ✅ Built | Extended with hint_count, quiz_type, review_concept_ids |
| `ActorResult.data` | ❌ Missing | error-as-payload pattern; Protocol change DEFINER decision #4 |
| Web search / fetch | ❌ Missing | Phase C gate |
| Voice / audio path | ❌ Missing | Browser Web Speech API is the zero-dep path |
| PREDICT step | ❌ Missing | Phase B.5 |
| Hint ladder | ❌ Missing | Phase B.5 |
| Error diagnosis in EVALUATE | ❌ Missing | Phase B.5 |
| Faded worked examples | ❌ Missing | Phase B.5 |
| Session interleaving | ❌ Missing | Phase B.5 |
| Transfer question type | ❌ Missing | Phase B.5 |
| Misconception log | ❌ Missing | Phase B.5 |
| Extended mastery model | ❌ Missing | Phase B.5 |
| Cold-start check | ❌ Missing | Phase B.5 |
| LearningPlan schema | ❌ Missing | Phase D |
| PlacementEvent schema | ❌ Missing | Phase D |
| IntakeSession schema | ❌ Missing | Phase D |
| PredictEvent schema | ❌ Missing | Phase B.5 |
| MisconceptionLog schema | ❌ Missing | Phase B.5 |
| INTAKE actor | ❌ Missing | Phase D |
| PLACER actor | ❌ Missing | Phase D |

---

*Commit to `docs/decisions/ADR-002-intake-placement-learning-plan.md`
in AIP_Aristotle. Do not implement before DEFINER review and approval.*
