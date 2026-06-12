"""YeQu Gateway CLI entry point."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys

import click

from yequ.config import load_config, Config
from yequ.storage.database import init_database
from yequ.storage.query import (
    get_latest_snapshot,
    query_snapshots_by_device,
    get_metrics,
    get_events,
)
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.collector.runner import CollectorRunner
from yequ.monitor.engine import MonitorEngine
from yequ.monitor.rules import load_rules_from_yaml


DEFAULT_CONFIG_PATH = "config/gateway.yaml"
DEFAULT_RULES_PATH = "config/monitor_rules.yaml"

logger = logging.getLogger("yequ")


def _setup_components(config: Config):
    """Initialize all Gateway components from config."""
    data_dir = config.data.dir
    os.makedirs(data_dir, exist_ok=True)

    db_path = os.path.join(data_dir, "gateway.db")
    init_database(db_path)

    store = DeviceStore(db_path)
    notify = NotifyRouter(log_path=config.notify.log_file)
    runner = CollectorRunner(db_path=db_path, device_store=store)

    rules = []
    if os.path.exists(DEFAULT_RULES_PATH):
        rules = load_rules_from_yaml(DEFAULT_RULES_PATH)

    monitor = MonitorEngine(
        db_path=db_path, device_store=store,
        notify_router=notify, rules=rules,
        data_dir=data_dir, rules_path=DEFAULT_RULES_PATH,
    )
    return db_path, store, notify, runner, monitor


@click.group()
@click.option("--config", "-c", "config_path", default=DEFAULT_CONFIG_PATH,
              help="Path to gateway.yaml")
@click.option("--debug/--no-debug", default=False)
@click.pass_context
def main(ctx, config_path, debug):
    """YeQu Gateway — Personal Data Hub"""
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@main.command()
@click.pass_context
def serve(ctx):
    """Start the Gateway server (HTTP + Monitor + Collector + API)."""
    config = load_config(ctx.obj["config_path"])
    db_path, store, notify, runner, monitor = _setup_components(config)

    from yequ.events_bus import bus as event_bus
    monitor._event_bus = event_bus

    local_device = runner.ensure_local_device()
    click.echo(f"[ok] Local device: {local_device.device_id}")

    from yequ.transport.http_server import create_app
    import uvicorn

    app = create_app(
        db_path=db_path, device_store=store, notify_router=notify,
        collector_runner=runner, agent_config=config.agent,
    )

    import threading
    import time

    stop_collector = threading.Event()

    def collector_loop():
        while not stop_collector.is_set():
            try:
                runner.collect_and_ingest()
            except Exception as e:
                logger.error("Collection error: %s", e)
            stop_collector.wait(config.collector.interval_seconds)

    threading.Thread(target=collector_loop, daemon=True).start()
    click.echo(f"Collector: every {config.collector.interval_seconds}s")

    stop_monitor = threading.Event()

    def monitor_loop():
        time.sleep(5)
        while not stop_monitor.is_set():
            try:
                monitor.scan()
            except Exception as e:
                logger.error("Monitor error: %s", e)
            stop_monitor.wait(config.monitor.scan_interval_seconds)

    threading.Thread(target=monitor_loop, daemon=True).start()
    click.echo(f"Monitor: every {config.monitor.scan_interval_seconds}s")

    # Archive: run once on startup, then every 24 hours
    stop_archive = threading.Event()

    def archive_loop():
        from yequ.storage.archive import run_archive_cycle
        # First run after 5 minutes (let data accumulate)
        stop_archive.wait(300)
        while not stop_archive.is_set():
            try:
                result = run_archive_cycle(db_path)
                if result["metrics_pruned"] or result["snapshots_pruned"]:
                    logger.info("Archive cycle: %s", result)
            except Exception as e:
                logger.error("Archive error: %s", e)
            stop_archive.wait(86400)  # 24 hours

    threading.Thread(target=archive_loop, daemon=True).start()
    click.echo("Archive: daily retention cleanup")

    click.echo(f"\nGateway listening on {config.gateway.host}:{config.gateway.port}")
    click.echo("Press Ctrl+C to stop\n")

    def shutdown(sig, frame):
        click.echo("\nShutting down...")
        stop_collector.set()
        stop_monitor.set()
        stop_archive.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    uvicorn.run(app, host=config.gateway.host, port=config.gateway.port,
                log_level="warning")


@main.command()
@click.pass_context
def devices(ctx):
    """List all registered devices."""
    config = load_config(ctx.obj["config_path"])
    db_path, store, _, _, _ = _setup_components(config)

    devices = store.list_devices()
    if not devices:
        click.echo("No devices registered.")
        return

    for d in devices:
        status_text = f"[{d.display_status}]"
        labels = ", ".join(f"{k}={v}" for k, v in d.labels.items())
        local_tag = " [local]" if d.is_local else ""
        click.echo(f"{status_text} {d.device_id}{local_tag}")
        if labels:
            click.echo(f"   Labels: {labels}")
        if d.last_hello_at:
            click.echo(f"   Last hello: {d.last_hello_at}")
        elif d.is_local:
            click.echo(f"   (local device, direct DB access)")
        click.echo()


@main.command()
@click.argument("device_id", required=False)
@click.pass_context
def status(ctx, device_id):
    """Show device status and latest metrics."""
    config = load_config(ctx.obj["config_path"])
    db_path, store, _, _, _ = _setup_components(config)

    if device_id:
        devices = [store.get_device(device_id)]
        if devices[0] is None:
            click.echo(f"Device not found: {device_id}")
            return
    else:
        devices = store.list_devices()

    for d in devices:
        click.echo(f"=== {d.device_id} ===")
        snaps = query_snapshots_by_device(db_path, d.device_id)
        for s in snaps:
            payload = json.loads(s["payload_json"])
            click.echo(f"  [{s['capability']}]")
            for k, v in payload.items():
                click.echo(f"    {k}: {v}")
            click.echo()


@main.command()
@click.argument("device_id", required=False)
@click.pass_context
def events(ctx, device_id):
    """Show recent events."""
    config = load_config(ctx.obj["config_path"])
    db_path, _, _, _, _ = _setup_components(config)

    events = get_events(db_path, device_id=device_id, limit=20)
    if not events:
        click.echo("No events.")
        return

    severity_labels = {"critical": "CRIT", "warning": "WARN", "info": "INFO"}
    for e in events:
        label = severity_labels.get(e["severity"], e["severity"].upper())
        click.echo(f"[{label}] [{e['timestamp']}] {e['title']}")
        if e["body"]:
            click.echo(f"   {e['body']}")
        click.echo()


@main.command()
@click.argument("query")
@click.pass_context
def ask(ctx, query):
    """Ask the Gateway anything in natural language, powered by LLM.

    Requires a configured LLM provider in config/gateway.yaml.
    Without an API key, falls back to offline keyword matching.
    """
    config = load_config(ctx.obj["config_path"])
    db_path, store, _, _, _ = _setup_components(config)

    api_key = config.agent.api_key
    if not api_key:
        import os as _os
        env_map = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
        api_key = _os.environ.get(env_map.get(config.agent.provider, ""))

    if api_key:
        from yequ.agent.core import create_agent
        agent = create_agent(config=config.agent, db_path=db_path,
                             data_dir=config.data.dir)
        click.echo("Thinking...\n")
        answer = agent.ask(query)
        click.echo(answer)
    else:
        click.echo("[no-api-key] LLM not configured, using offline mode.\n")
        click.echo("To enable AI: set agent.api_key in config/gateway.yaml\n")

        query_lower = query.lower()
        if any(w in query_lower for w in ["alert", "alarm", "event", "告警", "事件", "报警", "异常"]):
            ctx.invoke(events)
        elif any(w in query_lower for w in ["device", "设备", "devices", "列表", "有哪些", "几个"]):
            ctx.invoke(devices)
        elif any(w in query_lower for w in ["monitor", "巡检", "监控"]):
            ctx.invoke(monitor, action="status")
        else:
            ctx.invoke(status)


@main.command()
@click.argument("action", type=click.Choice(["on", "off", "status"]))
@click.pass_context
def monitor(ctx, action):
    """Control the inspector: on/off/status."""
    config = load_config(ctx.obj["config_path"])
    marker_path = os.path.join(config.data.dir, "monitor_enabled")

    if action == "on":
        with open(marker_path, "w") as f:
            f.write("1")
        click.echo("Monitor: ON (all rules active)")
    elif action == "off":
        with open(marker_path, "w") as f:
            f.write("0")
        click.echo("Monitor: OFF (keep-alive rules only)")
    elif action == "status":
        if os.path.exists(marker_path):
            state = open(marker_path).read().strip()
            label = "ON" if state == "1" else "OFF (keep-alive only)"
        else:
            label = "ON (default)"
        click.echo(f"Monitor: {label}")


if __name__ == "__main__":
    main()
