from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# =============================================================================
# Paths
# =============================================================================

# Root folder: YoloHome-AIoT/
ROOT_DIR = Path(__file__).resolve().parents[1]

# Load .env from project root
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH)

CONFIG_DIR = ROOT_DIR / "config"
DATABASE_DIR = ROOT_DIR / "database"
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"

DEVICE_REGISTRY_PATH = CONFIG_DIR / "device_registry.json"
COMMAND_SCHEMA_PATH = CONFIG_DIR / "command_schema.json"
LANGUAGE_ALIASES_PATH = CONFIG_DIR / "language_aliases.json"
DEVICE_CAPABILITIES_PATH = CONFIG_DIR / "device_capabilities.json"

DB_PATH = DATABASE_DIR / "yolohome.db"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"

PROMPT_TEMPLATE_PATH = (
    ROOT_DIR / "modules" / "llm_integration" / "prompt_template.txt"
)


# =============================================================================
# Typed environment helpers
#
# Sai config phải BÁO LỖI NGAY LÚC KHỞI ĐỘNG, kèm thông điệp rõ ràng.
# Không được để nó âm thầm sống sót rồi nổ giữa lúc demo.
# =============================================================================

class ConfigError(ValueError):
    """Biến môi trường có giá trị không hợp lệ."""


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    value = raw.strip().lower()

    if value in {"true", "1", "yes", "y", "on"}:
        return True

    if value in {"false", "0", "no", "n", "off"}:
        return False

    raise ConfigError(
        f"{name}={raw!r} không hợp lệ. Dùng true/false (hoặc 1/0, yes/no)."
    )


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(
            f"{name}={raw!r} không hợp lệ. Cần một số nguyên, ví dụ {default}."
        ) from exc

    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={value} quá nhỏ. Giá trị tối thiểu là {minimum}.")

    return value


def env_float(
    name: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(
            f"{name}={raw!r} không hợp lệ. Cần một số thực, ví dụ {default}."
        ) from exc

    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={value} quá nhỏ. Giá trị tối thiểu là {minimum}.")

    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}={value} quá lớn. Giá trị tối đa là {maximum}.")

    return value


def env_choice(name: str, default: str, allowed: set[str]) -> str:
    """Giá trị phải nằm trong tập cho phép. Chuỗi rỗng = không set."""
    value = env_str(name, default)

    if not value:
        return ""

    if value not in allowed:
        raise ConfigError(
            f"{name}={value!r} không hợp lệ. "
            f"Chọn một trong: {', '.join(sorted(allowed))} "
            "(hoặc để rỗng để dùng mặc định của model)."
        )

    return value


# =============================================================================
# LLM: Gemini
# =============================================================================

GEMINI_API_KEY = env_str("GEMINI_API_KEY")

