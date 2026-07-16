#!/usr/bin/env python3
"""reset_mastery_for_concepts.py — targeted reset of specific mastery rows.

Task 23 Fix 4 (reset tooling). Given a plan_id + an EXPLICIT list of
concept_ids, deletes those specific aristotle_mastery rows so the concepts
get taught normally on the next session (instead of being skipped as
"already known").

USAGE:
    python scripts/reset_mastery_for_concepts.py <plan_id> <concept_id> [<concept_id> ...]
    python scripts/reset_mastery_for_concepts.py <plan_id> --from-file concept_list.txt
    python scripts/reset_mastery_for_concepts.py --dry-run <plan_id> <concept_id> ...

The script REQUIRES an explicit concept_id list — it will NEVER do a blanket
"reset everything for this plan" operation. This is deliberate: there isn't
enough retroactive data to safely automate identifying which rows are
corrupted (see Task 23 Fix 4's rationale). Moses audits the output of
audit_placement_mastery.py, decides which concept_ids look corrupted, and
feeds that explicit list here.

--dry-run: print what would be deleted without actually deleting. Always
run with --dry-run first to confirm the targets.

--from-file: read concept_ids from a file (one per line, # comments allowed,
blank lines ignored). Useful when the audit revealed many corrupted rows.

WHAT THIS SCRIPT DOES (per concept_id):
    1. Reads the current aristotle_mastery row (student_id, concept_id,
       mastered, repetitions, last_score) for confirmation.
    2. Deletes the row.
    3. Prints a before/after summary.

After reset, the concept has no mastery row → _get_mastery_level() returns
0 → SOCRATES.teach() uses the full_worked_example fading mode → the concept
is taught from scratch on the next session.

WHAT THIS SCRIPT DOES NOT DO:
    - Delete the aristotle_placement_event row. The event is an audit
      record — keep it. If you want to also null out the placement_event's
      mastery_achieved flag, do that manually with sqlite3 (the audit trail
      should reflect what actually happened, even if it was wrong).
    - Reset any other table (no touch to aristotle_misconception_log,
      aristotle_predict_event, aristotle_struggle_pattern, etc.). Those
      are separate analytics; reset mastery only.

Exit codes:
    0 — all requested concept_ids were reset (or dry-run printed the plan)
    1 — DB error / no rows matched
    2 — invalid arguments (no concept_ids provided, etc.)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _default_db_path() -> Path:
    """Same resolution as audit_placement_mastery.py — see that file."""
    try:
        import importlib.resources
        pkg_files = importlib.resources.files("aristotle")
        return Path(str(pkg_files)) / "textbook.db"
    except Exception:
        script_dir = Path(__file__).resolve().parent
        return script_dir.parent / "aristotle" / "textbook.db"


def _read_concept_ids_from_file(path: Path) -> list[str]:
    """Read concept_ids from a file, one per line. # comments + blank
    lines are ignored. Whitespace-stripped."""
    ids: list[str] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Targeted reset of specific aristotle_mastery rows. REQUIRES an "
            "explicit concept_id list — never a blanket plan-wide reset."
        ),
    )
    parser.add_argument(
        "plan_id",
        help="The learning plan ID the concept_ids belong to (for logging/audit only — "
             "the deletion is keyed on (student_id='definer', concept_id) since "
             "aristotle_mastery has no plan_id column).",
    )
    parser.add_argument(
        "concept_ids",
        nargs="*",
        help="One or more concept_ids to reset. Required unless --from-file is used.",
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help="Read concept_ids from a file (one per line, # comments allowed).",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to the aristotle:textbook corpus DB. Defaults to the "
             "importlib.resources-resolved path.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without actually deleting.",
    )
    parser.add_argument(
        "--student-id",
        default="definer",
        help="Student ID filter (default: definer — pre-alpha single-tenant).",
    )
    args = parser.parse_args()

    # Collect concept_ids from positional args + --from-file.
    concept_ids: list[str] = list(args.concept_ids)
    if args.from_file is not None:
        concept_ids.extend(_read_concept_ids_from_file(args.from_file))

    # Dedup while preserving order.
    seen: set[str] = set()
    unique_ids: list[str] = []
    for cid in concept_ids:
        if cid not in seen:
            seen.add(cid)
            unique_ids.append(cid)
    concept_ids = unique_ids

    if not concept_ids:
        parser.error(
            "No concept_ids provided. Pass them as positional args or use --from-file. "
            "This script NEVER does a blanket plan-wide reset — you must specify "
            "exactly which concepts to reset."
        )
        return 2  # parser.error exits, but satisfy the type checker

    db_path = args.db_path or _default_db_path()
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    print(f"# Reset request: plan_id={args.plan_id}", file=sys.stderr)
    print(f"# {len(concept_ids)} concept_id(s) to reset: {concept_ids}", file=sys.stderr)
    print(f"# Student ID: {args.student_id}", file=sys.stderr)
    print(f"# DB: {db_path}", file=sys.stderr)
    print(f"# Dry run: {args.dry_run}", file=sys.stderr)
    print(file=sys.stderr)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row

        # Read the current state of each target row (for confirmation + log).
        rows_to_reset: list[sqlite3.Row] = []
        missing: list[str] = []
        for cid in concept_ids:
            cur = conn.execute(
                "SELECT student_id, concept_id, mastered, repetitions, "
                "last_score, updated_at "
                "FROM aristotle_mastery "
                "WHERE student_id = ? AND concept_id = ?",
                (args.student_id, cid),
            )
            row = cur.fetchone()
            if row is None:
                missing.append(cid)
            else:
                rows_to_reset.append(row)

        if missing:
            print(
                f"# WARNING: {len(missing)} concept_id(s) have no mastery row "
                f"to reset (already absent — nothing to do for these): {missing}",
                file=sys.stderr,
            )
            print(file=sys.stderr)

        if not rows_to_reset:
            print("# No mastery rows matched any of the requested concept_ids. Nothing to do.", file=sys.stderr)
            return 0

        # Print the before-state.
        print("# Before-state (what will be deleted):")
        print(
            "concept_id\tmastered\trepetitions\tlast_score\tupdated_at"
        )
        for row in rows_to_reset:
            print(
                f"{row['concept_id']}\t{row['mastered']}\t"
                f"{row['repetitions']}\t{row['last_score']}\t{row['updated_at']}"
            )
        print(file=sys.stderr)

        if args.dry_run:
            print(
                f"# DRY RUN — would delete {len(rows_to_reset)} mastery row(s). "
                f"Re-run without --dry-run to actually delete.",
                file=sys.stderr,
            )
            return 0

        # Confirm before deleting (interactive — skip if stdin is not a tty,
        # e.g. piped input. The --dry-run flag is the non-interactive safety).
        if sys.stdin.isatty():
            print(
                f"\nAbout to DELETE {len(rows_to_reset)} mastery row(s). "
                f"This cannot be undone. Type 'yes' to confirm: ",
                file=sys.stderr,
                end="",
                flush=True,
            )
            response = input().strip().lower()
            if response != "yes":
                print("# Aborted — no rows deleted.", file=sys.stderr)
                return 0

        # Delete in a single transaction.
        deleted = 0
        try:
            for row in rows_to_reset:
                conn.execute(
                    "DELETE FROM aristotle_mastery "
                    "WHERE student_id = ? AND concept_id = ?",
                    (args.student_id, row["concept_id"]),
                )
                deleted += 1
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            print(f"# ERROR: delete failed (rolled back): {exc}", file=sys.stderr)
            return 1

        print(
            f"# Deleted {deleted} mastery row(s). The concepts will be taught "
            f"from scratch on the next session (no mastery row → "
            f"_get_mastery_level returns 0 → full_worked_example fading mode).",
            file=sys.stderr,
        )
        print(
            "# NOTE: aristotle_placement_event rows were NOT touched — they're "
            "an audit record of what happened. If you want to also correct the "
            "placement_event's mastery_achieved flag, do that manually with sqlite3.",
            file=sys.stderr,
        )
        return 0
    except sqlite3.Error as exc:
        print(f"ERROR: DB query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
