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

---
## Research Annex — The Science of ARISTOTLE
*Amendment date: 2026-06-20 | Status: ACCEPTED*
*This annex documents the research foundation underlying every major
design decision in the ARISTOTLE architecture. It is written to serve
double duty: as formal ADR research backing, and as a manifesto for
eventual incorporation into ARISTOTLE product documentation for
educators, administrators, and curious users who want to understand
not just what ARISTOTLE does, but why.*
---
# The Science of ARISTOTLE
## Evidence-Based Adaptive Tutoring: A Manifesto

*This document accompanies ADR-001 (ARISTOTLE Architecture) as a
research annex and philosophical statement. It explains why ARISTOTLE
is built the way it is — tracing every major design decision to its
origin in learning science. It is written to be found by curious
educators, administrators, parents, and researchers who want to
understand not just what ARISTOTLE does, but why it does it that way.*

---

## In One Paragraph

ARISTOTLE is an adaptive tutoring system built on the research
literature of how human beings actually learn — not how we assume they
learn, and not how it is convenient to test them. Every design decision
maps to a documented finding with measured effect sizes: the generation
effect drives the PREDICT step; Bayesian knowledge tracing drives the
mastery model; the Zone of Proximal Development drives the hint ladder;
spaced retrieval drives the session scheduler; self-determination
theory drives the curiosity path. The result is not a quiz app with a
conversational interface. It is the closest approximation we know how
to build to the thing Benjamin Bloom proved in 1984 works best — a
personal tutor who knows exactly what you know, exactly where you are
stuck, and exactly how to move you forward.

---

# Part I — The Problem Worth Solving

In 1984, Benjamin Bloom published a study that should have changed
everything. He compared three modes of instruction: conventional
classroom teaching, mastery learning (self-paced with frequent
feedback), and one-on-one tutoring. The results were unambiguous.
Students who received individual tutoring performed, on average, two
standard deviations above students in conventional classrooms. Two
sigma. That is the difference between the 50th percentile and the
98th. A student of average ability, given a personal tutor, performs
as well as almost every student in the room taught conventionally.

Bloom called this the two-sigma problem. Not because it was
mysterious, but because it was obvious and unsolvable. Every education
system in history has known that tutoring works. No education system
has been able to provide it at scale. There are not enough tutors.
There is not enough time. The children who most need individualized
attention are the children least likely to receive it.

ARISTOTLE is a direct attempt to address the two-sigma problem — not
by simulating a tutor, but by implementing what tutors actually do.
The distinction matters. A tutor does not simply explain things more
clearly than a textbook. A tutor does several specific things that
classrooms cannot: they know where exactly you are confused, they do
not move on until you understand, they ask you questions rather than
testing you after the fact, they give you hints before giving you
answers, they bring back material you are about to forget, and they
follow your questions rather than forcing you through a preset
sequence. Each of these behaviors has a documented mechanism in
learning science. ARISTOTLE implements them.

---

# Part II — What ARISTOTLE Does and Why

## 1. The PREDICT Step: Making Wrong Answers Productive

Before ARISTOTLE teaches a concept, it asks the student to predict
it. "Before we go through Newton's First Law — what do you think it
says, in your own words?" Most students will be incomplete or
incorrect. This is intentional.

The research foundation is what cognitive scientists call the
**generation effect** (Slamecka & Graf, 1978). When people generate
information — even incorrectly — they form stronger memory traces
than when they passively receive the same information. The effort of
generation creates cognitive engagement that passive reading does not.
Being wrong first is not a problem to be avoided. It is a feature of
the learning process to be leveraged.

Kornell et al. (2009) extended this finding with the **pretesting
effect**: taking a test on material before instruction — even on
material not yet encountered — enhances learning of the subsequent
instruction. The student who tries to answer before being taught
encodes the teaching more durably than the student who receives the
teaching cold.

