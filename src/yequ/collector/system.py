"""Local machine system metrics collection using psutil."""

from __future__ import annotations

import platform
import socket

import psutil


def collect_system_metrics() -> dict:
    """Collect current system metrics as a flat dict.

    Returns keys suitable for both snapshot (overall state) and metric ingestion.
    """
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    net_io = psutil.net_io_counters()
    boot = psutil.boot_time()

    return {
        # CPU
        "cpu_percent": cpu,
        "cpu_count": psutil.cpu_count(),
        # Memory
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / (1024**3), 2),
        "memory_total_gb": round(mem.total / (1024**3), 2),
        # Disk
        "disk_usage_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        # Network (cumulative, good for metric tracking)
        "net_bytes_sent": net_io.bytes_sent,
        "net_bytes_recv": net_io.bytes_recv,
        # System
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "boot_time": boot,
        # Load
        "load_avg_1m": psutil.getloadavg()[0] if hasattr(psutil, "getloadavg") else 0.0,
    }
