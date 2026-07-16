# Task 21 Investigation — TEACH-step rendering in `gui/pages/ask.py`

**Status:** Confirmed bug (in `aristotle/api.py`, surfaced via `ask.py`)
**Task:** Task 21 (investigation-only item — no code changes per the prompt)
**Investigator:** Super Z (main agent)
**Date:** 2026-07-16
**Commits reviewed:** AIP_Aristotle `05a489a` (main), AIP_Brain `5995397` (feat/multi-corpus)

---

## Summary

**Confirmed bug.** A TEACH-step API response is currently dropped from the
chat display. The root cause is in `aristotle/api.py::session_step_route`,
not directly in `gui/pages/ask.py` — but `ask.py` faithfully renders
whatever the API returns, and the API returns an empty `output` string for
TEACH (also PROBE and QUIZ) steps. Only the PREDICT step's `data.prompt`
field is read by the API's `output` computation.

The user's hypothesis ("`ask.py`'s render logic has no branch for
`explanation`") is correct in spirit but slightly mis-located: `ask.py`
doesn't branch on `explanation` because it doesn't branch on the step type
at all — it reads a single `output` key from the API response, and the API
fails to populate that key for any step whose actor result doesn't put
its text under `data.prompt`.

---

## Exact code path

### Producer side — `aristotle/session.py::_step_teach()` (lines 949-980)

```python
async def _step_teach(ctx, session, socrates) -> ActorResult:
    ...
    result = await socrates.teach(ctx, session.concept_id, mastery_level=mastery_level)
    if result.ok:
        if result.data is not None and isinstance(result.data, dict):
            session.last_explanation = result.data.get("explanation", "")
        else:
            session.last_explanation = result.error or ""
        session.state = SessionState.PROBE
        ...
    return result  # <-- returns the socrates.teach() result directly
```

The returned `ActorResult.data` is `{"explanation": "<text>", "fading_mode": "<mode>"}` —
no `prompt` key. The session coordinator updates `session.last_explanation`
but does NOT rewrite `result.data` to add a `prompt` key.

### Producer side — `aristotle/session.py::_step_probe()` (lines 983-1015)

Same pattern. Returns the `examiner.probe()` result with
`data = {"question": "<text>", "question_type": "probe"}` — no `prompt` key.

### Producer side — `aristotle/session.py::_step_quiz()` (lines 1018+)

Same pattern. `data = {"question": "<text>", "question_type": "..."}` —
no `prompt` key.

### Producer side — `aristotle/session.py::_step_predict()` (lines 496+)

This one DOES use `data.prompt`:
```python
data={"prompt": prompt}  # SocratesActor.predict() returns this
```

So PREDICT is the only tutoring step whose result has a `prompt` key.

### API layer — `aristotle/api.py::session_step_route()` (lines 237-262)

```python
@router.post("/session/step")
async def session_step_route(request: Request):
    ...
    result = await run_session_step(ctx, session, student_input)
    return {
        "session": _session_to_dict(session),
        "output": (
            result.error
            or (result.data.get("prompt", "") if isinstance(result.data, dict) else "")
            or ""
        ) if result.ok else "",
        "ok": result.ok,
        "error": result.error if not result.ok else None,
    }
```

The `output` field is computed as:
1. `result.error` (Phase B.5 migration left this in for backward compat — but
   for migrated actors like `teach()`, `result.error` is `None`/empty on success)
2. OR `result.data.get("prompt", "")` — **only reads the `prompt` key**
3. OR `""`

For a TEACH step: `result.error` is empty, `result.data = {"explanation": "...", "fading_mode": "..."}`,
`result.data.get("prompt", "")` returns `""`. So `output = ""`. **The TEACH
explanation is dropped.**

For a PROBE step: same — `data = {"question": "..."}`, `data.get("prompt", "")` returns `""`.
**The probe question is dropped.**

For a QUIZ step: same — **the quiz question is dropped.**

For a PREDICT step: `data = {"prompt": "..."}`, `data.get("prompt", "")` returns the prompt.
**Only PREDICT works.**

### Consumer side — `AIP_Brain/gui/pages/ask.py::_step_tutoring()` (lines 2079-2108)

```python
async def _step_tutoring(student_input: str) -> None:
    nonlocal _tutor_session
    try:
        async with httpx.AsyncClient(...) as client:
            resp = await client.post(
                "/aristotle/session/step",
                json={"session": _tutor_session, "student_input": student_input},
            )
            resp.raise_for_status()
            data = resp.json()
            _tutor_session = data.get("session", _tutor_session)
            output = data.get("output", "")
            if output:
                await _render_aristotle_message(output)
            ...
```

`ask.py` reads `data.get("output", "")` and renders it via
`_render_aristotle_message`. If `output` is empty (which it is for TEACH,
PROBE, QUIZ), nothing is rendered. A grep of `ask.py` for `last_explanation`,
`last_probe_question`, `last_quiz_question`, `fading_mode` returns nothing —
`ask.py` does NOT read those session fields as a workaround.

---

## Why this hasn't been noticed before

The dogfood session that surfaced Task 21 logged `socrates_teach_ok concept=pharmacognosy_000 ... fading=full_worked_example explanation_len=4950` —
the LLM call succeeded with a ~5000-character explanation. The user's
complaint in Fix 4 is that the explanation is too long, not that it's
invisible. Two plausible explanations for why the invisibility wasn't flagged:

1. The user may have been reading the backend logs (which show the LLM
   call succeeding) rather than the chat UI (which shows nothing). The
   Fix 4 prompt's evidence is a log line, not a UI screenshot.