Robert Bjork (1994) frames this as one of several **desirable
difficulties** — conditions that feel harder during learning but
produce superior long-term retention. The prediction step is
counterintuitive by design. It should feel slightly uncomfortable.
That discomfort is the learning working.

When the student predicts, ARISTOTLE also gains something: a direct
view of the student's mental model before instruction. ARISTOTLE does
not teach against the textbook. It teaches against the specific gap
between what the student thinks and what is true.

## 2. Retrieval Practice: Testing as Learning, Not Measurement

After teaching, ARISTOTLE tests. This sounds obvious until you
understand what the research actually shows.

Most educational testing is measurement — it happens after learning
is supposed to have occurred, to find out whether it did. But Roediger
and Karpicke (2006) demonstrated something more surprising: the act
of retrieving information from memory is itself a powerful learning
event, stronger than re-studying the same material. This is the
**testing effect** or **retrieval practice effect**. It has been
replicated across ages, subjects, and contexts. It is one of the most
robust findings in cognitive science.

Karpicke and Blunt (2011) went further: students who studied a text
and then practiced retrieval retained more than students who spent the
same time creating concept maps, even though students themselves
predicted the opposite. People systematically underestimate how much
they learn from retrieval and overestimate how much they learn from
re-reading.

ARISTOTLE's session loop is built around this finding. The EXAMINE
actor does not test to measure. It tests because testing is how the
knowledge is consolidated. Every question ARISTOTLE asks the student
is not an assessment. It is an act of learning.

## 3. Spaced Repetition: Fighting the Forgetting Curve

Memory decays. Hermann Ebbinghaus documented this in 1885 with a
precision that has never been meaningfully overturned — his forgetting
curve describes the trajectory of memory loss with mathematical
regularity. What Ebbinghaus also found, and what most people forget,
is that memory can be interrupted in its decay by spaced review. Each
review resets and extends the retention period.

The spacing effect — studying the same material across distributed
sessions rather than in one massed session — is one of the most
replicated findings in memory research (Cepeda et al., 2006
meta-analysis: strong effect across learning types, ages, and
materials). Students who cram for an examination may perform well
immediately. Students whose practice is spaced retain the material for
months and years.

Piotr Wozniak's SM-2 algorithm (SuperMemo, 1987) operationalized the
spacing effect as a computable schedule: after each review, the
algorithm calculates the next optimal review date based on how well
the material was recalled. Easy recall → longer interval. Difficult
recall → shorter interval. The schedule adapts to the individual
learner's memory characteristics.

ARISTOTLE implements SM-2 as its session scheduler. The mastery table
tracks every concept's repetition history and next review date. The
session coordinator draws from the queue intelligently: concepts due
for review surface first; unstarted concepts surface next; mastered
concepts surface only at their scheduled review. The student never
forgets what they have learned because ARISTOTLE will not let them.

## 4. The Hint Ladder: Staying in the Zone of Proximal Development

ARISTOTLE does not give answers. It gives hints — graduated, in
sequence, each one closer to the answer than the last — and only
provides the correct response after the student has worked through the
full ladder.

This is not pedagogical stubbornness. It is the direct implementation
of Lev Vygotsky's **Zone of Proximal Development** (1978). Vygotsky
identified the zone between what a learner can do independently and
what they can do with guidance as the productive space where learning
actually happens. Instruction pitched below the zone is boring.
Instruction pitched above it is overwhelming. The zone is where the
student is stretched without being broken.

Wood, Bruner, and Ross (1976) formalized this as **scaffolding** —
temporary support structures that enable the learner to accomplish
tasks beyond their current independent capability. The critical word
is temporary: scaffolds are meant to be removed as competence
develops. A tutor who gives answers is not scaffolding — they are
bypassing the zone entirely.

VanLehn's (2011) meta-analysis of intelligent tutoring systems found
that systems which provide hints on demand consistently outperform
systems that supply answers immediately. The hint ladder keeps the
student in the productive zone longer than immediate correction.
ARISTOTLE's four-rung ladder — orientation, conceptual, procedural,
near-answer — is designed to maintain exactly this gradient.

