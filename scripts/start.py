#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
INFRA_SERVICES = ["zookeeper", "kafka", "clickhouse"]


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(load_env_file(ROOT / ".env"))
    env.setdefault("PYTHONPATH", str(BACKEND))
    env.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    env.setdefault("CLICKHOUSE_HOST", "localhost")
    env.setdefault("CLICKHOUSE_PORT", "8123")
    if not env.get("CLICKHOUSE_PASSWORD"):
        env["CLICKHOUSE_PASSWORD"] = "twitch_analyze"
    return env


def python_executable() -> str:
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def ensure_env_file() -> None:
    if not (ROOT / ".env").exists():
        print("Missing .env. Create it with: cp .env.example .env", file=sys.stderr)
        raise SystemExit(1)


def ensure_frontend_dependencies() -> None:
    if (FRONTEND / "node_modules").exists():
        return
    print("frontend/node_modules is missing; running npm install...")
    subprocess.run(["npm", "install"], cwd=FRONTEND, check=True)


def require_docker() -> None:
    if shutil.which("docker") is None:
        print(
            "Docker is required to start Kafka and ClickHouse. Install/start Docker, "
            "or run Kafka and ClickHouse yourself before starting backend/worker.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def wait_for_port(host: str, port: int, label: str, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"{label} is reachable at {host}:{port}")
                return
        except OSError:
            time.sleep(2)

    print(
        f"{label} did not become reachable at {host}:{port} within {timeout_seconds}s.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def start_infra() -> None:
    require_docker()
    print("Starting Kafka and ClickHouse infrastructure...")
    subprocess.run(["docker", "compose", "up", "-d", *INFRA_SERVICES], cwd=ROOT, check=True)

    env = base_env()
    clickhouse_host = env.get("CLICKHOUSE_HOST", "localhost")
    if clickhouse_host == "clickhouse":
        clickhouse_host = "localhost"
    wait_for_port("localhost", 9092, "Kafka")
    print("Giving Kafka a few seconds to finish broker metadata startup...")
    time.sleep(8)
    wait_for_port(clickhouse_host, int(env.get("CLICKHOUSE_PORT", "8123")), "ClickHouse")


def ensure_infra_reachable() -> None:
    env = base_env()
    clickhouse_host = env.get("CLICKHOUSE_HOST", "localhost")
    if clickhouse_host == "clickhouse":
        clickhouse_host = "localhost"
    try:
        with socket.create_connection((clickhouse_host, int(env.get("CLICKHOUSE_PORT", "8123"))), timeout=2):
            return
    except OSError:
        print(
            "ClickHouse is not reachable. Start infrastructure first with:\n"
            "  .venv/bin/python scripts/start.py infra\n"
            "or use:\n"
            "  .venv/bin/python scripts/start.py all",
            file=sys.stderr,
        )
        raise SystemExit(1)


def command_for(service: str, install_frontend: bool) -> tuple[list[str], Path, dict[str, str]]:
    env = base_env()

    if service == "backend":
        ensure_env_file()
        ensure_infra_reachable()
        return (
            [
                python_executable(),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--reload",
            ],
            BACKEND,
            env,
        )

    if service == "worker":
        ensure_env_file()
        ensure_infra_reachable()
        return ([python_executable(), "-m", "app.workers.clickhouse_consumer"], BACKEND, env)

    if service == "frontend":
        if install_frontend:
            ensure_frontend_dependencies()
        env.setdefault("VITE_BACKEND_PROXY_TARGET", "http://localhost:8000")
        env.setdefault("VITE_BACKEND_WS_PROXY_TARGET", "ws://localhost:8000")
        return (["npm", "run", "dev", "--", "--host", "0.0.0.0"], FRONTEND, env)

    raise ValueError(f"Unknown service: {service}")


def run_one(service: str, install_frontend: bool) -> int:
    command, cwd, env = command_for(service, install_frontend)
    print(f"Starting {service}: {' '.join(command)}")
    return subprocess.call(command, cwd=cwd, env=env)


def run_all(install_frontend: bool) -> int:
    start_infra()
    services = ["backend", "worker", "frontend"]
    processes: list[subprocess.Popen] = []

    try:
        for service in services:
            command, cwd, env = command_for(service, install_frontend)
            print(f"Starting {service}: {' '.join(command)}")
            processes.append(subprocess.Popen(command, cwd=cwd, env=env))
            time.sleep(1)

        while True:
            for process in processes:
                if process.poll() is not None:
                    return process.returncode or 0
            time.sleep(1)
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start Twitch Analyze services.")
    parser.add_argument(
        "service",
        choices=["infra", "backend", "worker", "frontend", "all"],
        help="Service to start. Use 'all' for infra, backend API, ClickHouse worker, and frontend.",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Do not run npm install automatically when frontend/node_modules is missing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    install_frontend = not args.no_install
    if args.service == "infra":
        start_infra()
        return 0
    if args.service == "all":
        return run_all(install_frontend=install_frontend)
    return run_one(args.service, install_frontend=install_frontend)


if __name__ == "__main__":
    raise SystemExit(main())
