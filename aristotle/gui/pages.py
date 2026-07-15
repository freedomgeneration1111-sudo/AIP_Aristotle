"""ARISTOTLE GUI pages — stats, learning map, settings.

Registered via @ui.page decorators. Loaded by Brain's gui/app.py via
the aip.extension_gui entry point. Each page uses the three-panel shell
from gui.components.layout.

All Brain GUI imports are wrapped in try/except — the extension must be
importable headlessly (e.g. in tests) without Brain's GUI layer present.
"""

from __future__ import annotations

import asyncio
import logging

from nicegui import ui

# Brain GUI imports — available at runtime when loaded via entry points.
# These fail gracefully if imported outside Brain's process (e.g. tests).
try:
    from gui.components.layout import (
        build_left_nav,
        build_top_bar,
        build_right_rail,
        set_active_extension,
        clear_active_extension,
    )
    from gui.state import get_session_state
    from gui.theme import (
        C_AMBER,
        C_CREAM,
        C_ERR_FG,
        C_GROUND,
        C_INK40,
        C_MUTED,
        C_OK_FG,
        C_SURFACE,
        F_MONO,
        F_SANS,
        R_LG,
    )

    _BRAIN_GUI = True
except ImportError:
    _BRAIN_GUI = False

    # Fallback stubs for headless import (tests).
    def set_active_extension(name: str, mode: str = "") -> None:
        pass

    def clear_active_extension() -> None:
        pass


from aristotle.gui.api_client import (
    get_mastery,
    get_misconceptions,
    get_struggle_patterns,
    get_concepts,
    get_settings,
    update_settings,
    get_session_history,
    get_plans,
)

log = logging.getLogger("aristotle.gui.pages")


# ---------------------------------------------------------------------------
# Page A — /aristotle/stats
# ---------------------------------------------------------------------------