## 5. Faded Worked Examples: From Guided to Independent

When a student encounters a new type of problem, fully worked
examples are optimal. When a student is becoming competent, worked
examples are counterproductive — they substitute the tutor's thinking
for the student's.

This tension is resolved by **fading**. John Sweller's worked example
effect (Sweller & Cooper, 1985) established that studying worked
solutions reduces the extraneous cognitive load that problem-solving
imposes, freeing working memory for schema construction. But Renkl
(2014) synthesized decades of subsequent research to show that the
optimal instructional sequence fades the worked example progressively:
fully worked → partially completed → student-generated. The student
transitions from observer to independent problem-solver across
successive encounters with the same concept type.

Sweller's Cognitive Load Theory (1988) provides the underlying
mechanism: working memory has severe capacity limits. Extraneous
load — cognitive effort spent managing the task environment rather
than learning — consumes this limited capacity. Worked examples
reduce extraneous load in early stages of learning. As competence
develops, the worked example itself becomes unnecessary scaffolding
that should be faded.

ARISTOTLE's SOCRATES actor begins every concept with a fully worked
example. Subsequent encounters present the same problem type with
progressively larger blanks that the student must complete. The
student experiences the same concept multiple times, each time doing
more of the cognitive work independently.

## 6. Error Diagnosis: The Misconception Behind the Wrong Answer

When a student answers incorrectly, ARISTOTLE does not simply mark it
wrong and provide the correct answer. It identifies the specific
misconception the wrong answer reveals.

This distinction — between a wrong answer and the reasoning that
produced it — is central to what effective tutors do and what most
assessment systems do not. Koedinger and Corbett (2006) formalized
this as **knowledge component modeling**: student errors are not
random, they are systematic. They reflect underlying misconceptions
that can be diagnosed and targeted specifically.

Chi et al. (1994) demonstrated the **self-explanation effect**: when
students are prompted to explain errors in their own words, they learn
more than when they simply receive the correct answer. The act of
identifying why the wrong answer seemed right, and why it is wrong,
does more cognitive work than mere correction.

Carnegie Learning MATHia — the most extensively validated AI tutoring
system in deployment — is built on model-tracing: step-level diagnosis
of student reasoning that identifies which specific knowledge component
failed, not just that the final answer was incorrect. Pane et al.
(2014) found significant positive effects on algebra achievement
attributable in part to this diagnostic precision.

ARISTOTLE's EXAMINER actor diagnoses misconception type from wrong
answers. The misconception log records each error with its conceptual
source. The MENTOR actor synthesizes these logs into a plain-language
struggle pattern — a description of the student's characteristic error
tendencies. What looks like confusion about one concept often reveals
a systematic gap in foundational understanding. The struggle pattern
makes that gap visible to both the student and the teacher.

## 7. Session Interleaving: The Counter-Intuitive Schedule

Intuition says: finish one topic before moving to another. Research
says otherwise.

Kornell and Bjork (2008) demonstrated the **interleaving effect**:
mixing different problem types or concepts within a study session
produces better long-term retention and transfer than blocked
practice — completing all of one type before moving to the next —
despite feeling harder and less productive during the session itself.
Rohrer et al. (2020) confirmed this finding in mathematics classroom
settings, where interleaved practice significantly outperformed
blocked practice on delayed tests.

The mechanism is discrimination: interleaving forces the student to
identify which strategy or concept applies to each problem, not just
execute a strategy they are currently primed to use. This more
effortful process — another desirable difficulty — produces superior
transfer to new problems.

ARISTOTLE's session coordinator selects concepts from the SM-2 queue
across multiple topics rather than exhausting one concept before
introducing the next. A session might move between Newton's three
laws rather than completing all repetitions of the first before
touching the second. This feels less efficient. It produces better
learning.

