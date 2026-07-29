# Design Principles

Tài liệu này ghi lại **tám nguyên lý** chi phối cách YoloHome-AIoT được viết.
Chúng không phải quy ước phong cách. Mỗi nguyên lý ra đời từ một lỗi có thật
trong quá trình phát triển, và mỗi nguyên lý đều có test khoá lại.

Khi review code hoặc thêm module mới, đây là danh sách để đối chiếu.

---

## 1. Mô hình diễn giải. Server quyết định.

> *The model interprets. The server decides.*

Một thành phần xác suất — LLM hay model nhận diện khuôn mặt — được phép **báo
cáo nó nhận thấy gì**. Nhưng cái biến nhận thức thành **quyền hạn** luôn là cấu
hình phía server.

**Áp dụng ở năm chỗ, cùng một lập luận:**

| # | Thành phần báo cáo | Server quyết định | Nơi thực thi |
|---|---|---|---|
| 1 | LLM trả `face_auth` | `device_capabilities.json` | `validator.enforce_policy()` |
| 2 | Face model trả `confidence` | `FACE_AUTH_THRESHOLD` | `AuthService` |
| 3 | LLM soạn câu hỏi làm rõ | `device_registry.json` quyết định **tập lựa chọn** | `missing_slot_question()` |
| 4 | LLM điền giá trị slot | Câu người dùng quyết định **có bằng chứng hay không** | `ground_slots()` |
| 5 | LLM phân loại phòng lạ | Registry quyết định phòng nào tồn tại | `detect_unsupported_room()` |

`enforce_policy()` ghi đè theo **cả hai chiều**: ép `True` khi policy yêu cầu mà
LLM trả `false`, và ép `False` khi policy không yêu cầu mà LLM trả `true`.

`AuthService` **cố ý bỏ qua** cờ `authorized` nếu face module có trả về. Tin nó
nghĩa là một model bị thay thế có thể tự cấp quyền cho chính mình.

Chỗ số 4 sinh ra từ một ca quan sát được trên console:

```
Bot : Nhà bếp chưa được đăng ký. Bạn có muốn gửi yêu cầu không?
User: có
Bot : Đã bật đèn phòng ngủ.
```

Chữ `"có"` không nhắc tới phòng nào. Model tự chọn `bedroom`, server thấy tổ hợp
hợp lệ nên cho chạy — và **phần cứng bị tác động thật** theo một giá trị không
ai nói ra.

Chỗ số 3 sinh ra từ ca ngược lại: bot gợi ý *"phòng ngủ, phòng khách, cửa chính"*
khi người dùng hỏi về đèn — mà cửa chính không có đèn. Người dùng làm đúng lời
bot dặn và vẫn bị từ chối.

**Test khoá lại:** `test_security_policy.py`, `test_auth_service.py`,
`test_slot_suggestions.py`, `test_slot_grounding.py`, `test_unsupported_room.py`

---

## 2. Fail closed

Thiếu module, camera lỗi, model ném exception, mạng chết, database khoá — tất cả
đều dẫn tới **TỪ CHỐI**. Không có nhánh nào mang nghĩa "vì không chắc nên cho qua".

Chi phí của một lần từ chối nhầm là người dùng quét lại. Chi phí của một lần cho
qua nhầm là cửa nhà mở.

**Điểm cần nhớ:** cờ `face_auth=True` chỉ **định tuyến** lệnh sang `AuthService`,
nó không tự chặn gì. `CommandService.execute_authorized_command()` cũng **không**
kiểm tra lại. Vì vậy `AuthService` là **chốt chặn duy nhất ở thời điểm thực thi**.

Hệ quả trực tiếp: `execute_authorized_command()` chỉ được gọi ở đúng **hai chỗ**
trong toàn hệ thống — `AuthService.authorize_and_execute()` (sau khi xác thực) và
`RuleObserver.update()` (chỉ cho lệnh **không** cần face auth).

**Fail closed có thể bị vô hiệu từ bên ngoài.** `face_recognition` gọi `quit()`
khi thiếu gói model; `quit()` ném `SystemExit`, vốn kế thừa `BaseException` chứ
không kế thừa `Exception`. Một khối `except Exception` để nó lọt qua và cả gateway
thoát giữa lúc boot. `_wire_face_auth()` vì vậy bắt `(Exception, SystemExit)` —
nhưng **không** bắt `BaseException`, để Ctrl+C vẫn dừng được chương trình.

**Test khoá lại:** mọi test trong `test_auth_service.py` đều assert số lần gọi
`execute_authorized_command()`, không chỉ assert chuỗi trả về. Một test chỉ kiểm
tra câu trả lời sẽ vẫn xanh ngay cả khi hệ thống mở cửa cho người lạ.

---

## 3. Hỏng thì phải kêu

Lỗi tệ nhất không phải lỗi làm sập chương trình. Lỗi tệ nhất là lỗi khiến hệ
thống **báo thành công rồi không làm gì cả** — hoặc **báo sai nguyên nhân**.

