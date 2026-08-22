#!/usr/bin/env python3
import sys
import time
import requests
from datetime import datetime


def clear_screen():
    print("\033[2J\033[H", end="")


def get_bar(percent, width=40):
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return bar


def get_color(percent):
    if percent < 50:
        return "\033[92m"  # Green
    elif percent < 80:
        return "\033[93m"  # Yellow
    else:
        return "\033[91m"  # Red


def format_uptime(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m"
    elif seconds < 86400:
        return f"{int(seconds / 3600)}h {int((seconds % 3600) / 60)}m"
    else:
        days = int(seconds / 86400)
        hours = int((seconds % 86400) / 3600)
        return f"{days}d {hours}h"


def render_dashboard(service_url):
    try:
        resp = requests.get(f"{service_url}/srvc", timeout=5)
        status_resp = requests.get(f"{service_url}/api/status", timeout=5)

        if resp.status_code != 200:
            print(f"\033[91mError: Service returned {resp.status_code}\033[0m")
            return

        data = resp.json()
        status = status_resp.json()

        clear_screen()

        # Header
        print("\033[1m╔══════════════════════════════════════════════════════════════╗\033[0m")
        print(f"\033[1m║  Bot Service Monitor\033[0m                   {datetime.now().strftime('%H:%M:%S')}  \033[1m║\033[0m")
        print("\033[1m╚══════════════════════════════════════════════════════════════╝\033[0m")
        print()

        # Server Status
        server_uptime = status["server"]["uptime_seconds"]
        bot_status = status["bot"]["status"]
        bot_running = status["bot"]["is_running"]

        status_color = "\033[92m" if bot_running and bot_status == "healthy" else "\033[91m"
        status_text = "HEALTHY" if bot_running and bot_status == "healthy" else bot_status.upper()

        print(f"  \033[1mServer Status:\033[0m {status_color}{status_text}\033[0m")
        print(f"  \033[1mServer Uptime:\033[0m {format_uptime(server_uptime)}")
        print(f"  \033[1mBot Restarts:\033[0m  {status['bot']['restart_count']}")
        print()

        # CPU
        cpu = data["cpu"]
        cpu_percent = cpu["percent"]
        cpu_color = get_color(cpu_percent)
        cpu_bar = get_bar(cpu_percent)

        print(f"  \033[1m╭─ CPU ({cpu['count']} cores @ {cpu['freq_mhz']}MHz)\033[0m")
        print(f"  │ {cpu_color}{cpu_bar}\033[0m {cpu_color}{cpu_percent:5.1f}%\033[0m")
        print()

        # Memory
        mem = data["memory"]
        mem_percent = mem["percent"]
        mem_color = get_color(mem_percent)
        mem_bar = get_bar(mem_percent)

        print(f"  \033[1m╭─ Memory\033[0m")
        print(f"  │ {mem_color}{mem_bar}\033[0m {mem_color}{mem_percent:5.1f}%\033[0m")
        print(f"  │ {mem['used_gb']:.2f} GB / {mem['total_gb']:.2f} GB (Available: {mem['available_gb']:.2f} GB)")
        print()

        # Swap
        swap = data["swap"]
        if swap["total_gb"] > 0:
            swap_percent = swap["percent"]
            swap_color = get_color(swap_percent)
            swap_bar = get_bar(swap_percent)

            print(f"  \033[1m╭─ Swap\033[0m")
            print(f"  │ {swap_color}{swap_bar}\033[0m {swap_color}{swap_percent:5.1f}%\033[0m")
            print(f"  │ {swap['used_gb']:.2f} GB / {swap['total_gb']:.2f} GB")
            print()

        # Disk
        disk = data["disk"]
        disk_percent = disk["percent"]
        disk_color = get_color(disk_percent)
        disk_bar = get_bar(disk_percent)

        print(f"  \033[1m╭─ Disk (/)\033[0m")
        print(f"  │ {disk_color}{disk_bar}\033[0m {disk_color}{disk_percent:5.1f}%\033[0m")
        print(f"  │ {disk['used_gb']:.2f} GB / {disk['total_gb']:.2f} GB (Free: {disk['free_gb']:.2f} GB)")
        print()

        # Footer
        print("\033[2m  Press Ctrl+C to exit\033[0m")

    except requests.exceptions.Timeout:
        print("\033[91mError: Request timeout\033[0m")
    except requests.exceptions.ConnectionError:
        print("\033[91mError: Cannot connect to service\033[0m")
    except Exception as e:
        print(f"\033[91mError: {e}\033[0m")


def main():
    if len(sys.argv) < 2:
        print("Usage: python monitor.py <service_url>")
        print("Example: python monitor.py https://your-app.onrender.com")
        sys.exit(1)

    service_url = sys.argv[1].rstrip("/")
    refresh_interval = 2  # seconds

    print("\033[?25l", end="")  # Hide cursor

    try:
        while True:
            render_dashboard(service_url)
            time.sleep(refresh_interval)
    except KeyboardInterrupt:
        print("\033[?25h")  # Show cursor
        clear_screen()
        print("\nMonitoring stopped.")
    except Exception as e:
        print("\033[?25h")  # Show cursor
        print(f"\n\033[91mFatal error: {e}\033[0m")
        sys.exit(1)


if __name__ == "__main__":
    main()