## 8. Transfer Questions: The Test of Real Understanding

Mastery of a concept in its original form is not the same as
understanding it. The student who can recite Newton's First Law is
not the same as the student who can recognize when it applies to a
spinning ice skater, or a satellite in orbit, or a ball rolling on a
frictionless surface. This distinction — between recognition and
transfer — is what separates understanding from memorization.

**Transfer-appropriate processing** (Morris et al., 1977) holds that
memory is most accessible when retrieval conditions match encoding
conditions. The implication for instruction: if students only ever
see a concept in one context, they will only recognize it in that
context. Varied practice across contexts produces the flexible
representation that transfers.

Once a student reaches initial mastery of a concept, ARISTOTLE
introduces transfer questions — the same principle applied to a novel
context the student has not previously encountered. The student who
answers a transfer question correctly has demonstrated understanding,
not just memorization. The student who cannot transfer has revealed
where the understanding is still shallow.

## 9. The Mastery Model: Knowing What You Do Not Know

ARISTOTLE tracks mastery probabilistically, not as a simple
percentage correct. The distinction reflects a fundamental challenge
in education: the student who answers a question correctly may know
the material, or may have guessed, or may have slipped from a concept
they actually know. Single data points are noisy. Patterns across
multiple encounters are not.

**Bayesian Knowledge Tracing** (Corbett & Anderson, 1994) models
student knowledge as a hidden state, estimated from observable
performance using four parameters: the probability the student knew
the concept before instruction, the probability of learning from each
practice opportunity, the probability of a correct answer despite not
knowing (guess), and the probability of an incorrect answer despite
knowing (slip). BKT is the knowledge model that underlies Carnegie
Learning and a generation of ITS research.

The mastery model ensures that a student who answers correctly three
times in a row has not necessarily mastered a concept — particularly
if their history shows frequent slips. And a student who answers
incorrectly does not necessarily lack understanding — a single error
in an otherwise consistent history may be a slip rather than a gap.
ARISTOTLE makes this distinction in real time. The student progresses
when the system is confident they have learned, not simply when they
have answered.

## 10. MENTOR and the Struggle Pattern: Metacognition Made Visible

The MENTOR actor watches every session. After three or more
misconceptions have accumulated, MENTOR synthesizes them into a
plain-language description of the student's characteristic error
pattern — what is sometimes called a **struggle pattern**.

The research foundation is metacognition (Flavell, 1979) — the
capacity to think about one's own thinking. Zimmerman (2002) showed
that **self-regulated learners** — those who monitor their own
understanding, identify gaps, and adjust their approach — consistently
outperform students who do not engage in this monitoring. The
challenge is that metacognition is difficult to develop and even
harder to scaffold.

Koedinger et al. (2012) demonstrated that making the student model
visible to the student improves both learning outcomes and motivation.
When students can see not just their scores but the pattern of their
errors, they develop a more accurate model of their own understanding.

ARISTOTLE's struggle pattern serves two functions: it gives the
teacher (Komal) a diagnostic view of what the student needs most, and
it gives the student a vocabulary for understanding their own
difficulties. The teacher dashboard surfaces struggle patterns
alongside mastery data precisely because a teacher armed with this
information can intervene with precision rather than assumption.

## 11. The Curiosity Path: The Student Holds the Wheel

ARISTOTLE does not enforce its learning plan. The plan is a
navigational suggestion. When the student asks a question — when
curiosity takes them somewhere the plan did not anticipate — ARISTOTLE
follows.

This design decision rests on **Self-Determination Theory** (Deci and
Ryan, 1985, 2000), one of the most extensively validated frameworks
in motivational psychology. Deci and Ryan identify three basic
psychological needs: autonomy (the sense of volition and self-direction),
competence (the sense of effectiveness and mastery), and relatedness
(the sense of connection). Educational environments that support
autonomy produce deeper learning, greater persistence, and better
transfer. Environments that thwart autonomy produce compliance without
understanding.

