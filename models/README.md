# Model nhận diện khuôn mặt KHÔNG nằm trong repo

`.gitignore` chặn `models/*` và `*.pkl`, nên thư mục này sau khi clone chỉ có
`.gitkeep`. Thiếu file, `SvmFaceRecognizer` ném `FileNotFoundError`, `main.py`
bắt lỗi và đặt `face_module = None` — theo nguyên tắc fail-closed thì **mọi lệnh
cần xác thực đều bị từ chối**. Hệ thống chạy bình thường, không crash, và không
có gì báo rằng bạn đang thiếu file.

## Cần gì

| File | Mô tả |
|---|---|
| `face_model.pkl` | SVM đã train, kèm LabelEncoder |

Tải tại: <dán link Google Drive>

Kiểm tra sau khi đặt file vào:

    python -c "import pickle; print(list(pickle.load(open('models/face_model.pkl','rb')).classes_))"

## ⚠️ Về scikit-learn

`face_model.pkl` là pickle của sklearn. Bản sklearn lúc load **phải khớp** bản
lúc train (`requirements.txt` ghim 1.9.0). Lệch bản có thể predict SAI mà không
báo lỗi — nguy hiểm hơn crash.

## ⚠️ Về bảo mật

`pickle.load()` THỰC THI mã khi đọc. Chỉ nhận file này từ nguồn tin cậy trong
nhóm, không tải từ đâu khác về.