2. The PREDICT step's prompt DOES reach the UI (it uses `data.prompt`).
   The user may have assumed the subsequent TEACH output was also
   reaching the UI because the PREDICT prompt did.

Either way, the bug is real: a learner using the chat UI today sees the
PREDICT prompt, types a guess, and then... nothing. The TEACH explanation
is generated by the model, stored on `session.last_explanation`, but never
rendered. The learner's next message triggers PROBE, which is also dropped.
Then QUIZ, also dropped. The session appears broken from the learner's
perspective — they type, the model thinks, but nothing comes back.

---

## Proposed minimal fix (NOT implemented per Task 21 prompt)

The fix is a single expression change in `aristotle/api.py::session_step_route`
(lines 255-258). Extend the `output` computation to fall through the keys
that `_step_*` functions actually use:

```python
"output": (
    result.error
    or (
        # Fall through the data keys that _step_* functions actually use.
        # _step_predict → data.prompt (the only one the original code read).
        # _step_teach  → data.explanation
        # _step_probe  → data.question
        # _step_quiz   → data.question
        # _step_evaluate → data.feedback (the learner-facing message;
        #   score/diagnosis are consumed separately by the coordinator)
        result.data.get("prompt")
        or result.data.get("explanation")
        or result.data.get("question")
        or result.data.get("feedback")
        or ""
    ) if isinstance(result.data, dict) else ""
) if result.ok else "",
```

This is the minimal fix — it doesn't touch the actors, the session
coordinator, or `ask.py`. It just makes the API flatten whatever output
key the actor used into the `output` string the GUI already reads.

### Why this is the right shape

- **No actor changes.** The actors (`socrates.teach`, `examiner.probe`,
  `examiner.quiz`, `examiner.evaluate`) all use semantically-named keys
  (`explanation`, `question`, `feedback`) on `ActorResult.data`. This is
  correct — the keys describe what the content IS, not how the GUI should
  render it.
- **No session-coordinator changes.** `_step_teach`, `_step_probe`,
  `_step_quiz` correctly store the content on session fields
  (`last_explanation`, `last_probe_question`, `last_quiz_question`) for
  later retrieval (e.g. by `_step_evaluate` reading the quiz question).
  They shouldn't rewrite `result.data` to add a `prompt` key — that would
  conflate the actor's contract with the GUI's rendering.
- **No `ask.py` changes.** `ask.py`'s `_step_tutoring` already does the
  right thing: read `output`, render it. The bug is that the API doesn't
  populate `output` correctly.
- **One expression, one place.** The API is the boundary between the
  actor/session layer (semantically-named keys) and the GUI layer
  (a single `output` string). The translation belongs at the boundary.

### What this fix does NOT do

- It doesn't render the EVALUATE diagnosis (misconception / why_wrong /
  corrective) — those are separate fields on `data.diagnosis`, not on
  `data.feedback`. The session coordinator currently consumes them for
  REMEDIATE routing, but they may also be valuable to show the learner
  directly. That's a separate design decision (do we want to surface the
  diagnosis in the chat, or only use it internally to drive the next
  step?) and should not be bundled into this minimal fix.
- It doesn't address Fix 4's length ceiling. Fix 4 is a separate change
  to `socrates.py::_build_system_prompt` (implemented in this same task)
  that bounds the explanation length at the prompt level. This fix just
  makes the (now-shorter) explanation actually reach the learner.

---

## Verification (what to check after implementing the fix)

1. **TEACH step:** start a tutoring session, advance to TEACH, verify the
   explanation text appears in the chat UI (not just in the backend log).
2. **PROBE step:** verify the probe question appears.
3. **QUIZ step:** verify the quiz question appears.
4. **PREDICT step:** verify the predict prompt still appears (regression
   check — the existing `data.prompt` path must still work).
5. **EVALUATE step:** verify the feedback appears. (The diagnosis is
   consumed internally — verify REMEDIATE still triggers correctly when
   `mastery_achieved=False`.)

The existing test `tests/test_aristotle_tutoring.py::TestSocratesTeach::test_teach_calls_beast_slot`
verifies the actor returns `data.explanation` but does NOT verify the API
flattens it to `output`. A new test in `tests/test_aristotle_routes.py`
(or similar) should assert that `POST /session/step` for a TEACH step
returns `output` containing the explanation text.

---

## Cross-references

- `aristotle/api.py:237-262` — the buggy `session_step_route`
- `aristotle/session.py:949-980` — `_step_teach` (returns socrates.teach() result with `data.explanation`)
- `aristotle/session.py:983-1015` — `_step_probe` (returns examiner.probe() result with `data.question`)
- `aristotle/session.py:1018+` — `_step_quiz` (returns examiner.quiz() result with `data.question`)
- `aristotle/session.py:496+` — `_step_predict` (the only one that uses `data.prompt`)
- `AIP_Brain/gui/pages/ask.py:2079-2108` — `_step_tutoring` (reads `data.output`)
- `AIP_Brain/gui/pages/ask.py:1552+` — `_ask_page_aristotle` (the function the Task 21 prompt named)
- `aristotle/actors/socrates.py` — `SocratesActor.teach()` returns `data={"explanation": ..., "fading_mode": ...}`
- `aristotle/actors/examiner.py` — `ExaminerActor.probe()/quiz()` return `data={"question": ..., "question_type": ...}`

---

## Recommendation

Implement the proposed minimal fix in a follow-up task (not Task 21 —
the Task 21 prompt explicitly says "do not change code for this one").
The fix is one expression in `aristotle/api.py::session_step_route` plus
one regression test. Until then, the chat UI shows only PREDICT prompts
and the session-complete end message; TEACH/PROBE/QUIZ/EVALUATE outputs
are silently dropped.
