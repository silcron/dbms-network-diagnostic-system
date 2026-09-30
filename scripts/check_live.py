"""Verify that the course TCP server reports a real MariaDB result."""

from __future__ import annotations

from app.client_core import DiagnosticClient


def main() -> None:
    client = DiagnosticClient()
    try:
        client.connect()
        response = client.diagnose()
        result = response["result"]
        mode, status = result["mode"], result["status"]
        print(f"Course diagnostic: {mode} / {status}")
        if mode != "LIVE" or status != "NORMAL":
            raise SystemExit("MariaDB LIVE diagnostic is not healthy.")
    finally:
        client.logout()


if __name__ == "__main__":
    main()
