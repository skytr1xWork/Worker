import logging
import os
import threading
import time
import traceback
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("BotSupervisor")


class BotSupervisor:
    def __init__(self):
        self._lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._bot_worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._restart_event = threading.Event()

        self.is_running = False
        self.status = "starting"
        self.server_started_at = datetime.now(timezone.utc)
        self.started_at: datetime | None = None
        self.last_restart_at: datetime | None = None
        self.restart_count = 0
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None
        self.last_error_traceback: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._monitor_thread and self._monitor_thread.is_alive():
                return
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._supervisor_loop,
                name="BotSupervisorMonitor",
                daemon=True,
            )
            self._monitor_thread.start()
            logger.info("Supervisor monitor thread started.")

    def trigger_restart(self) -> None:
        self._restart_event.set()
        logger.info("Manual bot restart requested.")

    def _supervisor_loop(self) -> None:
        from main import main as run_bot

        while not self._stop_event.is_set():
            self._restart_event.clear()

            with self._lock:
                self.status = "starting"
                self.started_at = datetime.now(timezone.utc)
                self.last_restart_at = datetime.now(timezone.utc)
                self.is_running = False

            logger.info("Starting bot worker thread...")

            bot_error_container = [None]
            bot_tb_container = [None]

            def worker():
                try:
                    run_bot()
                except Exception as exc:
                    bot_error_container[0] = exc
                    bot_tb_container[0] = traceback.format_exc()
                    logger.error(f"Bot worker terminated with error: {exc}")

            worker_thread = threading.Thread(
                target=worker,
                name=f"BotWorker-{self.restart_count}",
                daemon=True,
            )
            self._bot_worker = worker_thread
            worker_thread.start()
            time.sleep(1.5)

            if worker_thread.is_alive() and bot_error_container[0] is None:
                with self._lock:
                    self.is_running = True
                    self.status = "healthy"
                logger.info("Bot is healthy and polling updates.")
                while worker_thread.is_alive() and not self._stop_event.is_set() and not self._restart_event.is_set():
                    time.sleep(1.0)
            else:
                logger.warning("Bot failed during startup phase.")

            with self._lock:
                self.is_running = False

            if bot_error_container[0] is not None:
                with self._lock:
                    self.status = "error"
                    self.last_error = str(bot_error_container[0])
                    self.last_error_time = datetime.now(timezone.utc)
                    self.last_error_traceback = bot_tb_container[0]

                logger.error(
                    f"Bot stopped with error: {self.last_error}. "
                    f"Auto-restart disabled: server continues running normally."
                )
            elif self._restart_event.is_set():
                with self._lock:
                    self.status = "restarting"
                    self.restart_count += 1
                logger.info("Restart requested, proceeding to next iteration...")
                continue
            elif self._stop_event.is_set():
                break
            else:
                with self._lock:
                    self.status = "stopped"
                    self.last_error = "Bot process stopped unexpectedly."
                    self.last_error_time = datetime.now(timezone.utc)
                    self.last_error_traceback = "Bot worker thread terminated."

                logger.warning("Bot worker finished. Server continues running normally.")

            while not self._stop_event.is_set() and not self._restart_event.is_set():
                time.sleep(1.0)

    def get_telemetry(self) -> dict:
        with self._lock:
            now = datetime.now(timezone.utc)
            server_uptime = (now - self.server_started_at).total_seconds()
            bot_uptime = (now - self.started_at).total_seconds() if self.is_running and self.started_at else 0

            return {
                "server": {
                    "status": "online",
                    "uptime_seconds": round(server_uptime, 1),
                    "started_at": self.server_started_at.isoformat(),
                },
                "bot": {
                    "is_running": self.is_running,
                    "status": self.status,
                    "uptime_seconds": round(bot_uptime, 1) if self.is_running else 0,
                    "started_at": self.started_at.isoformat() if self.started_at else None,
                    "restart_count": self.restart_count,
                    "last_restart_at": self.last_restart_at.isoformat() if self.last_restart_at else None,
                    "last_error": self.last_error,
                    "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None,
                    "last_error_traceback": self.last_error_traceback,
                },
            }


supervisor = BotSupervisor()

app = Flask(__name__, template_folder="templates")


@app.route("/", methods=["GET", "HEAD"])
def index():
    if request.method == "HEAD":
        telemetry = supervisor.get_telemetry()
        is_healthy = telemetry["bot"]["is_running"] and telemetry["bot"]["status"] == "healthy"
        return "", (200 if is_healthy else 503)

    if request.args.get("format") == "json" or request.accept_mimetypes.best == "application/json":
        telemetry = supervisor.get_telemetry()
        is_healthy = telemetry["bot"]["is_running"] and telemetry["bot"]["status"] == "healthy"
        return jsonify(telemetry), (200 if is_healthy else 503)

    return render_template("index.html")