Renninger and Hidi (2016) documented the relationship between
individual interest and learning: when a student pursues something they
find genuinely interesting, they engage more deeply, persist longer,
and retain more. Interest activates attention, effort, and memory in
ways that externally imposed curriculum does not reliably produce.

The practical implementation is an intent classifier that reads every
student message before routing it through the session logic. If the
student is answering ARISTOTLE's question (ANSWER), the structured
path continues. If the student is asking their own question (QUESTION
or TANGENT), ARISTOTLE answers fully — not with a redirect, not with
"let's get back to what we were doing." A full answer. Then a soft
offer: "Want to keep exploring this, or shall we continue where we
left off?" The student decides.

This is not permissiveness. It is the recognition that Vygotsky's
Zone of Proximal Development is most productively located when the
student identifies it. A question the student generates is a
self-identification of their proximal zone. The most efficient tutoring
follows that question.

## 12. The One-Voice Principle: Reducing Cognitive Overhead

The student who uses ARISTOTLE meets one character: ARISTOTLE.
Behind this character, four internal actors orchestrate the session —
SOCRATES teaches, EXAMINER tests, MENTOR tracks patterns, HERALD
(in a future phase) connects concepts to the wider world. The student
sees none of this. They experience only ARISTOTLE.

This is not a user experience decision. It is a cognitive load
decision.

Sweller's Cognitive Load Theory (1988) identifies three types of
cognitive load: intrinsic (the inherent complexity of the material),
germane (the cognitive effort that builds understanding), and
extraneous (cognitive effort caused by poor design — effort that
contributes nothing to learning). Every irrelevant element in a
learning environment consumes working memory that should be directed
at the subject matter. Interface complexity is extraneous load.
Switching between different named tutors or modes is extraneous load.
Explaining what each actor does is extraneous load.

A consistent, single persona reduces cognitive overhead and allows the
student to focus on content. The complexity of the system is hidden
precisely so that the student's limited working memory is entirely
available for Newton's laws.

---

# Part III — The Systems That Informed ARISTOTLE

ARISTOTLE did not emerge from first principles. It emerged from a
systematic survey of existing AI tutoring systems and the learning
science literature, conducted before any code was written.

**Khanmigo (Khan Academy, 2023–):** The most widely deployed
AI tutoring system in the world. Khanmigo's central design principle
— never give the answer, always guide through questions — is a direct
implementation of Socratic method. Its documented impact on student
agency and persistence shaped ARISTOTLE's hint ladder design.

**Carnegie Learning MATHia:** The most extensively validated AI
tutoring system in deployment. Pane et al. (2014) documented
significant positive effects on algebra achievement. MATHia's
model-tracing architecture — step-level diagnosis of student reasoning
— is the inspiration for ARISTOTLE's error diagnosis and misconception
logging. Where MATHia is domain-specific and institution-grade,
ARISTOTLE attempts to generalize the same architecture to any subject
a teacher can load.

**Duolingo Max:** Streak mechanics, personalized error analysis, and
the "explain my answer" feature reflect learning science principles
in a consumer product. Duolingo's Birdbrain system predicts the
optimal next lesson for each learner. ARISTOTLE adopts the error
diagnosis principle and the personalized sequencing philosophy.

**Harvard RCT (Kestin et al., 2025):** A randomized controlled
trial comparing AI-assisted tutoring to traditional lecture instruction
in undergraduate physics. The AI tutoring condition produced
approximately twice the learning gains of conventional instruction.
The study confirmed that AI tutors can approach the effectiveness of
human tutors when they implement active learning principles — retrieval
practice, elaborative questioning, immediate feedback — rather than
simply providing information.

---

# Part IV — The Freedom Generation Context

Every design decision in ARISTOTLE was made in the knowledge that its
first users would be students at Freedom Generation Charity School in
Faisalabad, Pakistan — a school whose students would otherwise have
no access to individualized instruction of any kind.