# LƯU Ý: tên model Gemini thay đổi liên tục. Tính tới 07/2026, cả
# gemini-2.5-flash và gemini-2.5-flash-lite đều đã trả 404 với user mới.
#
# Dùng model STABLE, pin cứng - KHÔNG dùng alias "-latest".
# Alias tự hot-swap sang model mới mỗi lần Google release, khiến kết quả
# benchmark trong báo cáo không tái lập được.
#
# Chạy `python -m tools.probe_gemini` để xem key của bạn dùng được model nào.
GEMINI_MODEL = env_str("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Parse câu lệnh thành JSON là bài toán deterministic -> temperature = 0.
GEMINI_TEMPERATURE = env_float("GEMINI_TEMPERATURE", 0.0, minimum=0.0, maximum=2.0)

# Parse câu lệnh ngắn -> JSON schema chặt KHÔNG cần suy luận sâu.
# Thinking mặc định (medium trên Gemini 3.x) đẩy latency lên hàng chục giây
# và gây 504 DEADLINE_EXCEEDED.
#
# - Gemini 3.x : thinking_level
# - Gemini 2.5 : thinking_budget (0 = tắt)
# Để rỗng nếu muốn dùng mặc định của model.
THINKING_LEVELS = {"minimal", "low", "medium", "high"}

GEMINI_THINKING_LEVEL = env_choice("GEMINI_THINKING_LEVEL", "minimal", THINKING_LEVELS)
GEMINI_THINKING_BUDGET = env_str("GEMINI_THINKING_BUDGET", "0")

# Ép model trả đúng JSON schema thay vì hy vọng prompt đủ mạnh.
# Nếu model không hỗ trợ, llm_module tự hạ cấp config (không cần tắt tay).
GEMINI_STRUCTURED_OUTPUT = env_bool("GEMINI_STRUCTURED_OUTPUT", True)


# =============================================================================
# LLM: OpenAI (dùng để benchmark, không bắt buộc)
# =============================================================================

OPENAI_API_KEY = env_str("OPENAI_API_KEY")
OPENAI_MODEL = env_str("OPENAI_MODEL", "gpt-4o-mini")


# =============================================================================
# LLM: runtime resilience
# =============================================================================

# Timeout mỗi lần gọi LLM (giây).
LLM_TIMEOUT_SECONDS = env_float("LLM_TIMEOUT_SECONDS", 15.0, minimum=1.0)

# Số lần thử LẠI sau lần đầu thất bại. 2 = tối đa 3 lần gọi.
# Gemini trả 503 (quá tải) khá thường xuyên nên mặc định để 2.
LLM_MAX_RETRIES = env_int("LLM_MAX_RETRIES", 2, minimum=0)

# Backoff giữa các lần thử lại (giây), tăng gấp đôi: 0.5s -> 1.0s -> 2.0s.
LLM_RETRY_BACKOFF_SECONDS = env_float("LLM_RETRY_BACKOFF_SECONDS", 0.5, minimum=0.0)

# true  -> luôn dùng mock parser (không gọi API, không tốn quota)
# false -> dùng Gemini nếu có GEMINI_API_KEY
USE_MOCK_LLM = env_bool("USE_MOCK_LLM", False)


# =============================================================================
# Hội thoại nhiều lượt (multi-turn)
# =============================================================================

# Câu hỏi làm rõ sống được bao lâu (giây).
# Nửa tiếng sau user nói "phòng khách" thì đó là câu nói MỚI, không phải
# câu trả lời cho câu hỏi cũ.
SESSION_TTL_SECONDS = env_float("SESSION_TTL_SECONDS", 180.0, minimum=0.0)

# Tối đa bao nhiêu lượt hỏi lại trước khi bỏ cuộc.
# Chặn vòng lặp clarify vô tận khi parser cứ hỏi mãi mà không tiến triển.
SESSION_MAX_TURNS = env_int("SESSION_MAX_TURNS", 3, minimum=1)


# =============================================================================
# Command Pattern
# =============================================================================

# Số lệnh gần nhất giữ lại để undo. Chạy 24/7 mà không giới hạn thì
# lịch sử lệnh sẽ phình vô hạn.
COMMAND_HISTORY_SIZE = env_int("COMMAND_HISTORY_SIZE", 20, minimum=1)


# =============================================================================
# Automation rules
# =============================================================================

# Vùng chết (deadband) quanh ngưỡng của rule.
#
# Ví dụ rule "nhiệt độ > 30": nếu nhiệt độ dao động 29.9 / 30.1 quanh ngưỡng,
# rule sẽ bật/tắt liên tục. Với hysteresis = 0.5, rule chỉ được "nạp lại"
# khi nhiệt độ xuống dưới 29.5, tránh việc quạt bật tắt xoành xoạch.
RULE_HYSTERESIS = env_float("RULE_HYSTERESIS", 0.5, minimum=0.0)


# =============================================================================
# Hardware / web
# =============================================================================

ADAFRUIT_IO_USERNAME = env_str("ADAFRUIT_IO_USERNAME")
ADAFRUIT_IO_KEY = env_str("ADAFRUIT_IO_KEY")

FLASK_ENV = env_str("FLASK_ENV", "development")
DATABASE_URL = env_str("DATABASE_URL", f"sqlite:///{DB_PATH}")


# =============================================================================
# Face recognition
# =============================================================================

FACE_AUTH_THRESHOLD = env_float(
    "FACE_AUTH_THRESHOLD",
    0.80,
    minimum=0.0,
    maximum=1.0,
)

# Bật/tắt nhánh Face Auth lúc khởi động.
#
# MẶC ĐỊNH TẮT là có chủ ý: test suite và CI không bao giờ được mở webcam hay
# nạp model vài trăm MB. Bật trong .env khi chạy demo thật.
#
# TẮT KHÔNG CÓ NGHĨA LÀ BỎ QUA XÁC THỰC. MainOrchestrator vẫn fail closed:
# thiếu face module thì door.open bị TỪ CHỐI. Cờ này quyết định có NẠP module
# hay không, không phải có KIỂM TRA hay không.
ENABLE_FACE_AUTH = env_bool("ENABLE_FACE_AUTH", False)

# Chỉ số webcam truyền cho cv2.VideoCapture. Máy có webcam rời, hoặc có phần
# mềm camera ảo (OBS, Zoom, DroidCam), thường đẩy webcam thật sang index 1-2.
CAMERA_INDEX = env_int("CAMERA_INDEX", 0, minimum=0)

# Bắt buộc chớp mắt trước khi chấp nhận khung hình (chống ảnh in / video phát
# trên điện thoại). Chỉ tắt khi phòng quá tối để dò được mắt - và nếu tắt thì
# PHẢI ghi rõ trong báo cáo, vì nó hạ mức bảo mật thật sự chứ không phải tinh
# chỉnh giao diện.
FACE_REQUIRE_BLINK = env_bool("FACE_REQUIRE_BLINK", True)

# Model SVM đã train. Khai báo ở đây để check_config() cảnh báo được khi
# thiếu file, thay vì để lỗi nổ lúc có người đứng trước camera.
FACE_MODEL_PATH = MODELS_DIR / "face_model.pkl"


# =============================================================================
# Speech-to-Text
# =============================================================================

# Cùng lý do với ENABLE_FACE_AUTH: mặc định tắt để test và CI không tải model.
ENABLE_STT = env_bool("ENABLE_STT", False)

# Tên model trên HuggingFace, hoặc đường dẫn tới checkpoint đã fine-tune.
#
# CỐ Ý dùng đúng tên biến môi trường mà stt_module đã đọc (PHOWHISPER_MODEL).
# Khai báo tên khác ở đây sẽ tạo hai nguồn sự thật cho cùng một sự việc: đổi
# một chỗ, chỗ kia âm thầm giữ giá trị cũ.
STT_MODEL_NAME = env_str("PHOWHISPER_MODEL", "vinai/PhoWhisper-base")


# =============================================================================
# Startup sanity checks
# =============================================================================

def check_config() -> list[str]:
    """
    Trả về danh sách cảnh báo về cấu hình.

    Không raise: đây là những thứ hợp lệ về kỹ thuật nhưng có thể
    không phải điều người dùng thực sự muốn.
    """
    warnings: list[str] = []

    if not USE_MOCK_LLM and not GEMINI_API_KEY:
        warnings.append(
            "USE_MOCK_LLM=false nhưng GEMINI_API_KEY rỗng. "
            "Hệ thống sẽ chạy mock parser, KHÔNG gọi Gemini."
        )

    if USE_MOCK_LLM and GEMINI_API_KEY:
        warnings.append(
            "USE_MOCK_LLM=true nên GEMINI_API_KEY sẽ bị bỏ qua. "
            "Đặt USE_MOCK_LLM=false để thật sự dùng Gemini."
        )

    if not ENV_PATH.exists():
        warnings.append(
            f"Không tìm thấy {ENV_PATH}. "
            "Copy .env.example thành .env rồi điền GEMINI_API_KEY."
        )

    if ENABLE_FACE_AUTH and not FACE_MODEL_PATH.exists():
        warnings.append(
            f"ENABLE_FACE_AUTH=true nhưng không tìm thấy {FACE_MODEL_PATH}. "
            "Face Auth sẽ tự tắt, và mọi lệnh mở cửa sẽ bị từ chối."
        )

    if ENABLE_FACE_AUTH and not FACE_REQUIRE_BLINK:
        warnings.append(
            "FACE_REQUIRE_BLINK=false: một tấm ảnh in cũng qua được xác thực. "
            "Chỉ dùng khi debug, tuyệt đối không dùng lúc demo."
        )

    if FACE_AUTH_THRESHOLD < 0.5:
        warnings.append(
            f"FACE_AUTH_THRESHOLD={FACE_AUTH_THRESHOLD} là quá thấp cho một "
            "khoá cửa. Giá trị khuyến nghị: 0.80."
        )

    return warnings


for _warning in check_config():
    logger.warning(_warning)