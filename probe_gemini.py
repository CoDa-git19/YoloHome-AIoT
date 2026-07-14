"""
Dò model Gemini khả dụng với API key hiện tại.

Chạy:
    python probe_gemini.py

Script sẽ:
1. Liệt kê mọi model text khả dụng với key của bạn.
2. Thử parse một câu lệnh thật với từng model ứng viên.
3. Đo latency và báo cáo model nào dùng được.

Không đụng vào code hệ thống. Chỉ để chọn GEMINI_MODEL cho .env.
"""

from __future__ import annotations

import time

from google import genai
from google.genai import types

from config.settings import (
    GEMINI_API_KEY,
    GEMINI_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
)
from modules.llm_integration.llm_module import build_prompt, build_response_schema


TRANSCRIPT = "bật đèn phòng khách"

# Bỏ qua các model không phải text-to-text.
EXCLUDE_KEYWORDS = (
    "image",
    "imagen",
    "veo",
    "embedding",
    "tts",
    "audio",
    "live",
    "vision",
    "aqa",
    "learnlm",
)


def list_text_models(client: genai.Client) -> list[str]:
    """Liệt kê model hỗ trợ generateContent, bỏ image/audio/embedding."""
    names: list[str] = []

    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" not in actions:
            continue

        name = (model.name or "").replace("models/", "")
        if not name:
            continue

        if any(keyword in name.lower() for keyword in EXCLUDE_KEYWORDS):
            continue

        names.append(name)

    return sorted(names)


def rank_candidates(names: list[str]) -> list[str]:
    """
    Ưu tiên model nhẹ, nhanh, ổn định cho bài toán parse câu lệnh ngắn.

    flash-lite > flash > pro, và ưu tiên bản stable hơn preview/exp.
    """

    def score(name: str) -> tuple[int, int, str]:
        lowered = name.lower()

        if "lite" in lowered:
            size = 0
        elif "flash" in lowered:
            size = 1
        elif "pro" in lowered:
            size = 2
        else:
            size = 3

        unstable = 1 if any(
            tag in lowered for tag in ("preview", "exp", "latest")
        ) else 0

        return (size, unstable, name)

    return sorted(names, key=score)


def build_config(structured: bool, thinking: bool):
    config: dict = {
        "temperature": GEMINI_TEMPERATURE,
        "http_options": types.HttpOptions(
            timeout=int(LLM_TIMEOUT_SECONDS * 1000),
        ),
    }

    if structured:
        config["response_mime_type"] = "application/json"
        config["response_schema"] = build_response_schema()

    if thinking and hasattr(types, "ThinkingConfig"):
        try:
            config["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
        except TypeError:
            try:
                config["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except (TypeError, ValueError):
                pass

    return types.GenerateContentConfig(**config)


def probe(client: genai.Client, model: str) -> dict:
    """Thử gọi model một lần, trả về kết quả và latency."""
    prompt = build_prompt(transcript=TRANSCRIPT)
    config = build_config(structured=True, thinking=True)

    start = time.time()
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        elapsed = int((time.time() - start) * 1000)
        text = (response.text or "").strip()

        return {
            "model": model,
            "ok": True,
            "latency_ms": elapsed,
            "output": text[:120].replace("\n", " "),
        }

    except Exception as exc:
        elapsed = int((time.time() - start) * 1000)
        message = str(exc)

        return {
            "model": model,
            "ok": False,
            "latency_ms": elapsed,
            "output": message[:120].replace("\n", " "),
        }


def main() -> None:
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY chưa được set trong .env")
        return

    client = genai.Client(api_key=GEMINI_API_KEY)

    print("=" * 78)
    print("MODEL TEXT KHẢ DỤNG VỚI KEY NÀY")
    print("=" * 78)

    try:
        names = list_text_models(client)
    except Exception as exc:
        print(f"Không liệt kê được model: {exc}")
        return

    if not names:
        print("Không tìm thấy model text nào.")
        return

    for name in names:
        print(f"  {name}")

    candidates = rank_candidates(names)[:6]

    print()
    print("=" * 78)
    print(f"THỬ PARSE {TRANSCRIPT!r} (ưu tiên model nhẹ/nhanh trước)")
    print("=" * 78)
    print(f"{'MODEL':<40} {'KẾT QUẢ':<10} {'LATENCY':>10}")
    print("-" * 78)

    working: list[dict] = []

    for model in candidates:
        result = probe(client, model)

        status = "OK" if result["ok"] else "LỖI"
        print(f"{result['model']:<40} {status:<10} {result['latency_ms']:>8} ms")
        print(f"    -> {result['output']}")

        if result["ok"]:
            working.append(result)

    print()
    print("=" * 78)

    if not working:
        print("KHÔNG model nào chạy được. Có thể do quota, hoặc mọi model đang quá tải.")
        print("Thử lại sau vài phút, hoặc kiểm tra quota trên Google AI Studio.")
        return

    best = min(working, key=lambda r: r["latency_ms"])

    print("KHUYẾN NGHỊ - thêm dòng này vào .env:")
    print()
    print(f"    GEMINI_MODEL={best['model']}")
    print()
    print(f"(nhanh nhất: {best['latency_ms']} ms)")
    print("=" * 78)


if __name__ == "__main__":
    main()