The two-sigma problem, in this context, is not an abstraction. It is
the gap between what Ramesh could become with a tutor and what he could
become without one. It is the difference between Sameer mastering the
health informatics systems that could define his career, and Sameer
struggling through textbooks alone with no one to ask.

ARISTOTLE's bilingual operation (English/Urdu) is itself a cognitive
load intervention. Instruction delivered in a non-native language
imposes extraneous cognitive load — the student expends working memory
on language processing that should be available for the content.
When ARISTOTLE responds in the language the student finds most
natural for a given concept, it reduces that overhead.

The teacher dashboard — Komal's dashboard — exists because individual
student data has no value if it remains invisible to the person who
can act on it. The struggle pattern, the mastery table, the session
history: these are not metrics for reporting. They are instruments
for a teacher who cares about every student and needs to know where
each one actually stands.

ARISTOTLE was designed for students without tutors, in a school
without abundant resources, in a language that reflects where they
live. The research behind it was accumulated in universities and labs
with abundant resources. The synthesis is the point.

---

# Part V — Research Annex

*Formal citation structure for ADR integration and academic reference.*

## Core Claims and Primary Citations

**The two-sigma problem**
Bloom, B.S. (1984). The 2 sigma problem: The search for methods of
group instruction as effective as one-to-one tutoring. *Educational
Researcher, 13*(6), 4–16.

**Generation effect / PREDICT step**
Slamecka, N.J., & Graf, P. (1978). The generation effect: Delineation
of a phenomenon. *Journal of Experimental Psychology: Human Learning
and Memory, 4*(6), 592–604.
Kornell, N., Hays, M.J., & Bjork, R.A. (2009). Unsuccessful retrieval
attempts enhance subsequent learning. *Journal of Experimental
Psychology: Learning, Memory, and Cognition, 35*(4), 989–998.

**Desirable difficulties**
Bjork, R.A. (1994). Memory and metamemory considerations in the
training of human beings. In J. Metcalfe & A. Shimamura (Eds.),
*Metacognition: Knowing About Knowing* (pp. 185–205). MIT Press.

**Retrieval practice / testing effect**
Roediger, H.L., & Karpicke, J.D. (2006). Test-enhanced learning:
Taking memory tests improves long-term retention. *Psychological
Science, 17*(3), 249–255.
Karpicke, J.D., & Blunt, J.R. (2011). Retrieval practice produces
more learning than elaborative studying with concept mapping.
*Science, 331*(6018), 772–775.

**Spacing effect**
Ebbinghaus, H. (1885/1913). *Memory: A Contribution to Experimental
Psychology.* Columbia University Press.
Cepeda, N.J., Pashler, H., Vul, E., Wixted, J.T., & Rohrer, D.
(2006). Distributed practice in verbal recall tasks: A review and
quantitative synthesis. *Psychological Bulletin, 132*(3), 354–380.
Wozniak, P.A. (1987). SuperMemo — SM-2 algorithm. [Technical
specification.] SuperMemo World.

**Zone of Proximal Development / scaffolding**
Vygotsky, L.S. (1978). *Mind in Society: The Development of Higher
Psychological Processes.* Harvard University Press.
Wood, D., Bruner, J.S., & Ross, G. (1976). The role of tutoring in
problem solving. *Journal of Child Psychology and Psychiatry, 17*(2),
89–100.

**Worked examples / faded examples / cognitive load**
Sweller, J., & Cooper, G.A. (1985). The use of worked examples as a
substitute for problem solving in learning algebra. *Cognition and
Instruction, 2*(1), 59–89.
Sweller, J. (1988). Cognitive load during problem solving: Effects on
learning. *Cognitive Science, 12*(2), 257–285.
van Merriënboer, J.J.G., & Sweller, J. (2005). Cognitive load theory
and complex learning: Recent developments and future directions.
*Educational Psychology Review, 17*(2), 147–177.
Renkl, A. (2014). Toward an instructionally oriented theory of
example-based learning. *Cognitive Science, 38*(1), 1–37.

