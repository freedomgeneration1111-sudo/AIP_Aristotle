"""Aristotle GUI — the learning view (ADR-001 §3).

The learning view is the learner's surface: a concept selector + a tutoring
session interface. The learner picks a concept, starts a session, and
interacts with Aristotle (the single voice) through the TEACH→PROBE→QUIZ→
EVALUATE→REMEDIATE loop.

This module is imported by gui/app.py in AIP_Brain (the platform's NiceGUI
entry point). The import registers the @ui.page("/learn") route. If
Aristotle isn't installed, the import fails silently and the /learn route
doesn't exist — the GUI degrades gracefully.

Layer: this module imports from nicegui (GUI framework) + httpx (HTTP
client to the backend API). No aip.* imports — the GUI is API-first.
"""

from __future__ import annotations

import logging
import os

import httpx
from nicegui import ui

from gui.components.layout import build_top_bar, build_left_nav
from gui.state import GuiState
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_RAISED,
    C_MUTED,
    F_SANS,
    F_MONO,
    R_SM,
    SP_MD,
    SP_SM,
)

log = logging.getLogger("gui.aristotle")

_BACKEND_URL = os.getenv("AIP_BACKEND_URL", "http://127.0.0.1:8000")


@ui.page("/learn")
def learn_page():
    """Aristotle learning view — concept selector + tutoring session.

    The page has two sections:
    1. Top: concept selector (dropdown of ingested concepts)
    2. Main: tutoring session display (explanation, questions, evaluation)

    The learner picks a concept, clicks "Start Session", and the tutoring
    loop runs step-by-step. Each step's output is displayed; the learner
    provides answers via a text input.
    """
    state = GuiState()
    state.active_page = "/learn"

    build_top_bar(state)
    build_left_nav(state, active_page="/learn")

    # Main content
    with ui.column().classes("w-full flex-1").style(f"padding:{SP_MD}; gap:{SP_MD};"):
        ui.label("Aristotle — Adaptive Tutor").style(
            f"font-family:{F_SANS}; font-size:24px; font-weight:700; color:{C_CREAM};"
        )
        ui.label("The student's only job is to show up.").style(
            f"font-family:{F_SANS}; font-size:14px; color:{C_MUTED}; font-style:italic;"
        )

        # Concept selector
        with ui.row().classes("w-full items-center").style(f"gap:{SP_SM};"):
            concept_select = ui.select(
                options=[],
                label="Select a concept",
                value=None,
            ).style("min-width:300px;")

            start_btn = ui.button(
                "Start Session", on_click=lambda: start_session(concept_select.value)
            )
            start_btn.style(
                f"background:{C_AMBER}; color:#0d1117; "
                f"font-family:{F_SANS}; font-weight:600; border-radius:{R_SM};"
            )

        # Session display area
        session_area = ui.column().classes("w-full").style(f"gap:{SP_SM};")
        session_area.clear()

        # Status line
        status_label = ui.label("").style(
            f"font-family:{F_MONO}; font-size:11px; color:{C_MUTED};"
        )

        async def start_session(concept_id: str):
            """Start a tutoring session for the selected concept."""
            if not concept_id:
                ui.notify("Please select a concept first", type="warning")
                return

            session_area.clear()
            status_label.text = f"Starting session for {concept_id}..."

            try:
                async with httpx.AsyncClient(
                    base_url=_BACKEND_URL, timeout=60.0
                ) as client:
                    # Start the session
                    resp = await client.post(
                        "/aristotle/session/start", json={"concept_id": concept_id}
                    )
                    resp.raise_for_status()
                    session = resp.json()

                    with session_area:
                        ui.label(f"Session: {concept_id}").style(
                            f"font-family:{F_SANS}; font-size:16px; font-weight:600; color:{C_CREAM};"
                        )

                        # Run the session step by step
                        await run_session_steps(client, session, session_area)

            except httpx.ConnectError:
                status_label.text = "ERROR: Cannot connect to backend."
                ui.notify("Backend not reachable", type="negative")
            except Exception as exc:
                status_label.text = f"ERROR: {exc}"
                ui.notify(f"Session error: {exc}", type="negative")

        async def run_session_steps(client: httpx.AsyncClient, session: dict, area):
            """Run the tutoring session step by step, displaying each step."""

            answer_input = None
            step_count = 0
            max_steps = 20

            while session["state"] != "SESSION_COMPLETE" and step_count < max_steps:
                step_count += 1

                # Advance one step
                resp = await client.post(
                    "/aristotle/session/step",
                    json={"session": session, "student_input": ""},
                )
                resp.raise_for_status()
                step = resp.json()
                session = step["session"]

                if step.get("output"):
                    with area:
                        ui.markdown(step["output"]).style(
                            f"color:{C_CREAM}; font-family:{F_SANS}; "
                            f"background:{C_RAISED}; padding:{SP_SM}; border-radius:{R_SM};"
                        )

                # If we're at PROBE or QUIZ, wait for student input
                if session["state"] in ("PROBE", "QUIZ") and not session.get(
                    "quiz_generated"
                ):
                    # Generate the question first
                    continue

                if session["state"] == "QUIZ" and session.get("quiz_generated"):
                    # Wait for the student's answer
                    with area:
                        ui.label("Your answer:").style(
                            f"font-family:{F_SANS}; color:{C_AMBER}; font-weight:600;"
                        )
                        answer_input = ui.input(
                            placeholder="Type your answer...",
                        ).style("width:100%;")
                        submit_btn = ui.button(
                            "Submit",
                            on_click=lambda: submit_answer(
                                client, session, answer_input, area
                            ),
                        )
                        submit_btn.style(
                            f"background:{C_AMBER}; color:#0d1117; border-radius:{R_SM};"
                        )
                    break  # Wait for the student to submit

            if session["state"] == "SESSION_COMPLETE":
                with area:
                    ui.separator()
                    ui.label("Session Complete").style(
                        f"font-family:{F_SANS}; font-size:18px; font-weight:700; color:{C_CREAM};"
                    )
                    mastered_text = (
                        "Mastered ✓" if session["mastered"] else "Not yet mastered"
                    )
                    mastered_color = "#4A9B8E" if session["mastered"] else C_AMBER
                    ui.label(mastered_text).style(
                        f"font-family:{F_SANS}; font-size:14px; color:{mastered_color};"
                    )
                    ui.label(f"Score: {session['last_score']:.1%}").style(
                        f"font-family:{F_MONO}; color:{C_MUTED};"
                    )

        async def submit_answer(client, session, answer_input, area):
            """Submit the student's answer and continue the session."""
            answer = answer_input.value
            if not answer:
                ui.notify("Please type an answer", type="warning")
                return

            with area:
                ui.label(f"You: {answer}").style(
                    f"color:{C_MUTED}; font-family:{F_SANS}; font-style:italic;"
                )

            # Submit the answer
            resp = await client.post(
                "/aristotle/session/step",
                json={"session": session, "student_input": answer},
            )
            resp.raise_for_status()
            step = resp.json()
            session = step["session"]

            if step.get("output"):
                with area:
                    ui.markdown(step["output"]).style(
                        f"color:{C_CREAM}; font-family:{F_SANS}; "
                        f"background:{C_RAISED}; padding:{SP_SM}; border-radius:{R_SM};"
                    )

            # Continue the session
            await run_session_steps(client, session, area)

        # Load concepts on page load
        async def load_concepts():
            """Fetch concepts from the backend and populate the selector."""
            try:
                async with httpx.AsyncClient(
                    base_url=_BACKEND_URL, timeout=5.0
                ) as client:
                    resp = await client.get("/aristotle/concepts")
                    if resp.status_code == 200:
                        concepts = resp.json()
                        if concepts:
                            concept_select.options = {
                                c["id"]: c["topic"] for c in concepts
                            }
                        else:
                            status_label.text = "No concepts ingested. Run: python -m aristotle.cli ingest concepts_sample.yaml"
                    else:
                        status_label.text = (
                            "Backend reachable but /aristotle/concepts returned error."
                        )
            except httpx.ConnectError:
                status_label.text = "Backend not reachable. Start it with ./start.sh"
            except Exception as exc:
                status_label.text = f"Error loading concepts: {exc}"

        # Schedule concept loading
        ui.timer(0.1, load_concepts, once=True)


