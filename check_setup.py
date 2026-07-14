"""
Kiểm tra toàn bộ cấu hình và database. KHÔNG gọi Gemini, KHÔNG tốn quota.

Chạy:
    python check_setup.py

Qua hết thì mới chạy smoke_gemini.py (tốn quota).
"""

from __future__ import annotations

import sqlite3
import sys

from config.settings import (
    COMMAND_HISTORY_SIZE,
    DB_PATH,
    GEMINI_MODEL,
    GEMINI_STRUCTURED_OUTPUT,
    GEMINI_THINKING_LEVEL,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    RULE_HYSTERESIS,
    SESSION_MAX_TURNS,
    SESSION_TTL_SECONDS,
    USE_MOCK_LLM,
    check_config,
)
from database.init_db import init_db


problems: list[str] = []


def section(title: str) -> None:
    print()
    print("=" * 74)
    print(f"  {title}")
    print("=" * 74)


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK  " if ok else "LỖI "
    print(f"  {mark} {label}" + (f"  ({detail})" if detail else ""))

    if not ok:
        problems.append(label)


# =============================================================================
# 1. Config
# =============================================================================

section("1. CẤU HÌNH")

for warning in check_config():
    print(f"  [WARN] {warning}")

print()
print(f"  GEMINI_MODEL         : {GEMINI_MODEL}")
print(f"  GEMINI_THINKING_LEVEL: {GEMINI_THINKING_LEVEL or '(mặc định của model)'}")
print(f"  GEMINI_STRUCTURED    : {GEMINI_STRUCTURED_OUTPUT}")
print(f"  USE_MOCK_LLM         : {USE_MOCK_LLM}")
print(f"  LLM_TIMEOUT_SECONDS  : {LLM_TIMEOUT_SECONDS}")
print(f"  LLM_MAX_RETRIES      : {LLM_MAX_RETRIES}")
print(f"  SESSION_TTL_SECONDS  : {SESSION_TTL_SECONDS}")
print(f"  SESSION_MAX_TURNS    : {SESSION_MAX_TURNS}")
print(f"  RULE_HYSTERESIS      : {RULE_HYSTERESIS}")
print(f"  COMMAND_HISTORY_SIZE : {COMMAND_HISTORY_SIZE}")
print()

check(
    "Thinking đã tắt (tránh latency 16-22s)",
    GEMINI_THINKING_LEVEL == "minimal",
    GEMINI_THINKING_LEVEL or "chưa set",
)
check(
    "Không dùng alias '-latest' (số liệu benchmark phải tái lập được)",
    "latest" not in GEMINI_MODEL,
    GEMINI_MODEL,
)


# =============================================================================
# 2. Database
# =============================================================================

section("2. DATABASE")

init_db()

with sqlite3.connect(str(DB_PATH)) as conn:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}

    for table in ("command_log", "face_log", "automation_rules", "error_log"):
        check(f"bảng {table}", table in tables)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(automation_rules)")}

    # P1-6: rule edge-triggered cần 2 cột này.
    check("cột automation_rules.last_state (P1-6)", "last_state" in columns)
    check("cột automation_rules.last_triggered_at (P1-6)", "last_triggered_at" in columns)


# =============================================================================
# 3. Config files
# =============================================================================

section("3. FILE CẤU HÌNH")

from config.capabilities import load_device_capabilities
from modules.llm_integration.llm_module import (
    RULE_TRIGGERS,
    STATE_NAMES,
    load_device_registry,
    load_schema,
)
from system_core.commands import validate_special_commands

registry = load_device_registry()
schema = load_schema()

check("device_registry.json", bool(registry), f"{len(registry)} phòng")
check("command_schema.json", bool(schema.get("intents")))
check("language_aliases.json -> state_names (P1-4)", bool(STATE_NAMES))
check("language_aliases.json -> rule_triggers (P3)", bool(RULE_TRIGGERS))
check("device_capabilities.json", bool(load_device_capabilities()))

missing = validate_special_commands()
check("special_commands khớp với registry (P2-4)", not missing, str(missing))


# =============================================================================
# 4. Pipeline offline
# =============================================================================

section("4. PIPELINE (mock, không tốn quota)")

import contextlib
import io

from services.command_service import CommandService

service = CommandService(use_mock=True)

cases = [
    ("bật đèn phòng khách", "execute"),
    ("mở cửa chính", "auth_required"),
    ("nếu nhiệt độ trên 30 độ thì bật quạt phòng khách", "create_rule"),
    ("bật cái đó lên", "clarify"),
    ("bật máy lạnh phòng bếp", "registry_request"),
]

for transcript, expected in cases:
    with contextlib.redirect_stdout(io.StringIO()):
        result = service.handle_transcript(transcript)

    check(f"{transcript!r}", result["next_step"] == expected, result["next_step"])

# P1-4: query_status trả trạng thái thật
with contextlib.redirect_stdout(io.StringIO()):
    service.handle_transcript("bật quạt phòng ngủ")
    status = service.handle_transcript("quạt phòng ngủ đang thế nào")

check(
    "query_status trả trạng thái thật (P1-4)",
    "đang bật" in status["response"],
    status["response"],
)

# P1-3: multi-turn
with contextlib.redirect_stdout(io.StringIO()):
    service.handle_transcript("bật đèn", session_id="check")
    merged = service.handle_transcript("phòng khách", session_id="check")

check(
    "multi-turn slot filling (P1-3)",
    merged["next_step"] == "execute",
    merged["next_step"],
)


# =============================================================================
# Kết luận
# =============================================================================

section("KẾT LUẬN")

if problems:
    print(f"  {len(problems)} vấn đề:")
    for problem in problems:
        print(f"    - {problem}")
    print()
    print("  Sửa xong rồi mới chạy smoke_gemini.py.")
    sys.exit(1)

print("  Tất cả OK. Có thể chạy smoke_gemini.py (tốn khoảng 10 request).")
print("=" * 74)