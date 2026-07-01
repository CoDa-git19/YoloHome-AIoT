from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


# Root folder: YoloHome-AIoT/
ROOT_DIR = Path(__file__).resolve().parents[1]

# Load .env from project root
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH)


# =========================
# Core paths
# =========================
CONFIG_DIR = ROOT_DIR / "config"
DATABASE_DIR = ROOT_DIR / "database"
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"

DEVICE_REGISTRY_PATH = CONFIG_DIR / "device_registry.json"
COMMAND_SCHEMA_PATH = CONFIG_DIR / "command_schema.json"

DB_PATH = DATABASE_DIR / "yolohome.db"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"

PROMPT_TEMPLATE_PATH = (
    ROOT_DIR / "modules" / "llm_integration" / "prompt_template.txt"
)


# =========================
# API keys / environment
# =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

ADAFRUIT_IO_USERNAME = os.getenv("ADAFRUIT_IO_USERNAME", "")
ADAFRUIT_IO_KEY = os.getenv("ADAFRUIT_IO_KEY", "")

FLASK_ENV = os.getenv("FLASK_ENV", "development")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")


# =========================
# Runtime config
# =========================
USE_MOCK_LLM = os.getenv("USE_MOCK_LLM", "false").lower() == "true"
FACE_AUTH_THRESHOLD = float(os.getenv("FACE_AUTH_THRESHOLD", "0.80"))