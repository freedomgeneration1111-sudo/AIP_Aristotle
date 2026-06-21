"""Aristotle CLI — run via `python -m aristotle.cli`.

Provides commands for ingesting concepts, running tutoring sessions, and
checking extension health. The CLI constructs a minimal container with a
real CorpusRegistry (same pattern as the test fixtures) so it can reach
the extension's corpus stores without the full lifespan.

Usage:
  python -m aristotle.cli ingest concepts_sample.yaml
  python -m aristotle.cli list-concepts
  python -m aristotle.cli session newton_first_law
  python -m aristotle.cli health

For the full platform CLI (aip init, aip status, etc.), use `aip` from
AIP_Brain. This CLI is ARISTOTLE-specific.

Layer: imports click + aip.foundation.protocols.actors (ActorContext) +
aristotle's own modules. Accesses the CorpusRegistry via the platform's
adapter layer (aip.adapter.corpus_registry) — this is allowed because the
CLI is the composition root (same pattern as AIP_Brain's CLI accessing
stores directly).

Wait — the boundary test forbids aip.adapter imports outside the allowlist.
The CLI needs to construct a CorpusRegistry. Options:
1. The CLI is part of the platform, not the extension (wrong — ARISTOTLE
   is a separate package).
2. The CLI accesses the registry via the running server's API (the
   /health/extensions endpoint tells it the extension is mounted; then
   it calls /aristotle/concepts etc.).
3. The CLI constructs its own minimal container (imports CorpusRegistry
   directly).

Option 2 is cleanest for pre-alpha: the CLI is a thin client that talks
to the running server via HTTP. No boundary violation, no duplicate
container construction. The server has the real container; the CLI just
calls the API.

This file is the HTTP-client CLI. It requires the AIP_Brain server to be
running (./start.sh from AIP_Brain).
"""

from __future__ import annotations

import sys

import click
import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _client(base_url: str) -> httpx.Client:
    """Build an HTTP client for the AIP_Brain server."""
    return httpx.Client(base_url=base_url, timeout=60.0)


@click.group()
@click.option("--base-url", default=DEFAULT_BASE_URL, help="AIP_Brain server URL")
@click.pass_context
def cli(ctx: click.Context, base_url: str) -> None:
    """Aristotle — Adaptive Tutor CLI.

    Requires the AIP_Brain server to be running (./start.sh from AIP_Brain).
    This CLI is a thin HTTP client that calls the server's Aristotle API routes.
    """
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url


@cli.command("health")
@click.pass_context
def health(ctx: click.Context) -> None:
    """Check extension health + Aristotelan status."""
    with _client(ctx.obj["base_url"]) as client:
        try:
            resp = client.get("/api/v1/health/extensions")
            resp.raise_for_status()
            data = resp.json()
            click.echo(f"Host running: {data.get('host_running', False)}")
            extensions = data.get("extensions", [])
            for ext in extensions:
                click.echo(f"  {ext['id']} v{ext['version']} — state={ext['state']}")
                if ext.get("failures"):
                    for f in ext["failures"]:
                        click.echo(
                            f"    failure: {f['stage']}/{f['contribution']}: {f['reason']}"
                        )
        except httpx.ConnectError:
            click.echo(
                "ERROR: Cannot connect to AIP_Brain server. Is it running? (./start.sh)",
                err=True,
            )
            sys.exit(1)
        except Exception as exc:
            click.echo(f"ERROR: {exc}", err=True)
            sys.exit(1)


@cli.command("list-concepts")
@click.pass_context
def list_concepts(ctx: click.Context) -> None:
    """List all ingested concepts in the textbook corpus."""
    with _client(ctx.obj["base_url"]) as client:
        try:
            resp = client.get("/aristotle/concepts")
            resp.raise_for_status()
            concepts = resp.json()
            if not concepts:
                click.echo(
                    "No concepts ingested. Run: python -m aristotle.cli ingest <yaml_file>"
                )
                return
            for c in concepts:
                prereq = (
                    f" (requires: {c['prerequisite_concept_id']})"
                    if c.get("prerequisite_concept_id")
                    else ""
                )
                click.echo(
                    f"  {c['id']}: {c['topic']} — bloom={c['bloom_target']}{prereq}"
                )
        except httpx.ConnectError:
            click.echo("ERROR: Cannot connect to AIP_Brain server.", err=True)
            sys.exit(1)
        except Exception as exc:
            click.echo(f"ERROR: {exc}", err=True)
            sys.exit(1)


