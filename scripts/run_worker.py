#!/usr/bin/env python
"""Run the DocuParse background worker.

Processes queued extraction jobs and delivers webhooks. Run at least one
alongside the API:

    python scripts/run_worker.py

Several can run at once — jobs are claimed with FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.workers.worker import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