@ui.page("/aristotle/stats")
async def aristotle_stats_page():
    """ARISTOTLE Stats — mastery, misconceptions, struggle patterns.

    Task 20: now scoped to a single plan (subject) via the selector at
    the top. Defaults to the most recently active plan on load. Without
    the selector, every subject's concepts were mixed into one list —
    the same cross-contamination bug Task 17/18 fixed at the API level,
    resurfacing in the GUI because the helpers were never given a
    plan_id to thread through.
    """
    if not _BRAIN_GUI:
        ui.label("Brain GUI not available").style("color:red")
        return

    state = get_session_state()
    build_top_bar(state)
    build_left_nav(state, active_page="/aristotle/stats")
    build_right_rail(state)

    with (
        ui.column()
        .classes("flex-1")
        .style(
            f"background:{C_GROUND}; padding:24px; "
            f"overflow-y:auto; min-height:calc(100vh - 44px);"
        )
    ):
        ui.label("ARISTOTLE · Stats").style(
            f"font-family:{F_SANS}; font-size:24px; font-weight:700; "
            f"color:{C_CREAM}; margin-bottom:4px;"
        )
        ui.label("Mastery, misconceptions, and struggle patterns.").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:24px;"
        )

        # Task 20: plan selector. Stored in a one-element list so the
        # closure can reassign it (Python closures bind by reference,
        # so a bare `_selected_plan_id: str = ""` would let _on_plan_change
        # rebind it but _load() would still see the original. The list
        # trick is the standard Python workaround.)
        _selected: list[str | None] = [None]
        plans = await get_plans()
        initial_plan_id = _pick_default_plan_id(plans)
        _selected[0] = initial_plan_id

        if not plans:
            ui.label(
                "No learning plans yet. Complete an onboarding session first."
            ).style(
                f"color:{C_MUTED}; font-size:13px; padding:24px 0;"
            )
            return

        async def _on_plan_change(new_plan_id: str | None) -> None:
            _selected[0] = new_plan_id
            await _load()

        _build_plan_selector(
            plans,
            initial_plan_id=initial_plan_id,
            on_change=_on_plan_change,
        )

        # --- MASTERY SECTION ---
        ui.label("MASTERY BY CONCEPT").style(
            f"font-size:10px; font-weight:700; letter-spacing:2px; "
            f"color:{C_MUTED}; margin-bottom:8px;"
        )
        mastery_col = ui.column().classes("w-full gap-2")

        ui.separator().style(f"background:{C_INK40}; margin:16px 0;")

        # --- STRUGGLE PATTERNS ---
        ui.label("STRUGGLE PATTERNS").style(
            f"font-size:10px; font-weight:700; letter-spacing:2px; "
            f"color:{C_MUTED}; margin-bottom:8px;"
        )
        pattern_col = ui.column().classes("w-full gap-2")

        ui.separator().style(f"background:{C_INK40}; margin:16px 0;")

        # --- MISCONCEPTION LOG ---
        ui.label("RECENT MISCONCEPTIONS").style(
            f"font-size:10px; font-weight:700; letter-spacing:2px; "
            f"color:{C_MUTED}; margin-bottom:8px;"
        )
        misc_col = ui.column().classes("w-full gap-1")

        async def _load():
            plan_id = _selected[0]
            # Mastery
            mastery_data = await get_mastery(plan_id=plan_id)
            mastery_col.clear()
            with mastery_col:
                items = mastery_data.get("mastery_by_concept", [])
                if not items:
                    ui.label("No mastery data yet for this subject.").style(
                        f"color:{C_MUTED}; font-size:12px;"
                    )
                for item in items:
                    concept = item.get("topic", item.get("concept_id", "?"))
                    score = item.get("last_score")
                    if score is not None:
                        pct = round(score * 100)
                    else:
                        pct = 0
                    due = item.get("is_due", False)
                    color = C_OK_FG if pct >= 80 else C_AMBER if pct >= 40 else C_ERR_FG
                    with (
                        ui.row()
                        .classes("w-full items-center gap-3")
                        .style(
                            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_LG}; padding:8px 12px; max-width:600px;"
                        )
                    ):
                        ui.label(concept).style(
                            f"font-size:12px; color:{C_CREAM}; flex:1;"
                        )
                        if due:
                            ui.label("DUE").style(
                                f"font-size:10px; color:{C_AMBER}; "
                                f"font-family:{F_MONO};"
                            )
                        ui.label(f"{pct}%").style(
                            f"font-size:13px; font-weight:700; "
                            f"color:{color}; font-family:{F_MONO};"
                        )

            # Struggle patterns
            patterns = await get_struggle_patterns(plan_id=plan_id)
            pattern_col.clear()
            with pattern_col:
                if not patterns:
                    ui.label("No patterns synthesized yet for this subject.").style(
                        f"color:{C_MUTED}; font-size:12px;"
                    )
                for p in patterns:
                    concept = p.get("concept_name", p.get("concept_id", "?"))
                    pattern_text = p.get("pattern", "")
                    with (
                        ui.card()
                        .classes("w-full")
                        .style(
                            f"background:{C_SURFACE}; "
                            f"border:0.5px solid {C_AMBER}; "
                            f"border-radius:{R_LG}; padding:12px 16px; "
                            f"max-width:600px;"
                        )
                    ):
                        ui.label(concept).style(
                            f"font-size:11px; color:{C_AMBER}; "
                            f"font-family:{F_MONO}; margin-bottom:4px;"
                        )
                        ui.label(pattern_text).style(
                            f"font-size:12px; color:{C_CREAM}; line-height:1.6;"
                        )

            # Misconceptions
            miscs = await get_misconceptions(plan_id=plan_id)
            misc_col.clear()
            with misc_col:
                if not miscs:
                    ui.label("No misconceptions logged yet for this subject.").style(
                        f"color:{C_MUTED}; font-size:12px;"
                    )
                for m in miscs[:20]:
                    concept = m.get("concept_name", m.get("concept_id", "?"))
                    text = m.get("misconception_text", m.get("text", ""))
                    ts = (m.get("created_at", "") or "")[:10]
                    with (
                        ui.row()
                        .classes("w-full items-baseline gap-2")
                        .style("padding:2px 0;")
                    ):
                        ui.label(f"[{concept}]").style(
                            f"font-size:10px; color:{C_AMBER}; "
                            f"font-family:{F_MONO}; flex-shrink:0;"
                        )
                        ui.label(text).style(
                            f"font-size:11px; color:{C_CREAM}; flex:1;"
                        )
                        ui.label(ts).style(
                            f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                        )

        asyncio.create_task(_load())


# ---------------------------------------------------------------------------
# Page B — /aristotle/map
# ---------------------------------------------------------------------------


