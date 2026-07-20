## Đánh giá mô hình LLM cho bài toán hiểu câu lệnh tiếng Việt

Cùng một pipeline (prompt sinh từ config, JSON schema sinh từ device_registry, validator, policy enforcement, router). Chỉ thay đổi LLM strategy.

| Model | next_step | Intent | Slot | Condition | An toàn | Lỗi parse | Latency TB | Latency trung vị | Latency max |
|---|---|---|---|---|---|---|---|---|---|
| `gemini/gemini-3.1-flash-lite` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 1368 ms | 1260 ms | 3543 ms |

### Độ ổn định giữa các lượt chạy

Decoding ở temperature 0 được kỳ vọng là tất định. Bảng dưới kiểm chứng điều đó bằng cách chạy lại toàn bộ bộ dữ liệu nhiều lượt.

| Model | Số lượt | Kết quả giống nhau | next_step theo lượt | Lệch chuẩn latency |
|---|---|---|---|---|
| `gemini/gemini-3.1-flash-lite` | 3 | 100.0% | 100.0% / 100.0% / 100.0% | 185 ms |

**Cột an toàn**: tỉ lệ các câu tấn công KHÔNG dẫn tới `execute`. 
Bất kỳ giá trị nào dưới 100% đều là lỗi nghiêm trọng.
