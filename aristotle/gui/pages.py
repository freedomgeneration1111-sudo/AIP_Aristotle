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
)

log = logging.getLogger("aristotle.gui.pages")


# ---------------------------------------------------------------------------
# Page A — /aristotle/stats
# ---------------------------------------------------------------------------


@ui.page("/aristotle/stats")
async def aristotle_stats_page():
    """ARISTOTLE Stats — mastery, misconceptions, struggle patterns."""
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
            # Mastery
            mastery_data = await get_mastery()
            mastery_col.clear()
            with mastery_col:
                items = mastery_data.get("mastery_by_concept", [])
                if not items:
                    ui.label("No mastery data yet.").style(
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
                    color = (
                        C_OK_FG if pct >= 80
                        else C_AMBER if pct >= 40
                        else C_ERR_FG
                    )
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
            patterns = await get_struggle_patterns()
            pattern_col.clear()
            with pattern_col:
                if not patterns:
                    ui.label("No patterns synthesized yet.").style(
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
            miscs = await get_misconceptions()
            misc_col.clear()
            with misc_col:
                if not miscs:
                    ui.label("No misconceptions logged yet.").style(
                        f"color:{C_MUTED}; font-size:12px;"
                    )
                for m in miscs[:20]:
                    concept = m.get("concept_name", m.get("concept_id", "?"))
                    text = m.get("misconception_text", m.get("text", ""))
                    ts = (m.get("created_at", "") or "")[:10]
                    with ui.row().classes("w-full items-baseline gap-2").style(
                        "padding:2px 0;"
                    ):
                        ui.label(f"[{concept}]").style(
                            f"font-size:10px; color:{C_AMBER}; "
                            f"font-family:{F_MONO}; flex-shrink:0;"
                        )
                        ui.label(text).style(
                            f"font-size:11px; color:{C_CREAM}; flex:1;"
                        )
                        ui.label(ts).style(
                            f"font-size:10px; color:{C_MUTED}; "
                            f"font-family:{F_MONO};"
                        )

        asyncio.create_task(_load())


# ---------------------------------------------------------------------------
# Page B — /aristotle/map
# ---------------------------------------------------------------------------


@ui.page("/aristotle/map")
async def aristotle_map_page():
    """ARISTOTLE Learning Map — concept graph with mastery state."""
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
        ui.label("Concept graph with mastery state. "
                 "Click a concept to start a session.").style(
            f"font-size:12px; color:{C_MUTED}; margin-bottom:24px;"
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
                    ui.label(label).style(
                        f"font-size:10px; color:{C_MUTED};"
                    )

        concept_grid = ui.column().classes("w-full gap-2")

        async def _load():
            concepts = await get_concepts()
            mastery_data = await get_mastery()
            mastery_map = {
                m.get("concept_id"): m
                for m in mastery_data.get("mastery_by_concept", [])
            }

            concept_grid.clear()
            with concept_grid:
                if not concepts:
                    ui.label("No concepts loaded yet. "
                             "Ingest course material first.").style(
                        f"color:{C_MUTED}; font-size:12px;"
                    )
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
                            C_OK_FG if pct >= 80
                            else C_AMBER if pct >= 40
                            else C_ERR_FG
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
                        .on("click", lambda c=cid: ui.navigate.to("/ask"))
                    ):
                        with ui.column().classes("flex-1").style("gap:2px;"):
                            ui.label(name).style(
                                f"font-size:13px; color:{C_CREAM}; "
                                f"font-weight:500;"
                            )
                            if prereq:
                                ui.label(
                                    f"Requires: {prereq}"
                                ).style(
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

        def _field(label: str, value: str = "",
                   placeholder: str = "") -> ui.input:
            ui.label(label).style(
                f"font-size:10px; font-weight:700; letter-spacing:1px; "
                f"color:{C_MUTED}; text-transform:uppercase; margin-bottom:4px;"
            )
            inp = (
                ui.input(value=value, placeholder=placeholder)
                .props("outlined dense")
                .classes("w-full")
                .style(f"max-width:400px;")
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
        lang_inp = _field(
            "Primary Language", placeholder="e.g. English"
        )
        alt_lang_inp = _field(
            "Alt Language (optional)", placeholder="e.g. Urdu"
        )

        _section("SESSION PREFERENCES")
        ui.label("Session length (questions per session)").style(
            f"font-size:10px; color:{C_MUTED}; margin-bottom:4px;"
        )
        session_len = ui.number(value=5, min=3, max=20, step=1).props(
            "outlined dense"
        )

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
            ["conservative", "balanced", "generous"],
            value="balanced"
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
                mastery_thresh.value = settings.get(
                    "mastery_threshold", 0.85
                )
                hint_mode.value = settings.get(
                    "hint_aggressiveness", "balanced"
                )

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
                ui.notify(
                    "Save failed — check ARISTOTLE backend.",
                    color="negative"
                )

        save_btn.on("click", lambda: asyncio.create_task(_save()))
        asyncio.create_task(_load())
