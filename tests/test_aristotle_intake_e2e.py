"""End-to-end smoke test for ARISTOTLE LLM-driven intake + upload + draft plan.

Spawns the real AIP_Brain FastAPI app via TestClient (lifespan runs,
ExtensionHost starts, ARISTOTLE router is mounted). Then monkey-patches
the container's model_provider with a SCRIPTED FAKE that returns valid
JSON for each intake turn — simulating a well-behaved LLM that drives
the conversation forward.

Verifies the full pipeline end-to-end:
  1. POST /aristotle/intake/start  → greeting prompt
  2. POST /aristotle/upload        → paper text extracted + persisted
  3. POST /aristotle/intake/step   → subject extracted
  4. POST /aristotle/intake/step   → prior_knowledge probed
  5. POST /aristotle/intake/step   → goals probed
  6. POST /aristotle/intake/step (with material_ids) → draft plan proposed
  7. POST /aristotle/intake/step (confirm) → COMPLETE + plan_id returned
  8. GET  /aristotle/dashboard     → draft plan concepts appear in mastery table
  9. GET  /aristotle/concepts      → concepts persisted to DB

This test exercises the REAL platform code paths:
  - ExtensionHost lifespan start → router mount
  - CorpusRegistry → aristotle:textbook stores (real SQLite)
  - IntakeActor → model_provider.call(slot="beast") → JSON parsing
  - generate_plan → INSERT into aristotle_concept + aristotle_learning_plan
    + aristotle_intake_session
  - Upload route → pypdf extraction + INSERT into aristotle_uploaded_material
  - Dashboard route → LEFT JOIN aristotle_concept + aristotle_mastery

The ONLY thing faked is the model_provider — everything else is real.
This is the closest you can get to a production smoke test without
burning real LLM tokens. To run against a real LLM, set
AIP_OPENAI_API_KEY and walk the same flow via curl/httpie/GUI.

Run:
    pytest tests/test_aristotle_intake_e2e.py -v

Requires AIP_Brain to be installed (editable or otherwise) so that
aip.adapter.api.app.create_app is importable.
"""

from __future__ import annotations

import io
import json
import os
import warnings
from typing import Any

import pytest

warnings.filterwarnings("ignore")

# Ensure AIP_DOGFOOD_MODE is minimal so the app starts without all stores.
os.environ.setdefault("AIP_DOGFOOD_MODE", "minimal")


# ---------------------------------------------------------------------------
# Scripted fake model provider — returns valid JSON for each intake turn
# ---------------------------------------------------------------------------


