"""Vercel Serverless entrypoint for ColdChain Pulse FastAPI."""

import sys
from pathlib import Path

# Add project root and backend directory to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db import run_migrations  # noqa: E402
from app.telemetry import seed_initial_data  # noqa: E402
from app.main import app  # noqa: E402

# Ensure DB tables and initial seeds are present on serverless spin-up
try:
    run_migrations()
    seed_initial_data()
except Exception as exc:
    print(f"[Vercel Init Warning] {exc}")
