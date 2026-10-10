"""Paths and settings. Override with environment variables if needed."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "hospital.db"))