@ui.page("/aristotle/map")
async def aristotle_map_page():
    """ARISTOTLE Learning Map — concept graph with mastery state.

    Task 20: now scoped to a single plan (subject) via the selector at
    the top. Same rationale as /aristotle/stats — without it, every
    subject's concepts were mixed into one graph.
    """
    if not _BRAIN_GUI:
        ui.label("Brain GUI not available").style("color:red")
        return

    state = get_session_state()
    build_top_bar(state)
    build_left_nav(state, active_page="/aristotle/map")
    build_right_rail(state)

    with (
        ui.column()
        .classes("flex-1")
        .style(
            f"background:{C_GROUND}; padding:24px; "
            f"overflow-y:auto; min-height:calc(100vh - 44px);"
        )
    ):
        ui.label("ARISTOTLE · Learning Map").style(
            f"font-family:{F_SANS}; font-size:24px; font-weight:700; "
            f"color:{C_CREAM}; margin-bottom:4px;"
        )
        ui.label(
            "Concept graph with mastery state. Click a concept to start a session."
        ).style(f"font-size:12px; color:{C_MUTED}; margin-bottom:24px;")

        # Task 20: plan selector (same pattern as /aristotle/stats).
        _selected: list[str | None] = [None]
        plans = await get_plans()
        initial_plan_id = _pick_default_plan_id(plans)
        _selected[0] = initial_plan_id

        if not plans:
            ui.label(
                "No learning plans yet. Complete an onboarding session first."
            ).style(
                f"color:{C_MUTED}; font-size:13px; padding:24px 0;"
            )
            return

        async def _on_plan_change(new_plan_id: str | None) -> None:
            _selected[0] = new_plan_id
            await _load()

        _build_plan_selector(
            plans,
            initial_plan_id=initial_plan_id,
            on_change=_on_plan_change,
        )

        # Legend
        with ui.row().classes("gap-4").style("margin-bottom:16px;"):
            for label, color in [
                ("80%+ Mastered", C_OK_FG),
                ("40-79% Learning", C_AMBER),
                ("<40% Needs work", C_ERR_FG),
                ("Not started", C_MUTED),
            ]:
                with ui.row().classes("items-center gap-1"):
                    ui.label("●").style(f"color:{color}; font-size:12px;")
                    ui.label(label).style(f"font-size:10px; color:{C_MUTED};")

        concept_grid = ui.column().classes("w-full gap-2")

        async def _load():
            plan_id = _selected[0]
            concepts = await get_concepts(plan_id=plan_id)
            mastery_data = await get_mastery(plan_id=plan_id)
            mastery_map = {
                m.get("concept_id"): m
                for m in mastery_data.get("mastery_by_concept", [])
            }

            concept_grid.clear()
            with concept_grid:
                if not concepts:
                    ui.label(
                        "No concepts loaded yet for this subject."
                    ).style(f"color:{C_MUTED}; font-size:12px;")
                    return

                for concept in concepts:
                    cid = concept.get("id", concept.get("concept_id", ""))
                    name = concept.get("topic", concept.get("name", cid))
                    prereq = concept.get("prerequisite_concept_id")
                    m = mastery_map.get(cid, {})
                    score = m.get("last_score")

                    if score is None:
                        color = C_MUTED
                        pct_label = "Not started"
                    else:
                        pct = round(score * 100)
                        pct_label = f"{pct}%"
                        color = (
                            C_OK_FG if pct >= 80 else C_AMBER if pct >= 40 else C_ERR_FG
                        )

                    due = m.get("is_due", False)

                    with (
                        ui.row()
                        .classes("w-full items-center gap-3 cursor-pointer")
                        .style(
                            f"background:{C_SURFACE}; "
                            f"border:0.5px solid {C_INK40}; "
                            f"border-left:3px solid {color}; "
                            f"border-radius:{R_LG}; padding:10px 14px; "
                            f"max-width:640px; transition:background 0.15s;"
                        )
                        .on("click", lambda c=cid: ui.navigate.to(f"/ask?extension=aristotle&concept={c}"))
                    ):
                        with ui.column().classes("flex-1").style("gap:2px;"):
                            ui.label(name).style(
                                f"font-size:13px; color:{C_CREAM}; font-weight:500;"
                            )
                            if prereq:
                                ui.label(f"Requires: {prereq}").style(
                                    f"font-size:10px; color:{C_MUTED}; "
                                    f"font-family:{F_MONO};"
                                )
                        if due:
                            ui.label("DUE").style(
                                f"font-size:10px; color:{C_AMBER}; "
                                f"font-family:{F_MONO};"
                            )
                        ui.label(pct_label).style(
                            f"font-size:14px; font-weight:700; "
                            f"color:{color}; font-family:{F_MONO}; "
                            f"min-width:60px; text-align:right;"
                        )

        asyncio.create_task(_load())