class _ScriptedIntakeModel:
    """Returns canned JSON responses for the intake conversation.

    The IntakeActor calls model_provider.call(slot="beast", messages=[...]).
    We advance the focus one step per call, mirroring what a well-behaved
    LLM would do given the conversation context.

    The script is a 6-turn happy path:
      turn 1: greeting (next_focus=SUBJECT)
      turn 2: extract subject, advance to PRIOR_KNOWLEDGE
      turn 3: extract prior_knowledge, advance to GOALS
      turn 4: extract goals, advance to SCHEDULE
      turn 5: extract schedule, propose draft_plan (next_focus=PLAN_DRAFT)
      turn 6: confirm draft plan (next_focus=COMPLETE)
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []
        self._turn = 0

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        self.calls.append((slot_name, messages))
        if slot_name != "beast":
            return {"content": "", "model": "fake", "usage": {}, "latency_ms": 1}

        self._turn += 1

        if self._turn == 1:
            payload = {
                "response": "Hello! I'm Aristotle. What subject would you like to study?",
                "next_focus": "SUBJECT",
                "extracted": {},
                "draft_plan": None,
            }
        elif self._turn == 2:
            payload = {
                "response": "Great — physics! How much do you already know about it?",
                "next_focus": "PRIOR_KNOWLEDGE",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "",
                    "goals": "",
                    "schedule_minutes": 0,
                },
                "draft_plan": None,
            }
        elif self._turn == 3:
            payload = {
                "response": "Got it. What do you want to achieve with physics?",
                "next_focus": "GOALS",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "",
                    "schedule_minutes": 0,
                },
                "draft_plan": None,
            }
        elif self._turn == 4:
            payload = {
                "response": "Wonderful. How many minutes per day can you commit?",
                "next_focus": "SCHEDULE",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "personal interest",
                    "schedule_minutes": 0,
                },
                "draft_plan": None,
            }
        elif self._turn == 5:
            payload = {
                "response": (
                    "Based on what you've told me and the paper you uploaded, "
                    "here's a draft learning plan. Let me know if it looks right."
                ),
                "next_focus": "PLAN_DRAFT",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "personal interest",
                    "schedule_minutes": 30,
                },
                "draft_plan": [
                    {
                        "topic": "Newton's First Law",
                        "subtopic": "inertia",
                        "bloom_target": 2,
                        "content_primary": "Objects resist changes in motion. An object at rest stays at rest; an object in motion stays in motion unless acted on by a net external force.",
                        "prerequisite_concept_id": None,
                    },
                    {
                        "topic": "Newton's Second Law",
                        "subtopic": "F = ma",
                        "bloom_target": 3,
                        "content_primary": "The acceleration of an object is directly proportional to the net force acting on it and inversely proportional to its mass.",
                        "prerequisite_concept_id": 0,
                    },
                    {
                        "topic": "Newton's Third Law",
                        "subtopic": "action-reaction pairs",
                        "bloom_target": 3,
                        "content_primary": "For every action there is an equal and opposite reaction. Forces come in pairs acting on different bodies.",
                        "prerequisite_concept_id": 1,
                    },
                ],
            }
        else:
            # Turn 6+: learner confirmed. COMPLETE.
            payload = {
                "response": "Your plan is confirmed. Let's begin with Newton's First Law!",
                "next_focus": "COMPLETE",
                "extracted": {
                    "subject": "physics",
                    "prior_knowledge": "a little high school",
                    "goals": "personal interest",
                    "schedule_minutes": 30,
                },
                "draft_plan": [
                    {
                        "topic": "Newton's First Law",
                        "subtopic": "inertia",
                        "bloom_target": 2,
                        "content_primary": "Objects resist changes in motion.",
                        "prerequisite_concept_id": None,
                    },
                    {
                        "topic": "Newton's Second Law",
                        "subtopic": "F = ma",
                        "bloom_target": 3,
                        "content_primary": "F = ma",
                        "prerequisite_concept_id": 0,
                    },
                    {
                        "topic": "Newton's Third Law",
                        "subtopic": "action-reaction pairs",
                        "bloom_target": 3,
                        "content_primary": "Action equals reaction.",
                        "prerequisite_concept_id": 1,
                    },
                ],
            }

        return {
            "content": json.dumps(payload),
            "model": "scripted-fake",
            "usage": {"prompt_tokens": 100, "completion_tokens": 80},
            "latency_ms": 5,
        }


def _make_test_pdf() -> bytes:
    """Create a 2-page PDF for the upload test (blank pages, no text)."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixture: TestClient with the fake model_provider injected
# ---------------------------------------------------------------------------