**Danh mục lỗi âm thầm đã gặp trong dự án này:**

| Lỗi | Biểu hiện | Cách chặn |
|---|---|---|
| Observer chưa attach | Rule lưu vào DB, báo "đã tạo", không bao giờ chạy | attach tường minh + `test_observer_pattern.py` |
| `use_mock=True` mặc định | Bỏ qua Gemini hoàn toàn, kết quả trông vẫn đúng | in `[Config] USE_MOCK_LLM` lúc boot |
| Thiếu ffmpeg | `transcribe()` trả `""`, trông như model không nghe được | `_check_ffmpeg()` chạy đầu tiên |
| ABC dự phòng trong `except ImportError` | `isinstance()` trả `False` mà công cụ test vẫn chạy ngon | `_check_strategy_identity()` |
| Đổi tên khoá cảm biến | Mọi automation rule ngừng kích hoạt, không lỗi, không log | 4 khoá được ghi rõ là **hợp đồng** |
| Thiếu `"state"` khi `get_status` | Người dùng luôn nhận "không xác định được trạng thái" | `contracts.check_status_result()` |
| Nhãn `Unknown` truthy | Người lạ **mở được cửa** | `_as_identity()` + `REJECT_NAMES`, hai lớp |
| **Câu trả lời do LLM soạn trước khi validate** | Log ghi `failed`, người dùng nghe `"Đã bật đèn"` | `failure_response()` |
| **Tên phòng nuốt tên thiết bị** | `"bật đèn cửa chính"` thành lệnh **mở cửa** | `detect_device_excluding_room()` |
| **Thư viện in gợi ý sai sự thật** | Bảo cài gói đã có sẵn; nguyên nhân thật bị nuốt | chạy thẳng `import` để lộ lỗi |

Ba dòng cuối là mới, và dòng cuối cùng đến từ **bên ngoài code của nhóm**. Nguyên
lý này không dừng ở ranh giới dự án: một thư viện bắt `Exception` rồi in thông báo
sai cũng gây mất hàng giờ y như lỗi tự viết. Xem `Setup-Windows.md` §5.

Nguyên tắc rút ra: **nếu một thứ có thể hỏng mà không ai biết, phải có chỗ hét
lên** — log ERROR, dòng `[Config]` lúc boot, hoặc một test đỏ.

---

## 4. Một sự việc, một nguồn sự thật

Danh sách phòng, thiết bị, hành động, cảm biến, câu hỏi, câu trả lời — mỗi thứ
chỉ được định nghĩa **đúng một lần**.

**Mẫu lỗi này lặp lại ba lần trong cùng một đợt sửa:**

| Lần | Chỗ trùng lặp | Hậu quả |
|---|---|---|
| 1 | Câu hỏi clarify chép ở `validate_mock_slots` | mock hỏi khác Gemini |
| 2 | Câu trả lời thành công chép ở 3 chỗ | mock trả câu chung chung cho cả bật lẫn tắt |
| 3 | Ba nhánh soạn clarify khác nhau | lượt clarify đầu của Gemini mất danh sách gợi ý |

Cả ba đều được phát hiện bằng **so sánh mock với Gemini trên cùng input**, không
phải bằng đọc code. `MockLLMStrategy` vì vậy không chỉ là công cụ test — nó là
**đặc tả thi hành được** của hành vi mong muốn. Chỗ nào hai engine lệch nhau là
chỗ đó có bug.

Cùng lý do: `settings.STT_MODEL_NAME` đọc **đúng biến môi trường** mà
`stt_module` đã đọc (`PHOWHISPER_MODEL`), không đặt tên mới.

**Hệ quả về bảo mật, ghi lại chứ không giấu:** quyền ghi vào
`command_schema.json` và `device_capabilities.json` tương đương quyền điều khiển
toàn bộ chính sách xác thực. Rủi ro được **dời chỗ**, không bị loại bỏ.

---

## 5. Test phải chạy offline

Toàn bộ test suite chạy được trên máy **không** cài dlib, **không** có webcam,
**không** có micro, **không** có API key, **không** có phần cứng.

Vi phạm nguyên tắc này thì CI không chạy được, và các thành viên không làm phần
AI cũng bị chặn.

**Ba kỹ thuật giữ nguyên tắc này:**

1. **Lazy import.** `cv2` / `face_recognition` / `numpy` import **bên trong hàm**.
   Đổi lại, `_wire_face_auth()` nạp sẵn lúc boot để lần nhận diện đầu không khựng
   và lỗi thiếu thư viện vẫn lộ ra sớm.
2. **Tách logic thuần khỏi logic cần I/O.** `SvmFaceRecognizer._as_identity()`
   chỉ xử lý chuỗi, nên test được mà không cần camera hay model.
3. **Dependency injection.** `AuthService(logging_service=...)` cho phép tiêm
   LoggingService giả, test không cần database.

