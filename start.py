import logging
import os
import secrets
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from itertools import islice
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger = logging.getLogger("BotSupervisor")
    logger.warning("psutil not available, /srvc endpoint will be disabled")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("BotSupervisor")


def ttl_cache(seconds=1):
    """TTL cache decorator for methods. Caches result for specified seconds."""
    def decorator(func):
        cache = {"result": None, "timestamp": 0, "lock": threading.Lock()}

        @wraps(func)
        def wrapper(self):
            now = time.time()
            with cache["lock"]:
                if now - cache["timestamp"] > seconds:
                    cache["result"] = func(self)
                    cache["timestamp"] = now
                return cache["result"]

        return wrapper
    return decorator


def serialize_request_entry(entry):
    """Convert request entry with float timestamp to JSON-serializable format."""
    return {
        "timestamp": datetime.fromtimestamp(entry["timestamp"], timezone.utc).isoformat(),
        "method": entry["method"],
        "path": entry["path"],
        "status": entry["status"],
        "response_time_ms": entry["response_time_ms"],
        "ip": entry["ip"],
    }


class RequestLogger:
    """Tracks HTTP requests for analytics."""
    MAX_TRACKED_PATHS = 100
    MAX_TRACKED_STATUS_CODES = 20

    def __init__(self, max_size=1000):
        self._lock = threading.Lock()
        self.requests = deque(maxlen=max_size)
        self.stats = {
            "total_requests": 0,
            "status_codes": {},
            "paths": {},
            "methods": {},
        }

    def log_request(self, method, path, status_code, response_time_ms, ip):
        with self._lock:
            # Store timestamp as float for performance
            entry = {
                "timestamp": time.time(),
                "method": method,
                "path": path,
                "status": status_code,
                "response_time_ms": round(response_time_ms, 2),
                "ip": ip,
            }
            self.requests.append(entry)

            self.stats["total_requests"] += 1

            # Limit status_codes dictionary size
            status_key = str(status_code)
            if status_key not in self.stats["status_codes"] and len(self.stats["status_codes"]) >= self.MAX_TRACKED_STATUS_CODES:
                # Remove least frequent status code
                least_frequent = min(self.stats["status_codes"].items(), key=lambda x: x[1])[0]
                del self.stats["status_codes"][least_frequent]
            self.stats["status_codes"][status_key] = self.stats["status_codes"].get(status_key, 0) + 1

            # Limit paths dictionary size
            if path not in self.stats["paths"] and len(self.stats["paths"]) >= self.MAX_TRACKED_PATHS:
                # Remove least frequent path
                least_frequent = min(self.stats["paths"].items(), key=lambda x: x[1])[0]
                del self.stats["paths"][least_frequent]
            self.stats["paths"][path] = self.stats["paths"].get(path, 0) + 1

            # Methods are limited (GET, POST, HEAD, etc.) so no size limit needed
            self.stats["methods"][method] = self.stats["methods"].get(method, 0) + 1

    def get_recent_requests(self, limit=50):
        with self._lock:
            # Optimize: avoid full copy, use islice for better performance
            total = len(self.requests)
            if total <= limit:
                # If we have fewer items than limit, return all
                return [serialize_request_entry(e) for e in self.requests]
            else:
                # Use islice to get only last 'limit' items without full copy
                start_idx = total - limit
                return [serialize_request_entry(e) for e in islice(self.requests, start_idx, None)]

    def get_stats(self):
        with self._lock:
            # Return shallow copy to prevent external mutations
            return {
                "total_requests": self.stats["total_requests"],
                "status_codes": dict(self.stats["status_codes"]),
                "paths": dict(self.stats["paths"]),
                "methods": dict(self.stats["methods"]),
            }


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

        # Uptime tracking
        self.uptime_checks = deque(maxlen=100)
        self.last_health_check: datetime | None = None

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
                # Optimized: use Event.wait() instead of sleep() + check
                while worker_thread.is_alive():
                    if self._stop_event.wait(timeout=1.0) or self._restart_event.wait(timeout=0):
                        break
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

            # Optimized: use Event.wait() instead of active polling
            while True:
                if self._stop_event.wait(timeout=1.0) or self._restart_event.wait(timeout=0):
                    break

    def record_health_check(self, is_healthy: bool):
        with self._lock:
            now = time.time()
            self.last_health_check = datetime.fromtimestamp(now, timezone.utc)
            self.uptime_checks.append({
                "timestamp": now,  # Store as float for performance
                "healthy": is_healthy,
            })

    def get_uptime_percentage(self, minutes=60):
        # NOTE: This method is called from within get_telemetry() which already holds the lock
        # So we DON'T acquire lock here to avoid deadlock
        if not self.uptime_checks:
            return 100.0

        cutoff = time.time() - (minutes * 60)
        # Optimized: no datetime parsing, direct float comparison
        recent_checks = [c for c in self.uptime_checks if c["timestamp"] > cutoff]

        if not recent_checks:
            return 100.0

        healthy_count = sum(1 for c in recent_checks if c["healthy"])
        return round((healthy_count / len(recent_checks)) * 100, 2)

    @ttl_cache(seconds=1)
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
                    "last_health_check": self.last_health_check.isoformat() if self.last_health_check else None,
                    "uptime_1h": self.get_uptime_percentage(60),
                    "uptime_24h": self.get_uptime_percentage(1440),
                },
            }