# ---------------------------------------------------------------------------
# Page C — /aristotle/settings
# ---------------------------------------------------------------------------


@ui.page("/aristotle/settings")
async def aristotle_settings_page():
    """ARISTOTLE Settings — student profile and tutor preferences."""
    if not _BRAIN_GUI:
        ui.label("Brain GUI not available").style("color:red")
        return

    state = get_session_state()
    build_top_bar(state)
    build_left_nav(state, active_page="/aristotle/settings")
    build_right_rail(state)

    with (
        ui.column()
        .classes("flex-1")
        .style(
            f"background:{C_GROUND}; padding:24px; "
            f"overflow-y:auto; min-height:calc(100vh - 44px);"
            f"max-width:600px;"
        )
    ):
        ui.label("ARISTOTLE · Settings").style(
            f"font-family:{F_SANS}; font-size:24px; font-weight:700; "
            f"color:{C_CREAM}; margin-bottom:24px;"
        )

        def _field(label: str, value: str = "", placeholder: str = "") -> ui.input:
            ui.label(label).style(
                f"font-size:10px; font-weight:700; letter-spacing:1px; "
                f"color:{C_MUTED}; text-transform:uppercase; margin-bottom:4px;"
            )
            inp = (
                ui.input(value=value, placeholder=placeholder)
                .props("outlined dense")
                .classes("w-full")
                .style("max-width:400px;")
            )
            return inp

        def _section(title: str) -> None:
            ui.separator().style(f"background:{C_INK40}; margin:20px 0 12px;")
            ui.label(title).style(
                f"font-size:11px; font-weight:700; letter-spacing:2px; "
                f"color:{C_AMBER}; text-transform:uppercase; margin-bottom:12px;"
            )

        # Student profile
        _section("STUDENT PROFILE")
        name_inp = _field("Display Name", placeholder="e.g. Ramesh")
        lang_inp = _field("Primary Language", placeholder="e.g. English")
        alt_lang_inp = _field("Alt Language (optional)", placeholder="e.g. Urdu")

        _section("SESSION PREFERENCES")
        ui.label("Session length (questions per session)").style(
            f"font-size:10px; color:{C_MUTED}; margin-bottom:4px;"
        )
        session_len = ui.number(value=5, min=3, max=20, step=1).props("outlined dense")

        ui.label("Mastery threshold (0.0 - 1.0)").style(
            f"font-size:10px; color:{C_MUTED}; margin-bottom:4px; margin-top:12px;"
        )
        mastery_thresh = ui.number(
            value=0.85, min=0.5, max=1.0, step=0.05, format="%.2f"
        ).props("outlined dense")

        ui.label("Hint ladder aggressiveness").style(
            f"font-size:10px; color:{C_MUTED}; margin-bottom:4px; margin-top:12px;"
        )
        hint_mode = ui.select(
            ["conservative", "balanced", "generous"], value="balanced"
        ).props("outlined dense")

        save_btn = (
            ui.button("Save Settings")
            .props("flat")
            .style(
                f"background:{C_AMBER}; color:#0E0800; font-weight:700; "
                f"font-size:12px; padding:8px 24px; border-radius:4px; "
                f"margin-top:24px;"
            )
        )

        async def _load():
            settings = await get_settings()
            if settings:
                name_inp.value = settings.get("display_name", "")
                lang_inp.value = settings.get("primary_language", "")
                alt_lang_inp.value = settings.get("alt_language", "")
                session_len.value = settings.get("session_length", 5)
                mastery_thresh.value = settings.get("mastery_threshold", 0.85)
                hint_mode.value = settings.get("hint_aggressiveness", "balanced")

        async def _save():
            payload = {
                "display_name": name_inp.value,
                "primary_language": lang_inp.value,
                "alt_language": alt_lang_inp.value,
                "session_length": int(session_len.value or 5),
                "mastery_threshold": float(mastery_thresh.value or 0.85),
                "hint_aggressiveness": hint_mode.value,
            }
            result = await update_settings(settings=payload)
            if result:
                ui.notify("Settings saved.", color="positive", timeout=2000)
            else:
                ui.notify("Save failed — check ARISTOTLE backend.", color="negative")

        save_btn.on("click", lambda: asyncio.create_task(_save()))
        asyncio.create_task(_load())


