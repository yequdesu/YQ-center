from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from .config import Settings, init_config, load_settings, validate_config
from .daemon import WinNodeDaemon
from .diagnostics import export_diagnostics
from .l2_matrix import run_sync as run_l2_matrix_sync
from .l2_matrix import summarize as summarize_l2_matrix
from .l2b_matrix import run_sync as run_l2b_matrix_sync
from .l2b_matrix import summarize as summarize_l2b_matrix
from .logging_config import setup_logging
from .plugins import FakeSystemPlugin
from .state_store import StateStore
from .tester import IntegrationTester, run_for_duration

app = typer.Typer(help="Windows YQP node client and Center/Agent tester.")
config_app = typer.Typer(help="Config management commands.")
app.add_typer(config_app, name="config")

cache_app = typer.Typer(help="Local job result cache commands.")
app.add_typer(cache_app, name="cache")

diagnostics_app = typer.Typer(help="Diagnostics commands.")
app.add_typer(diagnostics_app, name="diagnostics")


def _load_settings_with_logging(config: str | None = None) -> Settings:
    settings = load_settings(config)
    setup_logging(settings.paths.log_dir)
    return settings


@config_app.command("init")
def config_init(
    output: Annotated[str, typer.Option("--output", "-o")] = "config.local.yaml",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Initialize a new configuration file."""
    try:
        result = init_config(output, overwrite=overwrite)
        print(f"Config written to {result['path']}")
    except FileExistsError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from e


@config_app.command("validate")
def config_validate(
    config: Annotated[str, typer.Option("--config", "-c")] = "config.local.yaml",
) -> None:
    """Validate a configuration file."""
    try:
        result = validate_config(config)
        print(f"Config at {result['path']} is valid.")
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from e


@app.command()
def gui(
    config: Annotated[str, typer.Option("--config", "-c")] = "config.local.yaml",
) -> None:
    """Open the GUI window."""
    from .gui import launch_gui
    launch_gui(config)


@app.command("user-worker")
def user_worker(
    config: Annotated[str, typer.Option("--config", "-c")] = "config.local.yaml",
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 9817,
) -> None:
    """Run the user-session worker used by Hybrid mode."""
    from .user_worker import serve

    serve(config=config, host=host, port=port)


@app.command()
def status(
    config: Annotated[str, typer.Option("--config", "-c")] = "config.local.yaml",
) -> None:
    """Show local node status."""
    from .service import get_service_status
    svc = get_service_status()
    print(json.dumps({
        "configured": True,
        "config_path": config,
        "service": svc,
    }, ensure_ascii=False, indent=2))


@app.command()
def capabilities(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Show registered capabilities."""
    settings = load_settings(config)
    plugin = FakeSystemPlugin(
        settings.l2_policy,
        settings.transfer.yq_croc,
        center_base_url=settings.center_base_url,
        node_token=settings.node_token,
        request_timeout_sec=settings.request_timeout_sec,
    )
    manifest = plugin.manifest()
    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))


@diagnostics_app.command("export")
def diagnostics_export(
    config: Annotated[str, typer.Option("--config", "-c")] = "config.local.yaml",
) -> None:
    """Export a diagnostics package."""
    settings = load_settings(config)
    zip_path = export_diagnostics(
        config_path=config,
        log_dir=settings.paths.log_dir,
        data_dir=settings.paths.data_dir,
    )
    print(f"Diagnostics exported to {zip_path}")


@cache_app.command("list")
def cache_list(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
) -> None:
    """List recent job results from local cache."""
    settings = load_settings(config)
    store = StateStore(f"{settings.paths.data_dir}/node_state.sqlite3")
    try:
        jobs = store.get_recent_jobs(limit)
        print(json.dumps(jobs, ensure_ascii=False, indent=2))
    finally:
        store.close()


