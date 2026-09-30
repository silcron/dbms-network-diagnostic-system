"""Console client for a quick end-to-end demo without opening Tkinter."""

from __future__ import annotations

import argparse
import json

from .client_core import DiagnosticClient


def main() -> None:
    parser = argparse.ArgumentParser(description="DBMS diagnostic demo client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9090, type=int)
    args = parser.parse_args()
    client = DiagnosticClient(args.host, args.port)
    try:
        hello = client.connect()
        print(f"Connected: {hello['message']}")
        print(json.dumps(client.diagnose(), ensure_ascii=False, indent=2))
    finally:
        client.logout()


if __name__ == "__main__":
    main()