@cli.command("ingest")
@click.argument("yaml_path", type=click.Path(exists=True))
@click.pass_context
def ingest(ctx: click.Context, yaml_path: str) -> None:
    """Ingest concepts from a YAML file into the textbook corpus."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        concepts_data = f.read()

    with _client(ctx.obj["base_url"]) as client:
        try:
            resp = client.post(
                "/aristotle/ingest",
                json={"yaml_content": concepts_data},
            )
            resp.raise_for_status()
            result = resp.json()
            click.echo(f"Ingested: {result['ingested']}")
            click.echo(f"Skipped: {result['skipped']}")
            if result.get("errors"):
                click.echo("Errors:")
                for e in result["errors"]:
                    click.echo(f"  {e}")
        except httpx.ConnectError:
            click.echo("ERROR: Cannot connect to AIP_Brain server.", err=True)
            sys.exit(1)
        except Exception as exc:
            click.echo(f"ERROR: {exc}", err=True)
            sys.exit(1)


@cli.command("session")
@click.argument("concept_id")
@click.option(
    "--answer",
    "-a",
    multiple=True,
    help="Pre-provided answer(s) for quiz steps. If not provided, runs in interactive mode.",
)
@click.pass_context
def session(
    ctx: click.Context,
    concept_id: str,
    answer: tuple[str, ...],
) -> None:
    """Run a tutoring session for a concept.

    If --answer is provided, runs the full session non-interactively with
    those answers. If not provided, runs step-by-step interactively.
    """
    with _client(ctx.obj["base_url"]) as client:
        try:
            if answer:
                # Non-interactive: full session with pre-provided answers
                resp = client.post(
                    "/aristotle/session/run",
                    json={
                        "concept_id": concept_id,
                        "answers": list(answer),
                    },
                )
                resp.raise_for_status()
                result = resp.json()
                _print_session_result(result)
            else:
                # Interactive: step-by-step
                _run_interactive_session(client, concept_id)
        except httpx.ConnectError:
            click.echo("ERROR: Cannot connect to AIP_Brain server.", err=True)
            sys.exit(1)
        except Exception as exc:
            click.echo(f"ERROR: {exc}", err=True)
            sys.exit(1)


def _run_interactive_session(client: httpx.Client, concept_id: str) -> None:
    """Run a step-by-step interactive session."""
    # Start the session
    resp = client.post(
        "/aristotle/session/start",
        json={"concept_id": concept_id},
    )
    resp.raise_for_status()
    session = resp.json()
    click.echo(f"\n=== Aristotle Tutoring Session: {concept_id} ===\n")

    while session["state"] != "SESSION_COMPLETE":
        step_resp = client.post(
            "/aristotle/session/step",
            json={"session": session, "student_input": ""},
        )
        step_resp.raise_for_status()
        step = step_resp.json()
        session = step["session"]

        if step.get("output"):
            click.echo(f"\n{step['output']}")

        if session["state"] in ("PROBE", "QUIZ"):
            answer = click.prompt("\nYour answer", type=str)
            step_resp = client.post(
                "/aristotle/session/step",
                json={"session": session, "student_input": answer},
            )
            step_resp.raise_for_status()
            step = step_resp.json()
            session = step["session"]
            if step.get("output"):
                click.echo(f"\n{step['output']}")

    click.echo("\n=== Session Complete ===")
    click.echo(f"Mastered: {session['mastered']}")
    click.echo(f"Score: {session['last_score']}")


def _print_session_result(result: dict) -> None:
    """Print a full-session result."""
    click.echo("\n=== Session Complete ===")
    click.echo(f"Concept: {result['concept_id']}")
    click.echo(f"Mastered: {result['mastered']}")
    click.echo(f"Final score: {result['last_score']}")
    if result.get("steps"):
        click.echo(f"\nSteps ({len(result['steps'])}):")
        for i, step in enumerate(result["steps"], 1):
            click.echo(f"  {i}. {step['state']}: {step.get('output', '')[:100]}...")


if __name__ == "__main__":
    cli()