**Cảnh báo:** cách "chữa" bằng `pytest.importorskip` là **sai**. Trên máy thiếu
thư viện, test sẽ **âm thầm skip** và pytest vẫn báo xanh — test an ninh quan
trọng nhất biến mất mà không ai biết. Đó chính là lỗi mà nguyên lý 3 cấm.

**Công cụ thủ công đặt trong `tests/`** (tên bắt đầu bằng `test_` nên pytest sẽ
import) phải để mọi phụ thuộc nặng trong `main()`, kèm `__test__ = False`.

---

## 6. Một tài nguyên, một chủ sở hữu

Mỗi tài nguyên hệ thống chỉ có **đúng một** chủ sở hữu chịu trách nhiệm mở và
đóng nó.

| Tài nguyên | Chủ sở hữu | Người dùng |
|---|---|---|
| `cv2.VideoCapture` | `MainOrchestrator` | `face_module.capture_frame(cap=...)` |
| Kết nối Adafruit IO | `HardwareModule` | `AdafruitPublisher` |
| `LLMStrategy` | `MainOrchestrator` | `CommandService`, benchmark |

Hai chỗ cùng mở webcam thì trên Windows chỗ thứ hai nhận `None`, và Face Auth từ
chối mọi lệnh **mà không rõ nguyên nhân**.

`capture_frame()` phản ánh nguyên tắc này bằng cờ `owns_capture`: chỉ đóng camera
nếu chính nó mở.

Cùng nguyên tắc áp cho tầng mạng: hệ thống từng có **hai đường** tới Adafruit IO
— MQTT để nhận cảm biến, REST để đẩy dữ liệu lên. Đường thứ hai vừa thừa vừa gây
echo: thiết bị publish `home-temperature`, backend subscribe nhận về rồi publish
ngược lên chính feed đó, nhân đôi data point trên gói free 30 điểm/phút.

---

## 7. Hợp đồng được kiểm tra lúc khởi động

Sai lệch interface phải lộ ra **lúc boot**, không phải giữa lúc demo.

`system_core/contracts.py` kiểm tra chữ ký của `LLMStrategy` và sự tồn tại của
`execute_command()` ngay khi khởi tạo. `settings.check_config()` in cảnh báo lúc
import — bật `ENABLE_FACE_AUTH=true` mà thiếu `face_model.pkl` thì biết ngay.

Nguyên tắc này mở rộng sang **phụ thuộc lúc chạy**, không chỉ interface nội bộ.
Phiên bản `scikit-learn` dùng để train `face_model.pkl` là một hợp đồng ngầm:
pickle load được qua các bản minor kèm cảnh báo, nhưng khi hỏng thì nó hỏng theo
kiểu tệ nhất — **unpickle thành công nhưng dự đoán sai**. Xem `Setup-Windows.md` §3.

---

## 8. Ghi lại giả định tin cậy, đừng giấu

Hệ thống nào cũng có những chỗ phải tin. Nguyên tắc không phải là loại bỏ chúng —
mà là **viết ra**.

- `pickle.load()` trên `models/face_model.pkl` **thực thi mã** trong file. Ai ghi
  được vào file đó thì chiếm được tiến trình gateway.
- Quyền ghi `command_schema.json` / `device_capabilities.json` tương đương quyền
  điều khiển chính sách xác thực.
- `FACE_REQUIRE_BLINK=false` khiến một tấm ảnh in cũng qua được xác thực.
  `check_config()` cảnh báo nếu bật cờ này.
- Liveness hiện xác nhận trên **cả khung hình**, không gắn với khuôn mặt cụ thể:
  kẻ lạ có thể cầm ảnh in của người nhà đứng cạnh mặt mình rồi tự chớp mắt.
  Đã đánh dấu `TODO` trong `recognize()`.
- Đường dẫn dự án phải toàn ký tự ASCII — `dlib` và `OpenCV` là thư viện C++,
  không mở được file qua đường dẫn chứa ký tự tiếng Việt.

Giả định được ghi lại là một quyết định kỹ thuật. Giả định không được ghi lại là
một lỗ hổng đang chờ.

---

## Danh sách đối chiếu khi thêm module mới

- [ ] Model/LLM chỉ **báo cáo**; quyết định nằm ở server?
- [ ] Mọi đường lỗi đều dẫn tới **từ chối**, không phải cho qua?
- [ ] Có thứ gì hỏng được mà **không ai biết** không? Nếu có, chỗ nào hét lên?
- [ ] Dữ liệu này đã tồn tại ở file config nào chưa?
- [ ] Test chạy được trên máy **không** cài thư viện nặng chứ?
- [ ] Tài nguyên (camera, kết nối, client) có **đúng một** chủ sở hữu chứ?
- [ ] Sai interface thì lộ ra **lúc boot** hay lúc demo?
- [ ] Có giả định tin cậy nào chưa được viết ra không?