# ---------------------------------------------------------------------------
# Helper functions for teacher dashboard
# ---------------------------------------------------------------------------


def _stat_tile(label: str, value: str, color: str) -> None:
    """Render a quick stat tile."""
    with (
        ui.column()
        .classes("items-center")
        .style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_LG}; padding:14px 20px; "
            f"min-width:120px; flex:1;"
        )
    ):
        ui.label(value).style(
            f"font-size:22px; font-weight:700; color:{color}; font-family:{F_MONO};"
        )
        ui.label(label).style(
            f"font-size:10px; letter-spacing:1px; "
            f"color:{C_MUTED}; text-transform:uppercase; "
            f"margin-top:2px;"
        )


def _section_hdr(text: str) -> None:
    """Render a section label."""
    ui.label(text).style(
        f"font-size:10px; font-weight:700; letter-spacing:2px; "
        f"color:{C_MUTED}; text-transform:uppercase; "
        f"margin-bottom:10px;"
    )


def _mini_stat(label: str, value: int, color: str | None = None) -> None:
    """Render a small inline stat."""
    c = color or C_MUTED
    ui.label(f"{value} {label}").style(
        f"font-size:10px; color:{c}; font-family:{F_MONO};"
    )


# ---------------------------------------------------------------------------
# Task 20: shared plan-selector helper.
#
# Used by /aristotle/stats and /aristotle/map to scope their concept/mastery
# data to a single plan (one subject). The /aristotle/teacher dashboard uses
# a variant (with an "All" option) since it aggregates across subjects by
# design — see _build_plan_filter_dropdown.
#
# The selector is populated from GET /aristotle/plans (same data source the
# /ask plan picker uses). On change, it calls on_change(new_plan_id) so the
# page can re-fetch its scoped data. The selected plan_id is also stored on
# the returned select element's .value for the page to read synchronously.
#
# Defaults to the most recently active plan (highest last_session_at) on
# first load — if no plans have a last_session_at, falls back to the first
# plan in the list (GET /plans orders by created_at DESC, so that's the
# newest plan).
# ---------------------------------------------------------------------------


def _pick_default_plan_id(plans: list) -> str | None:
    """Pick the most-recently-active plan_id from a get_plans() result.

    Returns None if plans is empty. Used as the initial value for the
    plan selector so the page loads with a real scope instead of "All".
    """
    if not plans:
        return None
    # Sort by last_session_at descending (None sorts last). Stability
    # keeps created_at DESC order for plans with the same last_session_at.
    with_session = [p for p in plans if p.get("last_session_at")]
    without_session = [p for p in plans if not p.get("last_session_at")]
    with_session.sort(key=lambda p: p["last_session_at"], reverse=True)
    ordered = with_session + without_session
    return ordered[0].get("id") if ordered else None


def _build_plan_selector(
    plans: list,
    *,
    initial_plan_id: str | None,
    on_change,
) -> Any:
    """Render a labeled ui.select for choosing a plan (subject).

    Args:
        plans: result of get_plans() — list of {id, subject, status, ...}.
        initial_plan_id: plan_id to select on first render (usually
            _pick_default_plan_id(plans)).
        on_change: async callable taking the new plan_id. The page's
            _load() reads it to scope its API calls.

    Returns the ui.select element so the caller can read .value later.
    """
    # Build {plan_id: "subject (N/M concepts, status)"} for the dropdown.
    options: dict[str, str] = {}
    for p in plans:
        pid = p.get("id", "")
        subject = p.get("subject", "(untitled)")
        total = p.get("total_concepts", 0)
        idx = p.get("current_concept_idx", 0)
        status = p.get("status", "active")
        if status == "complete":
            status_str = "complete"
        elif p.get("last_session_at"):
            status_str = f"last {p['last_session_at'][:10]}"
        else:
            status_str = "not started"
        options[pid] = f"{subject} ({idx}/{total}, {status_str})"

    with ui.row().classes("items-center gap-2").style("margin-bottom:16px;"):
        ui.label("Subject:").style(
            f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO}; "
            f"letter-spacing:1px; text-transform:uppercase;"
        )
        select = (
            ui.select(
                options=options,
                value=initial_plan_id,
                on_change=lambda e: asyncio.create_task(on_change(e.value)),
            )
            .props("dense dark outlined")
            .style(
                f"min-width:320px; font-size:13px; "
                f"font-family:{F_MONO};"
            )
        )
    return select


