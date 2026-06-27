"""Aristotle configuration — ADR-014 §6.4 config.schema.

A plain dataclass (not pydantic_settings.BaseSettings) so it instantiates
without env-var dependencies. The host's `_validate_config_schema_class`
accepts dataclasses. All fields have defaults so `AristotleSettings()`
works with zero args.

Layer: this module is imported by the host at stage 1 validate. It lives
under extensions/aristotle/ which the host adds to sys.path. Imports from
the stdlib only (dataclasses) — no aip imports, keeping the extension
self-contained.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AristotleSettings:
    """Configuration for the Aristotle extension.

    Bilingual schema per ADR-014 §1: content_primary + content_alt +
    content_alt_lang (ISO 639-1). ARISTOTLE defaults to English primary +
    Urdu alternate.
    """

    # Bilingual defaults (ADR-ARISTOTLE §7: Urdu and English side by side)
    primary_language: str = "en"
    alt_language: str = "ur"

    # Bloom target default (1-6 scale, ADR-ARISTOTLE §4)
    bloom_default: int = 3

    # SM-2 spaced repetition interval (seconds) — passed to core VIGIL
    # when ARISTOTLE records a review outcome. Pre-alpha: 24h default.
    review_interval_seconds: int = 86400

    # Mastery threshold (0.0-1.0) — EXAMINER's evaluate() uses this to
    # decide if a concept is mastered. Score >= threshold → mastered.
    mastery_threshold: float = 0.7

    # Maximum chars of each uploaded material to include in the IntakeActor's
    # model context per turn. Papers are the curriculum — the LLM needs to
    # actually read them, not just see a 2000-char abstract preview.
    # 20000 chars ≈ 5000 tokens, which fits comfortably in modern context
    # windows (gpt-4o: 128k, claude-3.5: 200k, gemini-1.5: 1M, even
    # openrouter free tiers handle 32k+). For papers longer than this,
    # the prompt includes a clear truncation notice so the LLM knows the
    # paper continues and can ask the learner to confirm scope.
    material_preview_chars: int = 20000