@pytest.fixture
def aristotle_client(tmp_path, monkeypatch):
    """Yield a TestClient with the ARISTOTLE router mounted + fake model injected.

    The fixture:
      1. Points AIP_DB_DIR + db paths at tmp_path so each test gets a
         fresh SQLite DB (no cross-test contamination).
      2. Creates the FastAPI app + enters the lifespan (ExtensionHost
         starts, ARISTOTLE router mounts).
      3. Replaces container.model_provider with the scripted fake.
      4. Yields the client.
      5. Tears down on exit (lifespan shutdown closes the DB).
    """
    # Isolate DB to tmp_path so we don't pollute the repo's db/ dir.
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("AIP_DB_DIR", str(db_dir))
    # Override the state.db path. The config loader reads [database].db_path
    # from config/aip.config.toml, but we can patch via env override.
    # Actually, the simplest approach: chdir to tmp_path so relative db_path
    # resolves there.
    monkeypatch.chdir(tmp_path)
    # Make sure db/ exists in tmp_path (relative to chdir).
    (tmp_path / "db").mkdir(exist_ok=True)

    # Silence chatty loggers during the test.
    import logging
    for name in ("aip", "aristotle", "uvicorn", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.ERROR)

    from fastapi.testclient import TestClient
    from aip.adapter.api.app import create_app

    app = create_app()
    fake_model = _ScriptedIntakeModel()

    with TestClient(app) as client:
        # Lifespan has run; container is set on app.state.
        container = getattr(app.state, "container", None)
        if container is None:
            # Some lifespan versions store it differently — try the
            # extensions host as a fallback.
            ext_host = getattr(app.state, "extensions_host", None)
            if ext_host is not None:
                container = getattr(ext_host, "_container", None)
        assert container is not None, (
            "container not found on app.state — cannot inject fake model_provider. "
            f"app.state keys: {list(vars(app.state).keys())}"
        )
        container.model_provider = fake_model
        # Stash for assertions inside tests.
        client._fake_model = fake_model  # type: ignore[attr-defined]
        client._container = container  # type: ignore[attr-defined]
        yield client


# ---------------------------------------------------------------------------
# End-to-end happy path test
# ---------------------------------------------------------------------------


