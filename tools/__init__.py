"""
Công cụ chẩn đoán và đo đạc cho phần LLM.

Chạy từ THƯ MỤC GỐC của project, dùng cờ -m:

    python -m tools.check_setup      # kiểm tra config + DB + pipeline (0 quota)
    python -m tools.probe_gemini     # dò model khả dụng với API key
    python -m tools.smoke_gemini     # 10 câu, gọi Gemini thật (~10 request)
    python -m tools.benchmark_llm    # benchmark đầy đủ (~32 request/model)

KHÔNG chạy `python tools/check_setup.py` - Python sẽ không tìm thấy package
config/ vì thư mục gốc không nằm trong sys.path.
"""