def _build_plan_filter_dropdown(
    plans: list,
    *,
    on_change,
) -> Any:
    """Variant of _build_plan_selector for the Teacher Dashboard.

    Adds an "All" option at the top (value=None) so the dashboard's
    default view remains the cross-subject aggregation it has always
    been — the filter is opt-in, not required. Per Task 20 brief: do
    NOT change how the dashboard's default (unfiltered) view behaves
    beyond adding labels + the optional filter.
    """
    options: dict[str | None, str] = {None: "All subjects"}
    for p in plans:
        pid = p.get("id", "")
        subject = p.get("subject", "(untitled)")
        options[pid] = subject

    with ui.row().classes("items-center gap-2").style("margin-bottom:16px;"):
        ui.label("Filter:").style(
            f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO}; "
            f"letter-spacing:1px; text-transform:uppercase;"
        )
        select = (
            ui.select(
                options=options,
                value=None,  # default to "All"
                on_change=lambda e: asyncio.create_task(on_change(e.value)),
            )
            .props("dense dark outlined")
            .style(
                f"min-width:240px; font-size:13px; "
                f"font-family:{F_MONO};"
            )
        )
    return select


def _label_for_plan(plan_id: str | None, plans_by_id: dict) -> str:
    """Return a human-readable subject label for a plan_id.

    Used by the Teacher Dashboard to label each Needs-Attention /
    Recent-Sessions row with its subject. Returns "Unlabeled" for
    None or unknown plan_id (pre-Task-18 legacy data) rather than
    crashing or hiding the row.
    """
    if not plan_id:
        return "Unlabeled"
    plan = plans_by_id.get(plan_id)
    if plan is None:
        return "Unlabeled"
    return plan.get("subject", "(untitled)")


# ---------------------------------------------------------------------------
# Page D — /aristotle/teacher (Komal's dashboard)
# ---------------------------------------------------------------------------