@app.route("/health", methods=["GET", "HEAD"])
def health():
    telemetry = supervisor.get_telemetry()
    is_healthy = telemetry["bot"]["is_running"] and telemetry["bot"]["status"] == "healthy"
    status_code = 200 if is_healthy else 503

    if request.method == "HEAD":
        return "", status_code

    response_data = {
        "status": "healthy" if is_healthy else "unhealthy",
        "bot_status": telemetry["bot"]["status"],
        "bot_running": telemetry["bot"]["is_running"],
        "restarts": telemetry["bot"]["restart_count"],
        "server_status": "online",
        "last_error": telemetry["bot"]["last_error"],
    }
    return jsonify(response_data), status_code


@app.get("/api/status")
def api_status():
    return jsonify(supervisor.get_telemetry())


@app.route("/api/restart", methods=["GET", "POST"])
def api_restart():
    supervisor.trigger_restart()
    return jsonify({
        "ok": True,
        "message": "Restart command sent to supervisor",
    })


@app.get("/srvc")
def system_resources():
    import psutil
    import os
    from pathlib import Path

    # Try to read cgroup v2 memory limit (Docker/Kubernetes)
    cgroup_mem_limit = None
    cgroup_mem_usage = None
    try:
        mem_max_path = Path("/sys/fs/cgroup/memory.max")
        mem_current_path = Path("/sys/fs/cgroup/memory.current")

        if mem_max_path.exists():
            mem_max = mem_max_path.read_text().strip()
            if mem_max != "max":
                cgroup_mem_limit = int(mem_max)

        if mem_current_path.exists():
            cgroup_mem_usage = int(mem_current_path.read_text().strip())
    except Exception:
        pass

    # Fallback to cgroup v1
    if cgroup_mem_limit is None:
        try:
            mem_limit_path = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
            mem_usage_path = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")

            if mem_limit_path.exists():
                limit = int(mem_limit_path.read_text().strip())
                # Check if limit is set (not the host max)
                if limit < (1024**4):  # Less than 1 TB = likely a real limit
                    cgroup_mem_limit = limit

            if mem_usage_path.exists():
                cgroup_mem_usage = int(mem_usage_path.read_text().strip())
        except Exception:
            pass

    # CPU stats (interval=None for instant, non-blocking read)
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_count = psutil.cpu_count(logical=True)
    cpu_freq = psutil.cpu_freq()

    # Memory: use cgroup limits if available, otherwise fallback to psutil
    if cgroup_mem_limit and cgroup_mem_usage:
        mem_total = cgroup_mem_limit
        mem_used = cgroup_mem_usage
        mem_available = mem_total - mem_used
        mem_percent = (mem_used / mem_total) * 100 if mem_total > 0 else 0
    else:
        mem = psutil.virtual_memory()
        mem_total = mem.total
        mem_used = mem.used
        mem_available = mem.available
        mem_percent = mem.percent

    swap = psutil.swap_memory()
    disk = psutil.disk_usage('/')

    return jsonify({
        "cpu": {
            "percent": round(cpu_percent, 1),
            "count": cpu_count,
            "freq_mhz": round(cpu_freq.current, 1) if cpu_freq else None,
        },
        "memory": {
            "total_gb": round(mem_total / (1024**3), 2),
            "used_gb": round(mem_used / (1024**3), 2),
            "available_gb": round(mem_available / (1024**3), 2),
            "percent": round(mem_percent, 1),
            "is_cgroup_limited": cgroup_mem_limit is not None,
        },
        "swap": {
            "total_gb": round(swap.total / (1024**3), 2),
            "used_gb": round(swap.used / (1024**3), 2),
            "percent": round(swap.percent, 1),
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": round(disk.percent, 1),
        },
    })


@app.get("/favicon.ico")
def favicon():
    return "", 204


def self_ping_loop():
    """Периодически пингует /health, чтобы сервер не засыпал."""
    logger.info("Self-ping loop started.")
    base_url = os.getenv("SELF_PING_URL", "http://localhost:8000")
    interval = int(os.getenv("SELF_PING_INTERVAL", "300"))  # 5 минут по умолчанию

    while True:
        time.sleep(interval)
        try:
            url = f"{base_url}/health"
            response = requests.get(url, timeout=10)
            logger.debug(f"Self-ping /health: {response.status_code}")
        except Exception as e:
            logger.warning(f"Self-ping failed: {e}")


# Запуск self-ping в отдельном потоке
self_ping_thread = threading.Thread(target=self_ping_loop, name="SelfPing", daemon=True)
self_ping_thread.start()

supervisor.start()
