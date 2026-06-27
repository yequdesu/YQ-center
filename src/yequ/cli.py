"""YeQu Center CLI — management tool.

Config: ~/.config/yequ/config.toml
  [center]
  base_url = "http://127.0.0.1:9800"
"""

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import click
import httpx


def _load_config() -> dict[str, Any]:
    """Load config from ~/.config/yequ/config.toml."""
    config_path = Path.home() / ".config" / "yequ" / "config.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    return {}


def _get_client() -> httpx.Client:
    """Create an HTTP client from config."""
    config = _load_config()
    base_url = config.get("center", {}).get("base_url", "http://127.0.0.1:9800")
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=30)


def _print_json(data: object) -> None:
    """Pretty print JSON."""
    click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ── CLI entry ──


@click.group()
def cli() -> None:
    """YeQu Center — infrastructure control CLI."""
    pass


# ── health ──


@cli.command()
def health() -> None:
    """Check Center health."""
    try:
        with _get_client() as c:
            r = c.get("/healthz")
            r.raise_for_status()
            data = r.json()
            status = data.get("status", "unknown")
            db = data.get("database", "unknown")
            if status == "ok":
                click.secho(f"Center: {status}  DB: {db}", fg="green")
            else:
                click.secho(f"Center: {status}  DB: {db}", fg="yellow")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── nodes ──


@cli.group()
def nodes() -> None:
    """Manage nodes."""
    pass


@nodes.command("list")
def nodes_list() -> None:
    """List all nodes."""
    try:
        with _get_client() as c:
            r = c.get("/admin/nodes")
            r.raise_for_status()
        data = r.json()
        if not data:
            click.echo("No nodes found.")
            return
        for n in data:
            color = {"online": "green", "degraded": "yellow", "offline": "red"}.get(
                n["status"], "white"
            )
            click.echo(
                f"{n['node_id']:<20} {click.style(n['status'], fg=color):<14} "
                f"role={n['role']}  locality={n['locality']}  "
                f"last_seen={n.get('last_seen_at', 'never')}"
            )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@nodes.command("show")
@click.argument("node_id")
def nodes_show(node_id: str) -> None:
    """Show node details."""
    try:
        with _get_client() as c:
            r = c.get(f"/admin/nodes/{node_id}")
            r.raise_for_status()
        _print_json(r.json())
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── capabilities ──


@cli.group()
def capabilities() -> None:
    """Query capabilities."""
    pass


@capabilities.command("list")
@click.option("--node", "-n", "node_id", default=None, help="Filter by node_id")
def capabilities_list(node_id: str | None) -> None:
    """List capabilities."""
    try:
        with _get_client() as c:
            params = {}
            if node_id:
                params["node_id"] = node_id
            r = c.get("/admin/capabilities", params=params)
            r.raise_for_status()
        data = r.json()
        if not data:
            click.echo("No capabilities found.")
            return
        for cap in data:
            risk_color = {"safe": "green", "maintenance": "yellow"}.get(cap.get("risk", ""), "red")
            click.echo(
                f"{cap['name']:<35} type={cap['capability_type']:<10} "
                f"risk={click.style(cap.get('risk', '-'), fg=risk_color):<4} "
                f"plugin={cap['plugin_id']}"
            )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── invoke ──


@cli.command()
@click.argument("function_name")
@click.option("--node", "-n", "node_id", required=True, help="Target node")
@click.option("--input", "-i", "input_json", default="{}", help="Input payload (JSON)")
@click.option("--timeout", "-t", default=30, help="Job timeout in seconds")
@click.option("--wait/--no-wait", default=True, help="Wait for job completion")
def invoke(
    function_name: str,
    node_id: str,
    input_json: str,
    timeout: int,
    wait: bool,
) -> None:
    """Create an Invocation on a target node."""
    try:
        payload = json.loads(input_json)
    except json.JSONDecodeError as e:
        click.secho(f"Invalid JSON: {e}", fg="red")
        sys.exit(1)

    try:
        with _get_client() as c:
            r = c.post(
                "/admin/invocations",
                json={
                    "function_name": function_name,
                    "target_node_id": node_id,
                    "input_payload": payload,
                    "timeout_sec": timeout,
                },
            )
            r.raise_for_status()
        data = r.json()
        inv_id = data["invocation_id"]
        job_id = data["job_id"]
        click.secho(f"Invocation: {inv_id}", fg="cyan")
        click.secho(f"Job:        {job_id}  status={data['job_status']}", fg="cyan")

        if wait:
            import time

            with _get_client() as c:
                for _ in range(timeout * 2):
                    time.sleep(1)
                    r2 = c.get(f"/admin/jobs/{job_id}")
                    r2.raise_for_status()
                    j = r2.json()
                    if j["status"] in ("succeeded", "failed", "cancelled", "timeout"):
                        color = "green" if j["status"] == "succeeded" else "red"
                        click.secho(f"Result: {j['status']}", fg=color)
                        if j.get("output"):
                            _print_json(j["output"])
                        return
                click.secho("Timeout waiting for job completion", fg="yellow")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── jobs ──


@cli.group()
def jobs() -> None:
    """Query jobs."""
    pass