@ui.page("/aristotle/teacher")
async def aristotle_teacher_page():
    """ARISTOTLE Teacher Dashboard — Komal's view.

    Shows: quick stat tiles (total/mastered/due/not started),
    needs-attention list, struggle pattern, recent sessions timeline,
    full mastery table.

    Task 20: each concept/session row now carries a subject label
    (from its plan_id) so it's clear which subject each item belongs
    to. An optional "All subjects" filter (default) scopes to one
    plan_id when chosen — the dashboard's default cross-subject
    aggregation behavior is preserved when no filter is selected.
    Rows with no plan_id (pre-Task-18 legacy data) are labeled
    "Unlabeled" rather than hidden or crashing.
    """
    if not _BRAIN_GUI:
        ui.label("Brain GUI not available").style("color:red")
        return

    state = get_session_state()
    build_top_bar(state)
    build_left_nav(state, active_page="/aristotle/teacher")
    build_right_rail(state)

    with (
        ui.column()
        .classes("flex-1")
        .style(
            f"background:{C_GROUND}; padding:24px; "
            f"overflow-y:auto; min-height:calc(100vh - 44px);"
        )
    ):
        ui.label("ARISTOTLE - Teacher Dashboard").style(
            f"font-family:{F_SANS}; font-size:24px; "
            f"font-weight:700; color:{C_CREAM}; margin-bottom:4px;"
        )
        ui.label("Student progress overview - Freedom Generation").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:24px;"
        )

        # Task 20: optional subject filter. Default "All subjects"
        # preserves the dashboard's cross-subject aggregation behavior.
        _selected: list[str | None] = [None]
        plans = await get_plans()
        plans_by_id: dict[str, dict] = {p.get("id", ""): p for p in plans}

        async def _on_filter_change(new_plan_id: str | None) -> None:
            _selected[0] = new_plan_id
            await _load()

        if plans:
            _build_plan_filter_dropdown(plans, on_change=_on_filter_change)

        # Row 1: Quick stat tiles
        stats_row = (
            ui.row()
            .classes("w-full gap-4")
            .style("flex-wrap:wrap; margin-bottom:24px;")
        )

        # Row 2: Two-column layout
        with (
            ui.row()
            .classes("w-full gap-6")
            .style("flex-wrap:wrap; align-items:flex-start;")
        ):
            left_col = ui.column().style("flex:1; min-width:280px; gap:0;")
            right_col = ui.column().style("flex:1; min-width:280px; gap:0;")

        # Row 3: Full mastery table
        ui.separator().style(f"background:{C_INK40}; margin:24px 0 16px;")
        ui.label("ALL CONCEPTS").style(
            f"font-size:10px; font-weight:700; letter-spacing:2px; "
            f"color:{C_MUTED}; margin-bottom:12px;"
        )
        table_col = ui.column().classes("w-full")

        async def _load():
            import asyncio as _asyncio

            plan_id = _selected[0]
            dashboard, sessions = await _asyncio.gather(
                get_mastery(plan_id=plan_id),
                get_session_history(plan_id=plan_id),
                return_exceptions=True,
            )
            if isinstance(dashboard, Exception):
                dashboard = {}
            if isinstance(sessions, Exception):
                sessions = []

            total = dashboard.get("total_concepts", 0)
            mastered = dashboard.get("mastered_count", 0)
            due = dashboard.get("due_count", 0)
            not_start = sum(
                1
                for c in dashboard.get("mastery_by_concept", [])
                if c.get("repetitions", 0) == 0
            )
            struggle = dashboard.get("struggle_pattern")
            concepts = dashboard.get("mastery_by_concept", [])

            # Stat tiles
            stats_row.clear()
            with stats_row:
                _stat_tile("Total", str(total), C_CREAM)
                _stat_tile(
                    "Mastered",
                    f"{mastered} ({round(mastered / total * 100) if total else 0}%)",
                    C_OK_FG,
                )
                _stat_tile(
                    "Due for Review",
                    str(due),
                    C_AMBER if due > 0 else C_MUTED,
                )
                _stat_tile(
                    "Not Started",
                    str(not_start),
                    C_ERR_FG if not_start > 0 else C_MUTED,
                )

            # Left col: Needs Attention
            left_col.clear()
            with left_col:
                _section_hdr("NEEDS ATTENTION")
                attention = [
                    c
                    for c in concepts
                    if c.get("is_due") or c.get("repetitions", 0) == 0
                ][:12]
                if not attention:
                    ui.label("Nothing urgent - student is on track.").style(
                        f"font-size:12px; color:{C_OK_FG}; padding:8px 0;"
                    )
                for c in attention:
                    is_due = c.get("is_due", False)
                    not_yet = c.get("repetitions", 0) == 0
                    topic = c.get("topic", c.get("concept_id", "?"))
                    score = c.get("last_score")
                    score_txt = f"{round(score * 100)}%" if score else "-"
                    badge = "DUE" if is_due else "NEW"
                    badge_c = C_AMBER if is_due else C_MUTED
                    # Task 20: subject label per row.
                    subject_label = _label_for_plan(c.get("plan_id"), plans_by_id)
                    with (
                        ui.row()
                        .classes("w-full items-center gap-2")
                        .style(
                            f"padding:6px 10px; "
                            f"border-left:2px solid {badge_c}; "
                            f"margin-bottom:4px; "
                            f"background:{C_SURFACE}; "
                            f"border-radius:0 4px 4px 0;"
                        )
                    ):
                        ui.label(badge).style(
                            f"font-size:9px; font-family:{F_MONO}; "
                            f"color:{badge_c}; min-width:28px; "
                            f"font-weight:700;"
                        )
                        ui.label(topic).style(
                            f"font-size:11px; color:{C_CREAM}; flex:1;"
                        )
                        ui.label(subject_label).style(
                            f"font-size:9px; color:{C_MUTED}; "
                            f"font-family:{F_MONO}; "
                            f"background:{C_GROUND}; padding:2px 6px; "
                            f"border-radius:3px; max-width:140px; "
                            f"overflow:hidden; text-overflow:ellipsis; "
                            f"white-space:nowrap;"
                        )
                        ui.label(score_txt).style(
                            f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};"
                        )

                # Struggle Pattern
                if struggle:
                    ui.separator().style(f"background:{C_INK40}; margin:16px 0 12px;")
                    _section_hdr("STRUGGLE PATTERN")
                    with (
                        ui.card()
                        .classes("w-full")
                        .style(
                            f"background:{C_SURFACE}; "
                            f"border:0.5px solid {C_AMBER}; "
                            f"border-radius:{R_LG}; padding:12px 14px;"
                        )
                    ):
                        ui.label(struggle).style(
                            f"font-size:12px; color:{C_CREAM}; line-height:1.6;"
                        )

            # Right col: Recent Sessions
            right_col.clear()
            with right_col:
                _section_hdr("RECENT SESSIONS")
                if not sessions:
                    ui.label("No sessions recorded yet.").style(
                        f"font-size:12px; color:{C_MUTED}; padding:8px 0;"
                    )
                for s in sessions[:10]:
                    concept = s.get("concept_id", "?")
                    started = (s.get("started_at") or "")[:16].replace("T", " ")
                    events = s.get("event_count", 0)
                    answers = s.get("answer_count", 0)
                    curiosity = s.get("curiosity_count", 0)
                    # Task 20: subject label per session row. The
                    # /session-history route (when wired — currently
                    # unwired per STATUS.md) returns plan_id per
                    # session if available. Fall back to "Unlabeled"
                    # for sessions with no plan_id.
                    session_plan_id = s.get("plan_id")
                    subject_label = _label_for_plan(session_plan_id, plans_by_id)
                    with (
                        ui.card()
                        .classes("w-full")
                        .style(
                            f"background:{C_SURFACE}; "
                            f"border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_LG}; "
                            f"padding:10px 12px; margin-bottom:6px;"
                        )
                    ):
                        with ui.row().classes("w-full items-center gap-2"):
                            ui.label(concept).style(
                                f"font-size:12px; font-weight:600; "
                                f"color:{C_AMBER}; flex:1;"
                            )
                            ui.label(subject_label).style(
                                f"font-size:9px; color:{C_MUTED}; "
                                f"font-family:{F_MONO}; "
                                f"background:{C_GROUND}; padding:2px 6px; "
                                f"border-radius:3px; max-width:140px; "
                                f"overflow:hidden; text-overflow:ellipsis; "
                                f"white-space:nowrap;"
                            )
                            ui.label(started).style(
                                f"font-size:10px; color:{C_MUTED}; "
                                f"font-family:{F_MONO};"
                            )
                        with ui.row().classes("w-full gap-3").style("margin-top:4px;"):
                            _mini_stat("exchanges", events)
                            _mini_stat("answers", answers)
                            if curiosity:
                                _mini_stat("questions asked", curiosity, C_AMBER)

            # Full mastery table
            table_col.clear()
            with table_col:
                if not concepts:
                    ui.label("No concepts in curriculum yet.").style(
                        f"color:{C_MUTED}; font-size:12px;"
                    )
                    return
                with (
                    ui.element("table")
                    .classes("w-full")
                    .style(
                        f"border-collapse:collapse; "
                        f"font-family:{F_MONO}; font-size:11px; "
                        f"color:{C_MUTED};"
                    )
                ):
                    with ui.element("thead"):
                        with ui.element("tr").style(
                            f"border-bottom:0.5px solid {C_INK40};"
                        ):
                            # Task 20: added "Subject" column (2nd).
                            for col in [
                                "Concept",
                                "Subject",
                                "Score",
                                "Reps",
                                "Next Review",
                                "Status",
                            ]:
                                th = ui.element("th").style(
                                    f"text-align:left; padding:4px 8px; "
                                    f"font-size:10px; letter-spacing:.5px; "
                                    f"color:{C_MUTED}; font-weight:500;"
                                )
                                th.text = col
                    with ui.element("tbody"):
                        for c in concepts:
                            topic = c.get("topic", c.get("concept_id", "?"))
                            score = c.get("last_score")
                            reps = c.get("repetitions", 0)
                            due_dt = (c.get("next_review_at") or "")[:10]
                            mastered = c.get("mastered", False)
                            is_due = c.get("is_due", False)
                            subject_label = _label_for_plan(c.get("plan_id"), plans_by_id)

                            score_txt = f"{round(score * 100)}%" if score else "-"
                            if mastered:
                                status, sc = "Mastered", C_OK_FG
                            elif is_due:
                                status, sc = "Due", C_AMBER
                            elif reps > 0:
                                status, sc = "In progress", C_AMBER
                            else:
                                status, sc = "Not started", C_MUTED

                            with ui.element("tr").style(
                                f"border-bottom:0.5px solid {C_INK40};"
                            ):
                                for txt, color in [
                                    (topic, C_CREAM),
                                    (subject_label, C_MUTED),
                                    (score_txt, C_CREAM),
                                    (str(reps), C_MUTED),
                                    (due_dt or "-", C_MUTED),
                                    (status, sc),
                                ]:
                                    td = ui.element("td").style(
                                        f"padding:5px 8px; color:{color};"
                                    )
                                    td.text = txt

        asyncio.create_task(_load())
