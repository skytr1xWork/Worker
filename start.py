import logging
import os
import threading
import time
import traceback
from datetime import datetime, timezone

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


@app.get("/favicon.ico")
def favicon():
    return "", 204

supervisor.start()