# ============================================================
# Teacher Dashboard (Phase B — ADR-001 §8)
# ============================================================


@ui.page("/dashboard")
def dashboard_page():
    """Aristotle teacher dashboard — mastery overview + struggle pattern (ADR-001 §8).

    Three panels:
    1. Header: total concepts, mastered count, due count
    2. Struggle pattern sentence (from MENTOR, prominent)
    3. Mastery table: concept | topic | mastered | last score | next due date
       Sorted by due date ascending — what needs attention is at the top.

    This is the ONE place the actor decomposition is visible (ADR-001 §8):
    the teacher sees what MENTOR tracks, what SM-2 schedules, what's due.
    The learner never sees this page — it's for Komal (the teacher).
    """
    state = GuiState()
    state.active_page = "/dashboard"

    build_top_bar(state)
    build_left_nav(state, active_page="/dashboard")

    with ui.column().classes("w-full flex-1").style(f"padding:{SP_MD}; gap:{SP_MD};"):
        ui.label("Aristotle — Teacher Dashboard").style(
            f"font-family:{F_SANS}; font-size:24px; font-weight:700; color:{C_CREAM};"
        )
        ui.label(
            "Leverage, not surveillance — the tutor's memory of who this learner is."
        ).style(
            f"font-family:{F_SANS}; font-size:14px; color:{C_MUTED}; font-style:italic;"
        )

        # Data will be loaded async into these containers
        stats_row = ui.row().classes("w-full").style(f"gap:{SP_MD};")
        struggle_card = ui.column().classes("w-full").style(f"gap:{SP_SM};")
        table_card = ui.column().classes("w-full").style(f"gap:{SP_SM};")
        status_label = ui.label("").style(
            f"font-family:{F_MONO}; font-size:11px; color:{C_MUTED};"
        )

    async def load_dashboard():
        """Fetch dashboard data from the backend and render it."""
        try:
            async with httpx.AsyncClient(base_url=_BACKEND_URL, timeout=10.0) as client:
                resp = await client.get("/aristotle/dashboard")
                if resp.status_code != 200:
                    status_label.text = f"Backend error: {resp.status_code}"
                    return
                data = resp.json()

                # Panel 1: Stats header
                stats_row.clear()
                with stats_row:
                    _stat_card("Total Concepts", str(data["total_concepts"]), C_CREAM)
                    _stat_card("Mastered", str(data["mastered_count"]), "#4A9B8E")
                    _stat_card("Due Now", str(data["due_count"]), C_AMBER)

                # Panel 2: Struggle pattern
                struggle_card.clear()
                with struggle_card:
                    ui.label("Struggle Pattern").style(
                        f"font-family:{F_SANS}; font-size:14px; font-weight:700; "
                        f"color:{C_AMBER}; letter-spacing:1px; text-transform:uppercase;"
                    )
                    pattern = data.get("struggle_pattern")
                    if pattern:
                        ui.label(pattern).style(
                            f"font-family:{F_SANS}; font-size:16px; color:{C_CREAM}; "
                            f"background:{C_RAISED}; padding:{SP_MD}; border-radius:{R_SM}; "
                            f"line-height:1.5; border-left:3px solid {C_AMBER};"
                        )
                    else:
                        ui.label(
                            "No struggle pattern recorded yet — the tutor is still learning who this learner is."
                        ).style(
                            f"font-family:{F_SANS}; font-size:14px; color:{C_MUTED}; "
                            f"font-style:italic; background:{C_RAISED}; padding:{SP_MD}; border-radius:{R_SM};"
                        )

                # Panel 3: Mastery table
                table_card.clear()
                with table_card:
                    ui.label("Mastery by Concept").style(
                        f"font-family:{F_SANS}; font-size:14px; font-weight:700; "
                        f"color:{C_AMBER}; letter-spacing:1px; text-transform:uppercase;"
                    )

                    mastery = data.get("mastery_by_concept", [])
                    if not mastery:
                        ui.label(
                            "No mastery records yet. Run a tutoring session first."
                        ).style(
                            f"font-family:{F_SANS}; font-size:14px; color:{C_MUTED}; font-style:italic;"
                        )
                    else:
                        # Build a table using NiceGUI's ui.table
                        columns = [
                            {
                                "name": "concept",
                                "label": "Concept",
                                "field": "concept",
                                "align": "left",
                            },
                            {
                                "name": "topic",
                                "label": "Topic",
                                "field": "topic",
                                "align": "left",
                            },
                            {
                                "name": "mastered",
                                "label": "Mastered",
                                "field": "mastered",
                                "align": "center",
                            },
                            {
                                "name": "score",
                                "label": "Last Score",
                                "field": "score",
                                "align": "center",
                            },
                            {
                                "name": "due",
                                "label": "Next Due",
                                "field": "due",
                                "align": "left",
                            },
                        ]
                        rows = []
                        for m in mastery:
                            mastered_text = "✓" if m["mastered"] else "—"
                            score_text = (
                                f"{m['last_score']:.0%}"
                                if m.get("last_score") is not None
                                else "—"
                            )
                            due_text = m.get("next_review_at") or "Due now"
                            if m.get("is_due"):
                                due_text = f"⚠ {due_text}"
                            rows.append(
                                {
                                    "concept": m["concept_id"],
                                    "topic": m["topic"],
                                    "mastered": mastered_text,
                                    "score": score_text,
                                    "due": due_text,
                                }
                            )

                        ui.table(
                            columns=columns,
                            rows=rows,
                        ).style(
                            f"background:{C_RAISED}; color:{C_CREAM}; "
                            f"font-family:{F_SANS}; font-size:13px; border-radius:{R_SM};"
                        )

                status_label.text = f"Loaded {len(data.get('mastery_by_concept', []))} concept records for {data.get('student_id', 'definer')}."

        except httpx.ConnectError:
            status_label.text = "Backend not reachable. Start it with ./start.sh"
        except Exception as exc:
            status_label.text = f"Error: {exc}"

    ui.timer(0.1, load_dashboard, once=True)


def _stat_card(label: str, value: str, color: str) -> None:
    """Render a small stat card (label + big number) in the dashboard header."""
    with ui.column().style(
        f"background:{C_RAISED}; padding:{SP_MD}; border-radius:{R_SM}; "
        f"min-width:120px; align-items:center; gap:4px;"
    ):
        ui.label(value).style(
            f"font-family:{F_SANS}; font-size:28px; font-weight:700; color:{color};"
        )
        ui.label(label).style(
            f"font-family:{F_MONO}; font-size:10px; color:{C_MUTED}; "
            f"letter-spacing:1px; text-transform:uppercase;"
        )