**Error diagnosis / knowledge component models**
Chi, M.T.H., de Leeuw, N., Chiu, M., & LaVancher, C. (1994).
Eliciting self-explanations improves understanding. *Cognitive
Science, 18*(3), 439–477.
Koedinger, K.R., & Corbett, A. (2006). Cognitive tutors: Technology
bringing learning science to the classroom. In R.K. Sawyer (Ed.),
*The Cambridge Handbook of the Learning Sciences* (pp. 61–78).

**Interleaving**
Kornell, N., & Bjork, R.A. (2008). Learning concepts and categories:
Is spacing the "enemy of induction"? *Psychological Science, 19*(6),
585–592.
Rohrer, D., Dedrick, R.F., Hartwig, M.K., & Cheung, C. (2020). A
randomized controlled trial of interleaved mathematics practice.
*Journal of Educational Psychology, 112*(1), 40–52.

**Transfer-appropriate processing**
Morris, C.D., Bransford, J.D., & Franks, J.J. (1977). Levels of
processing versus transfer appropriate processing. *Journal of Verbal
Learning and Verbal Behavior, 16*(5), 519–533.

**Bayesian Knowledge Tracing**
Corbett, A.T., & Anderson, J.R. (1994). Knowledge tracing: Modeling
the acquisition of procedural knowledge. *User Modeling and
User-Adapted Interaction, 4*(4), 253–278.
Piech, C., Spencer, J., Huang, J., Ganguli, S., Sahami, M.,
Guibas, L., & Koller, D. (2015). Deep knowledge tracing. In *Advances
in Neural Information Processing Systems 28* (pp. 505–513).

**Metacognition / self-regulated learning**
Flavell, J.H. (1979). Metacognition and cognitive monitoring: A new
area of cognitive–developmental inquiry. *American Psychologist,
34*(10), 906–911.
Zimmerman, B.J. (2002). Becoming a self-regulated learner: An
overview. *Theory into Practice, 41*(2), 64–70.

**Open learner models**
Koedinger, K.R., Kim, J., Jia, J.Z., McLaughlin, E.A., & Bier,
N.L. (2012). Learning is not a spectator sport: Doing is better
than watching for learning from a MOOC. In *Proceedings of the 2nd
ACM Conference on Learning @ Scale.*

**Self-Determination Theory / curiosity and motivation**
Deci, E.L., & Ryan, R.M. (1985). *Intrinsic Motivation and
Self-Determination in Human Behavior.* Plenum.
Deci, E.L., & Ryan, R.M. (2000). The "what" and "why" of goal
pursuits: Human needs and the self-determination of behavior.
*Psychological Inquiry, 11*(4), 227–268.
Renninger, K.A., & Hidi, S. (2016). *The Power of Interest for
Motivation and Engagement.* Routledge.

**ITS effectiveness**
VanLehn, K. (2011). The relative effectiveness of human tutoring,
intelligent tutoring systems, and other tutoring systems.
*Educational Psychologist, 46*(4), 197–221.
Ma, W., Adesope, O.O., Nesbit, J.C., & Liu, Q. (2014). Intelligent
tutoring systems and learning outcomes: A meta-analysis. *Journal of
Educational Psychology, 106*(4), 901–918.

**Carnegie Learning field validation**
Pane, J.F., Griffin, B.A., McCaffrey, D.F., & Katar, R. (2014).
Effectiveness of cognitive tutor algebra I at scale. *Educational
Evaluation and Policy Analysis, 36*(2), 127–144.

**AI tutoring RCT**
Kestin, G., Miller, K., McCarty, L.S., Callaghan, K., & Deslauriers,
L. (2025). AI tutoring outperforms active learning in an
introductory physics course. *[Nature Human Behaviour / Science,
pending citation verification].* Harvard University.
