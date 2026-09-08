"""Executable entry point for the durable analysis worker."""

from __future__ import annotations

import logging

from tradingagents.platform.worker_runtime import create_worker_runtime


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    runtime = create_worker_runtime()
    try:
        runtime.runner.run_forever(poll_seconds=runtime.poll_seconds)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