@jobs.command("list")
@click.option("--node", "-n", "node_id", default=None)
@click.option("--status", "-s", default=None)
@click.option("--limit", "-l", default=20)
def jobs_list(node_id: str | None, status: str | None, limit: int) -> None:
    """List jobs."""
    try:
        with _get_client() as c:
            params: dict[str, int | str] = {"limit": limit}
            if node_id:
                params["node_id"] = node_id
            if status:
                params["status"] = status
            r = c.get("/admin/jobs", params=params)
            r.raise_for_status()
        data = r.json()
        if not data:
            click.echo("No jobs found.")
            return
        for j in data:
            color = {
                "succeeded": "green",
                "failed": "red",
                "running": "yellow",
                "cancelled": "magenta",
                "timeout": "red",
            }.get(j["status"], "white")
            click.echo(
                f"{j['job_id'][:20]:<22} {click.style(j['status'], fg=color):<14} "
                f"func={j['function_name']}  node={j['node_id']}"
            )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@jobs.command("show")
@click.argument("job_id")
def jobs_show(job_id: str) -> None:
    """Show job details."""
    try:
        with _get_client() as c:
            r = c.get(f"/admin/jobs/{job_id}")
            r.raise_for_status()
        _print_json(r.json())
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── invocations ──


@cli.group()
def invocations() -> None:
    """Query invocations."""
    pass


@invocations.command("show")
@click.argument("invocation_id")
def invocations_show(invocation_id: str) -> None:
    """Show invocation details with associated jobs."""
    try:
        with _get_client() as c:
            r = c.get(f"/admin/invocations/{invocation_id}")
            r.raise_for_status()
        data = r.json()
        click.echo(f"Invocation: {data['invocation_id']}")
        click.echo(f"Function:   {data['function_name']}")
        click.echo(f"Status:     {data['status']}")
        click.echo(f"Job count:  {len(data.get('jobs', []))}")
        if data.get("result"):
            click.echo(f"Result:     {json.dumps(data['result'], default=str)}")
        for j in data.get("jobs", []):
            color = {"succeeded": "green", "failed": "red"}.get(j["status"], "white")
            click.echo(f"  [{click.style(j['status'], fg=color)}] {j['job_id']}")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── timeline ──


@cli.group()
def timeline() -> None:
    """Query timeline events."""
    pass


@timeline.command("tail")
@click.option("--node", "-n", "node_id", default=None)
@click.option("--job", "-j", "job_id", default=None)
@click.option("--limit", "-l", default=20)
def timeline_tail(node_id: str | None, job_id: str | None, limit: int) -> None:
    """Show recent timeline events."""
    try:
        with _get_client() as c:
            params: dict[str, int | str] = {"limit": limit}
            if node_id:
                params["node_id"] = node_id
            if job_id:
                params["job_id"] = job_id
            r = c.get("/admin/timeline", params=params)
            r.raise_for_status()
        data = r.json()
        if not data:
            click.echo("No events found.")
            return
        for e in data:
            ts = e.get("timestamp", "?")[:19] if e.get("timestamp") else "?"
            click.echo(
                f"[{e['global_seq']:>6}] {ts}  {e['event_type']:<30} "
                f"node={e.get('node_id', '-'):<20} job={e.get('job_id', '-'):<22}"
            )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── agent ──


@cli.group()
def agent() -> None:
    """Agent operations."""
    pass


@agent.command("session")
@click.option("--create/--no-create", default=True, help="Create a new session")
@click.option("--mode", default="auto", help="Execution mode")
@click.option("--actor", default="cli", help="Actor ID")
def agent_session(create: bool, mode: str, actor: str) -> None:
    """Create an Agent Session."""
    try:
        with _get_client() as c:
            r = c.post(
                "/agent/sessions",
                json={
                    "actor_id": actor,
                    "execution_mode": mode,
                },
            )
            r.raise_for_status()
        data = r.json()
        click.secho(f"Session created: {data['session_id']}", fg="green")
        click.echo(f"Mode: {data['execution_mode']}")
        click.echo(
            f"Max depth: {data['max_depth']}  Steps: {data['max_steps']}  "
            f"Duration: {data['max_total_duration_sec']}s"
        )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@agent.command("invoke")
@click.argument("session_id")
@click.argument("prompt")
@click.option("--provider", "-p", default="fake", help="Provider name")
@click.option("--mode", default="auto", help="Execution mode")
def agent_invoke(session_id: str, prompt: str, provider: str, mode: str) -> None:
    """Invoke an Agent Provider."""
    try:
        with _get_client() as c:
            r = c.post(
                "/agent/invoke",
                json={
                    "session_id": session_id,
                    "provider_name": provider,
                    "prompt": prompt,
                    "execution_mode": mode,
                },
            )
            r.raise_for_status()
        data = r.json()
        if data["success"]:
            click.secho("Success", fg="green")
        else:
            click.secho(
                f"Error: {data.get('error_code', 'unknown')} - {data.get('error_message', '')}",
                fg="red",
            )
        if data.get("function_calls"):
            for fc in data["function_calls"]:
                click.echo(f"  → {fc['name']}")
        if data.get("output"):
            _print_json(data["output"])
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# ── config init ──


@cli.command("config-init")
def config_init() -> None:
    """Create a default config file."""
    config_dir = Path.home() / ".config" / "yequ"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    if config_path.exists():
        click.echo(f"Config already exists: {config_path}")
        return
    config_path.write_text(
        '# YeQu Center CLI config\n[center]\nbase_url = "http://127.0.0.1:9800"\n'
    )
    click.secho(f"Created: {config_path}", fg="green")


if __name__ == "__main__":
    cli()
