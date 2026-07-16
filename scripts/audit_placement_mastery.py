#!/usr/bin/env python3
"""audit_placement_mastery.py — read-only audit of placement-scored mastery rows.

Task 23 Fix 4 (audit tooling). Given a plan_id, prints every aristotle_mastery
row with mastered=1 joined against its originating aristotle_placement_event
row (same plan_id + concept_id), showing concept_id, score, assessed_at, and
(if available — only rows written after M010 shipped) the raw student_answer.

USAGE:
    python scripts/audit_placement_mastery.py <plan_id> [--db-path PATH]

The DB path defaults to the aristotle:textbook corpus DB (resolved the same
way the ExtensionHost resolves it: importlib.resources on the aristotle
package, then <pkg_dir>/textbook.db). Override with --db-path for non-default
installs.

This script is READ-ONLY. It does not modify any rows. The output is
designed for Moses to eyeball which concept_ids look corrupted (e.g. a
mastered=1 row whose student_answer is "no. teach me about it.") and then
feed that explicit list to reset_mastery_for_concepts.py.

Output format (one row per line, tab-separated for easy grep/awk):

    concept_id<TAB>score<TAB>assessed_at<TAB>student_answer

Rows with no originating placement_event (mastery was set by a tutoring
session, not placement) are printed with a note. Rows whose placement_event
predates M010 (student_answer is NULL) are printed with <NULL — pre-M010>.

Exit codes:
    0 — query succeeded (rows may or may not have been found)
    1 — DB not found / query failed
    2 — invalid arguments
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _default_db_path() -> Path:
    """Resolve the aristotle:textbook corpus DB path the same way the
    ExtensionHost does: importlib.resources on the aristotle package, then
    <pkg_dir>/textbook.db.

    Falls back to the source-tree path if importlib.resources can't find
    the package (e.g. running from a checkout that isn't pip-installed).
    """
    try:
        import importlib.resources
        pkg_files = importlib.resources.files("aristotle")
        return Path(str(pkg_files)) / "textbook.db"
    except Exception:
        # Fallback: assume the script is in <repo>/scripts/ and the DB
        # is at <repo>/aristotle/textbook.db (editable-install layout).
        script_dir = Path(__file__).resolve().parent
        return script_dir.parent / "aristotle" / "textbook.db"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit of placement-scored mastery rows for a plan.",
    )
    parser.add_argument(
        "plan_id",
        help="The learning plan ID to audit (from aristotle_learning_plan.id).",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to the aristotle:textbook corpus DB. Defaults to the "
             "importlib.resources-resolved path (or <repo>/aristotle/textbook.db).",
    )
    args = parser.parse_args()

    db_path = args.db_path or _default_db_path()
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        print(
            "If your install uses a non-default DB location, pass --db-path.",
            file=sys.stderr,
        )
        return 1

    print(f"# Audit: placement-scored mastery rows for plan_id={args.plan_id}", file=sys.stderr)
    print(f"# DB: {db_path}", file=sys.stderr)
    print(file=sys.stderr)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row

        # Check whether student_answer column exists (M010 applied).
        cur = conn.execute("PRAGMA table_info(aristotle_placement_event)")
        cols = [row[1] for row in cur.fetchall()]
        has_student_answer = "student_answer" in cols
        if not has_student_answer:
            print(
                "# WARNING: student_answer column not found on "
                "aristotle_placement_event — M010 not applied. Rows will "
                "show <NULL — pre-M010> for the answer text.",
                file=sys.stderr,
            )
            print(file=sys.stderr)

        # Join aristotle_mastery (mastered=1) against aristotle_placement_event
        # on (plan_id, concept_id). LEFT JOIN so mastery rows with no
        # originating placement_event are still shown (they were set by a
        # tutoring session, not placement — note that in the output).
        if has_student_answer:
            query = """
                SELECT
                    m.concept_id,
                    m.last_score,
                    m.repetitions,
                    m.updated_at,
                    pe.score AS placement_score,
                    pe.assessed_at,
                    pe.student_answer
                FROM aristotle_mastery m
                LEFT JOIN aristotle_placement_event pe
                    ON pe.concept_id = m.concept_id
                   AND pe.plan_id = ?
                WHERE m.mastered = 1
                ORDER BY pe.assessed_at ASC, m.concept_id ASC
            """
        else:
            query = """
                SELECT
                    m.concept_id,
                    m.last_score,
                    m.repetitions,
                    m.updated_at,
                    pe.score AS placement_score,
                    pe.assessed_at,
                    NULL AS student_answer
                FROM aristotle_mastery m
                LEFT JOIN aristotle_placement_event pe
                    ON pe.concept_id = m.concept_id
                   AND pe.plan_id = ?
                WHERE m.mastered = 1
                ORDER BY pe.assessed_at ASC, m.concept_id ASC
            """
        cur = conn.execute(query, (args.plan_id,))
        rows = cur.fetchall()

        if not rows:
            print(
                f"# No mastered=1 rows found in aristotle_mastery (no plan_id "
                f"filter on mastery itself — this audits ALL mastered rows, "
                f"joined against placement events for plan {args.plan_id}).",
                file=sys.stderr,
            )
            return 0

        # Header (stdout — the actual data the user will eyeball).
        print(
            "concept_id\tmastery_last_score\tmastery_reps\t"
            "placement_score\tassessed_at\tstudent_answer"
        )

        count_with_placement = 0
        count_without_placement = 0
        for row in rows:
            concept_id = row["concept_id"]
            m_score = row["last_score"]
            m_reps = row["repetitions"]
            p_score = row["placement_score"]
            assessed_at = row["assessed_at"]
            student_answer = row["student_answer"]

            if p_score is None and assessed_at is None:
                # No matching placement_event — mastery was set by a
                # tutoring session, not placement.
                count_without_placement += 1
                ans_display = "<no placement event — set by tutoring session>"
                print(
                    f"{concept_id}\t{m_score}\t{m_reps}\t"
                    f"-\t-\t{ans_display}"
                )
            else:
                count_with_placement += 1
                if student_answer is None:
                    ans_display = "<NULL — pre-M010>"
                else:
                    # Tab/newline-collapse so the row stays on one line.
                    ans_display = " ".join(
                        str(student_answer).replace("\t", " ").replace("\n", " ").split()
                    )
                print(
                    f"{concept_id}\t{m_score}\t{m_reps}\t"
                    f"{p_score}\t{assessed_at}\t{ans_display}"
                )

        print(file=sys.stderr)
        print(
            f"# Summary: {len(rows)} mastered=1 row(s) total. "
            f"{count_with_placement} with a matching placement_event for "
            f"plan {args.plan_id}, {count_without_placement} without "
            f"(set by tutoring sessions).",
            file=sys.stderr,
        )
        print(
            "# Rows with student_answer containing decline phrases (e.g. "
            "'teach me', 'i don't know') are likely corrupted — feed their "
            "concept_ids to reset_mastery_for_concepts.py to reset.",
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