@cache_app.command("retry-unreported")
def cache_retry_unreported(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Retry reporting unreported job results to Center."""

    async def _run() -> None:
        settings = load_settings(config)
        store = StateStore(f"{settings.paths.data_dir}/node_state.sqlite3")
        daemon = WinNodeDaemon(settings)
        try:
            unreported = store.get_unreported()
            if not unreported:
                print("No unreported results.")
                return
            print(f"Found {len(unreported)} unreported results. Reconciling...")
            daemon.state_store = store
            await daemon.hello()
            result = await daemon.report_unreported_results()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            store.close()
            await daemon.close()

    asyncio.run(_run())


@cache_app.command("flush-reported")
def cache_flush_reported(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    ttl_hours: Annotated[float, typer.Option("--ttl-hours")] = 24,
) -> None:
    """Flush reported job results older than TTL."""
    settings = load_settings(config)
    store = StateStore(f"{settings.paths.data_dir}/node_state.sqlite3")
    try:
        count = store.flush_reported(ttl_hours)
        print(f"Flushed {count} reported job results (TTL: {ttl_hours}h).")
    finally:
        store.close()


@app.command()
def hello(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Send node.hello once."""

    async def _run() -> None:
        daemon = WinNodeDaemon(_load_settings_with_logging(config))
        try:
            response = await daemon.hello()
            print(json.dumps(response, ensure_ascii=False, indent=2))
        finally:
            await daemon.close()

    asyncio.run(_run())


@app.command()
def register(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Register capabilities once."""

    async def _run() -> None:
        daemon = WinNodeDaemon(_load_settings_with_logging(config))
        try:
            response = await daemon.register_capabilities()
            print(json.dumps(response, ensure_ascii=False, indent=2))
        finally:
            await daemon.close()

    asyncio.run(_run())


@app.command("upload-artifact")
def upload_artifact(
    path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False)],
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    content_type: Annotated[str | None, typer.Option("--content-type")] = None,
    title: Annotated[str | None, typer.Option("--title")] = None,
    job_id: Annotated[str | None, typer.Option("--job-id")] = None,
) -> None:
    """Upload a local file to Center through YQP artifact.upload."""

    async def _run() -> None:
        daemon = WinNodeDaemon(_load_settings_with_logging(config))
        try:
            response = await daemon.upload_artifact_file(
                path,
                content_type=content_type,
                title=title,
                job_id=job_id,
                summary={"purpose": "manual artifact upload"},
                metadata={"cli": "upload-artifact"},
            )
            print(json.dumps(response, ensure_ascii=False, indent=2))
        finally:
            await daemon.close()

    asyncio.run(_run())


@app.command("artifact-smoke")
def artifact_smoke(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Create and upload a small text artifact to Center."""

    async def _run() -> None:
        settings = _load_settings_with_logging(config)
        daemon = WinNodeDaemon(settings)
        smoke_text = (
            "YeQu Windows Node artifact.upload smoke\n"
            f"node_id={settings.node_id}\n"
        )
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".txt",
                prefix="yequ-artifact-smoke-",
                delete=False,
            ) as file:
                file.write(smoke_text)
                smoke_path = Path(file.name)
            try:
                response = await daemon.upload_artifact_file(
                    smoke_path,
                    content_type="text/plain",
                    title="winclient-artifact-smoke.txt",
                    summary={"purpose": "artifact.upload smoke test"},
                    metadata={"cli": "artifact-smoke", "node_runtime": "windows"},
                )
            finally:
                smoke_path.unlink(missing_ok=True)
            print(json.dumps(response, ensure_ascii=False, indent=2))
        finally:
            await daemon.close()

    asyncio.run(_run())


@app.command()
def once(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Run one pass of hello/register/heartbeat/signal/reconcile/poll."""

    async def _run() -> None:
        settings = _load_settings_with_logging(config)
        tester = IntegrationTester(settings)
        try:
            results = await tester.run_all(include_agent=False)
            for result in results:
                status = "OK" if result.ok else "FAIL"
                print(f"[{status}] {result.name}")
                if result.detail:
                    print(f"  {result.detail}")
        finally:
            await tester.close()

    asyncio.run(_run())


@app.command()
def run(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Run the node daemon loop until Ctrl+C."""

    async def _run() -> None:
        daemon = WinNodeDaemon(_load_settings_with_logging(config))
        try:
            await daemon.run()
        finally:
            await daemon.close()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("stopped")


@app.command()
def smoke(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
    include_agent: Annotated[bool, typer.Option("--agent/--no-agent")] = True,
) -> None:
    """Run Center and Agent smoke checks using configured routes."""

    async def _run() -> None:
        settings = _load_settings_with_logging(config)
        tester = IntegrationTester(settings)
        try:
            results = await tester.run_all(include_agent=include_agent)
            for result in results:
                status = "OK" if result.ok else "FAIL"
                print(f"[{status}] {result.name}")
                if result.detail:
                    print(f"  {result.detail}")
            if not all(result.ok for result in results):
                raise typer.Exit(1)
        finally:
            await tester.close()

    asyncio.run(_run())


@app.command()
def soak(
    seconds: Annotated[int, typer.Option("--seconds", "-s")] = 60,
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Run the daemon for a fixed number of seconds."""

    asyncio.run(run_for_duration(_load_settings_with_logging(config), seconds))


@app.command("l2-dry-run-matrix")
def l2_dry_run_matrix(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Run all L2-A functions locally with dry_run=true and no Center calls."""

    summary = summarize_l2_matrix(run_l2_matrix_sync(load_settings(config)))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["ok"]:
        raise typer.Exit(2)


@app.command("l2b-matrix")
def l2b_matrix(
    config: Annotated[str | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Run the local L2-B function matrix without Center calls."""

    summary = summarize_l2b_matrix(run_l2b_matrix_sync(load_settings(config)))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["ok"]:
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
