"""Aristotle on_load / on_unload hooks — ADR-014 §4 stage 5.

The host calls on_load(host) after discover → validate → migrate → register.
This is where ARISTOTLE registers its actors (ADR-014 §5.3: manifest's
`actors` list is advisory; actual registration happens here) and its GUI
page (ADR-014 §5.5: register_page for the learning view).

The host sets `_current_ext_id` before calling on_load, so `host.config`
and `host.manifest` resolve to ARISTOTLE's validated config + manifest.
"""

from __future__ import annotations

from aristotle.actors import ExaminerActor, MentorActor, SocratesActor
from aristotle.api import router as aristotle_router


def on_load(host) -> None:
    """Register ARISTOTLE's actors + GUI page (ADR-001 §2, §3).

    Phase A ships:
      - 3 actors: SOCRATES (teach), EXAMINER (probe/quiz/evaluate), MENTOR (long-arc)
      - 1 GUI page: /learn (the learning view — concept selector + tutoring session)

    HERALD (field awareness) is Phase C — depends on the Phase 0 web/feed
    layer (ADR-014 §3.4), which is not yet built.

    All actors are manual-only (cadence=0.0) — the tutoring state machine
    is driven by user turns, not by a timer (ADR-001 §3: "the learner
    only feels rhythm"). The host runs one cycle on start, then waits for
    cancellation.
    """
    # Register actors
    host.register_actor("socrates", SocratesActor, cadence=0.0)
    host.register_actor("examiner", ExaminerActor, cadence=0.0)
    host.register_actor("mentor", MentorActor, cadence=0.0)

    # Register GUI page (ADR-014 v1.1 — the learning view)
    # The builder_fn is a no-op here — the actual NiceGUI route is registered
    # by aristotle/gui.py (imported by gui/app.py in AIP_Brain). The
    # host.register_page call records the NavItem so the host knows the
    # extension has a GUI surface and transitions to MOUNTED.
    host.register_page(
        route="/learn",
        title="Learn",
        icon="school",
        builder_fn=lambda: None,  # NiceGUI route registered by aristotle/gui.py
        order=30,
    )

    # Register teacher dashboard page (Phase B — ADR-001 §8)
    host.register_page(
        route="/dashboard",
        title="Teach",
        icon="school_outlined",
        builder_fn=lambda: None,  # NiceGUI route registered by aristotle/gui.py
        order=35,
    )

    # Register API router (ADR-014 v1.1 — the platform includes it via
    # host.registered_api_routers() after host.start()). This preserves
    # the boundary: the platform never imports aristotle by name — the
    # extension passes its router object to the host.
    host.register_api_router(aristotle_router)


def on_unload(host) -> None:
    """Cleanup hook — called by host.stop() (ADR-014 §4.2).

    ARISTOTLE has no background resources to release in Phase A. The
    aristotle:textbook corpus is owned by CorpusRegistry and closed by
    its own shutdown. Actor scheduler tasks are cancelled by the host.
    """
    pass