supervisor = BotSupervisor()
request_logger = RequestLogger()

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

# Admin credentials from environment
ADMIN_LOGIN = os.getenv("ADM_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("ADM_PASS", "admin")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def log_request_start():
    request.start_time = time.time()


@app.after_request
def log_request_end(response):
    if hasattr(request, "start_time"):
        response_time_ms = (time.time() - request.start_time) * 1000
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        # Skip logging static assets
        if not request.path.startswith(("/static", "/favicon")):
            request_logger.log_request(
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                response_time_ms=response_time_ms,
                ip=ip,
            )

    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == ADMIN_LOGIN and password == ADMIN_PASSWORD:
            session["authenticated"] = True
            session["login_time"] = datetime.now(timezone.utc).isoformat()
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Неверный логин или пароль")

    if session.get("authenticated"):
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/analytics")
@login_required
def api_analytics():
    stats = request_logger.get_stats()
    recent_requests = request_logger.get_recent_requests(100)
    telemetry = supervisor.get_telemetry()

    return jsonify({
        "telemetry": telemetry,
        "request_stats": stats,
        "recent_requests": recent_requests,
    })


@app.route("/health", methods=["GET", "HEAD"])
def health():
    telemetry = supervisor.get_telemetry()
    is_healthy = telemetry["bot"]["is_running"] and telemetry["bot"]["status"] == "healthy"
    status_code = 200 if is_healthy else 503

    # Record health check
    supervisor.record_health_check(is_healthy)

    if request.method == "HEAD":
        return "", status_code

    response_data = {
        "status": "healthy" if is_healthy else "unhealthy",
        "bot_status": telemetry["bot"]["status"],
        "bot_running": telemetry["bot"]["is_running"],
        "restarts": telemetry["bot"]["restart_count"],
        "server_status": "online",
        "last_error": telemetry["bot"]["last_error"],
        "uptime_1h": telemetry["bot"]["uptime_1h"],
        "uptime_24h": telemetry["bot"]["uptime_24h"],
    }
    return jsonify(response_data), status_code


@app.get("/api/status")
def api_status():
    return jsonify(supervisor.get_telemetry())


@app.route("/api/restart", methods=["GET", "POST"])
@login_required
def api_restart():
    supervisor.trigger_restart()
    return jsonify({
        "ok": True,
        "message": "Restart command sent to supervisor",
    })


@app.get("/srvc")
def system_resources():
    if not PSUTIL_AVAILABLE:
        return jsonify({"error": "psutil module not available"}), 503

    # Cache system resource data for 5 seconds to avoid blocking I/O on every request
    return _get_system_resources_cached()


@ttl_cache(seconds=5)
def _get_system_resources_cached():
    """Cached system resources to avoid expensive I/O operations."""
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


supervisor.start()