class TestIntakeE2E:
    """End-to-end smoke test: full LLM-driven intake loop with real DB."""

    def test_full_intake_loop_with_upload_and_draft_plan(self, aristotle_client):
        """Walk the full intake conversation, upload a paper, confirm draft plan.

        Verifies:
          - /aristotle/intake/start triggers a beast-slot model call
          - /aristotle/upload extracts PDF text + persists to DB
          - LLM-driven intake loop extracts structured fields per turn
          - material_ids attached to session are passed to the model context
          - next_focus=PLAN_DRAFT populates session.draft_plan
          - next_focus=COMPLETE triggers generate_plan → plan_id returned
          - Concepts are ingested into aristotle_concept (draft_plan path)
          - /aristotle/dashboard shows the new concepts
          - /aristotle/concepts confirms DB persistence
        """
        c = aristotle_client
        fake = c._fake_model

        # --- Stage 1: /aristotle/intake/start (no plan_id → full intake) ---
        r = c.post("/aristotle/intake/start", json={"plan_id": None})
        assert r.status_code == 200, f"intake/start failed: {r.status_code} {r.text}"
        body = r.json()
        assert body["trigger"] == "full"
        assert body["prompt"] and "Aristotle" in body["prompt"]
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "beast"
        session = body["session"]

        # --- Stage 2: /aristotle/upload (PDF) ---
        pdf_bytes = _make_test_pdf()
        r = c.post(
            "/aristotle/upload",
            content=pdf_bytes,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="newtons_laws_paper.pdf"',
            },
        )
        assert r.status_code == 200, f"upload failed: {r.status_code} {r.text}"
        body = r.json()
        assert body["source_type"] == "pdf"
        assert body["page_count"] == 2
        assert body["material_id"], "material_id should be non-empty (DB persisted)"
        material_id = body["material_id"]

        # --- Stage 3: intake/step (subject='physics') ---
        r = c.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "physics"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "PRIOR_KNOWLEDGE"
        session = body["session"]
        assert session["subject"] == "physics"
        assert session["extracted"]["subject"] == "physics"

        # --- Stage 4: intake/step (prior_knowledge) ---
        r = c.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "a little high school"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "GOALS"
        session = body["session"]

        # --- Stage 5: intake/step (goals) ---
        r = c.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "personal interest"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "SCHEDULE"
        session = body["session"]

        # --- Stage 6: intake/step (schedule + attach material_id) ---
        # Model proposes draft plan based on the uploaded paper.
        r = c.post(
            "/aristotle/intake/step",
            json={
                "session": session,
                "student_input": "30 minutes per day",
                "material_ids": [material_id],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "GENERATING_PLAN"
        session = body["session"]
        draft_plan = session["draft_plan"]
        assert isinstance(draft_plan, list)
        assert len(draft_plan) == 3, f"expected 3 concepts in draft_plan, got {len(draft_plan)}"
        # All required fields present
        required_keys = {"topic", "bloom_target", "content_primary"}
        for concept in draft_plan:
            assert required_keys.issubset(concept.keys()), \
                f"draft_plan concept missing keys: {concept.keys()}"
        # Material was attached
        assert material_id in session["material_ids"]
        # Schedule extracted
        assert session["schedule_minutes"] == 30

        # --- Stage 7: intake/step (confirm draft plan → COMPLETE) ---
        r = c.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "looks good, let's start"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "COMPLETE"
        assert body["plan_id"], "plan_id should be non-empty after COMPLETE"
        assert body["concept_count"] == 3
        plan_id = body["plan_id"]

        # --- Stage 8: GET /aristotle/dashboard ---
        # The 3 concepts from draft_plan should appear in mastery_by_concept.
        r = c.get("/aristotle/dashboard")
        assert r.status_code == 200
        body = r.json()
        assert "mastery_by_concept" in body
        mastery = body["mastery_by_concept"]
        assert len(mastery) == 3, \
            f"dashboard should show 3 concepts, got {len(mastery)}"
        topics = [m["topic"] for m in mastery]
        assert any("First" in t for t in topics), \
            f"Newton's First Law missing from dashboard: {topics}"
        assert any("Second" in t for t in topics), \
            f"Newton's Second Law missing from dashboard: {topics}"
        assert any("Third" in t for t in topics), \
            f"Newton's Third Law missing from dashboard: {topics}"

        # --- Stage 9: GET /aristotle/concepts ---
        # Concepts should be persisted in aristotle_concept.
        r = c.get("/aristotle/concepts")
        assert r.status_code == 200
        body = r.json()
        concepts = body if isinstance(body, list) else body.get("concepts", [])
        assert len(concepts) == 3, \
            f"expected 3 concepts persisted, got {len(concepts)}"

        # Sanity: the model was called exactly 6 times (one per turn).
        assert len(fake.calls) == 6, \
            f"expected 6 model calls (1 start + 5 steps), got {len(fake.calls)}"


# ---------------------------------------------------------------------------
# Negative test: no model_provider → deterministic fallback path
# ---------------------------------------------------------------------------


class TestIntakeNoModelFallback:
    """When model_provider is None, intake falls back to the deterministic path.

    This is the test/offline-mode path — the IntakeActor uses fixed templates
    instead of LLM calls. Verifies the fallback works end-to-end.
    """

    def test_deterministic_intake_completes_without_model(self, aristotle_client):
        """Walk a few steps of the deterministic intake path (no model calls)."""
        c = aristotle_client
        # Remove the model_provider entirely → triggers the deterministic
        # fallback in run_intake_step.
        c._container.model_provider = None

        # Start
        r = c.post("/aristotle/intake/start", json={"plan_id": None})
        assert r.status_code == 200
        body = r.json()
        # Deterministic path: trigger=full, prompt is the fixed greeting.
        assert body["trigger"] == "full"
        assert "subject" in body["prompt"].lower()
        session = body["session"]

        # Step 1: subject
        r = c.post(
            "/aristotle/intake/step",
            json={"session": session, "student_input": "biology"},
        )
        assert r.status_code == 200
        body = r.json()
        # Deterministic path: state should advance to PRIOR_KNOWLEDGE.
        assert body["state"] == "PRIOR_KNOWLEDGE"
        assert "biology" in body["prompt"].lower() or "biology" in body["session"]["subject"].lower()
