import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def wait_for(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited before becoming ready: {process.args}")
        try:
            if httpx.get(url, timeout=0.5).is_success:
                return
        except httpx.RequestError:
            time.sleep(0.2)
    raise RuntimeError(f"Service did not become ready: {url}")


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


@pytest.mark.integration
def test_real_order_inventory_payment_flow() -> None:
    inventory_port, payment_port, order_port = free_port(), free_port(), free_port()
    env = os.environ.copy()
    env.update(
        INVENTORY_SERVICE_URL=f"http://127.0.0.1:{inventory_port}",
        PAYMENT_SERVICE_URL=f"http://127.0.0.1:{payment_port}",
    )
    env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [str(ROOT / "services" / "shared"), env.get("PYTHONPATH")],
        )
    )
    specs = [
        ("inventory-service", inventory_port, env),
        ("payment-service", payment_port, env),
        ("order-service", order_port, env),
    ]
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for service, port, service_env in specs:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT / "services" / service,
                env=service_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            processes.append(process)
            wait_for(f"http://127.0.0.1:{port}/health", process)

        response = httpx.post(
            f"http://127.0.0.1:{order_port}/orders",
            json={"product_id": "product-1", "quantity": 1},
            timeout=5,
        )
        assert response.status_code == 201
        assert response.json()["status"] == "created"
        assert response.json()["payment_id"]
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
