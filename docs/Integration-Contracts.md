# Integration Contracts

This document is the precise, buildable spec for the two seams where other
modules plug into the LLM command pipeline: **STT → LLM** and **LLM → Face Auth**.

If you own the STT or Face module, build to the shapes below. The LLM side of
both contracts is already implemented and contract-checked at startup
(`system_core/contracts.py`), so mismatches surface on boot, not mid-demo.

> Nguyên tắc bảo mật xuyên suốt: **LLM hiểu ý định, KHÔNG ra quyết định an ninh.**
> `face_auth` do server quyết định (`validator.enforce_policy`), và tầng thực thi
> chỉ chạy sau khi Face Auth đã xác thực thật.

---

## Contract A — STT → LLM

### What the STT owner implements

The interface already exists in `system_core/strategies.py`:

```python
class STTStrategy(ABC):
    @abstractmethod
    def transcribe(self, audio_data: bytes) -> str: ...
```

That is the entire contract: **`bytes → str`**. STT never calls the LLM directly.
The orchestrator (`system_core/main.py`) wires them:

```python
transcript = stt.transcribe(audio_bytes)
result = command_service.handle_transcript(transcript, sensor_data, session_id)
```

### Requirements on the returned string

- **Natural Vietnamese with diacritics**, e.g. `"bật đèn phòng khách"`. The mock
  parser matches against `config/language_aliases.json`, and Gemini receives the
  raw string — both expect real Vietnamese, not ASCII-folded/normalized text.
- **Do not pre-lowercase or strip punctuation.** `llm_module` lowercases
  internally on the mock path; doing it upstream is harmless but pointless.
- **Empty/whitespace is safe, not a crash.** An empty transcript makes
  `parse_and_validate` return `ok=False, next_step="stop"`. STT should avoid
  emitting empty strings for silence, but the pipeline will not break if it does.

STT has no obligation to supply `sensor_data` or `pending_command` — those are the
orchestrator's to pass into `handle_transcript`.

---

## Contract B — LLM → Face Auth

Sensitive actions (currently only `door.open`, per `config/command_schema.json`
→ `sensitive_actions`) route through Face Auth before any hardware runs.

### Step 1 — the LLM pipeline emits the handoff

`CommandService.handle_transcript(...)` returns:

```python
{
    "command_id": 42,                 # <-- Face owner MUST keep this to close the log
    "next_step": "auth_required",
    "command": {                      # face_auth already FORCED True server-side
        "intent": "control_device",
        "action": "open",
        "device": "door",
        "room": "main_door",
        "face_auth": True,
        "condition": None,
        "response": "..."
    },
    "execution_status": "waiting_auth",
    ...
}
```

The `command` dict is already policy-enforced. **Pass it through unchanged** — do
not rebuild it.

### Step 2 — authenticate

`services/auth_service.py` asks the Face module to recognize a face, then decides
authorization against the threshold:

```python
authorized = (person_name is not None) and (confidence >= FACE_AUTH_THRESHOLD)
```

`FACE_AUTH_THRESHOLD` comes from `config/settings.py` (default `0.80`).

The Face module (`modules/face_recognition/face_module.py`) implements:

```python
class FaceRecognizer(ABC):
    @abstractmethod
    def recognize(self, frame) -> dict:
        """Returns {"person_name": str | None, "confidence": float}."""
```

> The threshold decision lives in `auth_service`, not the Face module. If the Face
> module also returns an `authorized` flag, it is **advisory only** — `auth_service`
> is the authority, because the threshold is server config.

### Step 3 — execute, but only on success

```python
ok = command_service.execute_authorized_command(result["command"])  # -> bool
```

### ⚠️ Two things the Face owner MUST know

1. **`execute_authorized_command` is not a security gate.** It runs the hardware
   action and returns a bool — it does **not** re-check `face_auth`. The
   `face_auth=True` flag only *routes* to auth; it does not block hardware. Therefore
   **`auth_service` is the sole enforcement point at execution time.** Never call
   `execute_authorized_command` unless authentication actually passed.

2. **Closing the DB log is the Face owner's job.** Step 1 leaves the `command_log`
   row in `waiting_auth` state and writes no `face_log` row.
   `execute_authorized_command` touches no database. Using the `command_id` from
   Step 1:

   ```python
   from services.logging_service import log_face, update_command_result

   update_command_result(
       command_id,
       result="success" if ok else "fail: hardware_error",
       execution_status="success" if ok else "failed",
   )
   log_face(
       person_name=person_name or "unknown",
       confidence=confidence,
       status="authorized" if authorized else "denied",  # {authorized|denied|no_face|timeout}
       command_id=command_id,
       device="door",
       room="main_door",
   )
   ```

   - `log_face` — `services/logging_service.py:233`; `confidence` must be in `[0.0, 1.0]`.
   - `update_command_result` — `services/logging_service.py:276`.
   - Valid `face_log.status`: `authorized`, `denied`, `no_face`, `timeout`.

`services/auth_service.py` already implements this glue (threshold, execute,
logging). The Face owner only needs to implement `FaceRecognizer.recognize()` and
wire in a real camera frame; the rest works as-is with the provided
`MockFaceRecognizer`.

---

## Summary

| Seam | Their obligation | LLM side (done) |
|---|---|---|
| STT → LLM | `transcribe(bytes) -> str`, natural Vietnamese | `parse_and_validate(transcript, ...)`, contract-checked at startup |
| LLM → Auth | Auth-gate, call `execute_authorized_command(command)`, write `log_face` + `update_command_result` via `command_id` | Emit `next_step="auth_required"` with enforced `command` + `command_id` |

See also [`LLM-Command-Strategy-Overview.md`](LLM-Command-Strategy-Overview.md) for
the full pipeline and the `next_step` routing table.

---

## Reference — how `main.py` wires it together

Reference only, **not prescriptive**. It shows the three contract touchpoints the
orchestrator must hit; the loop structure, camera capture, threading, and shutdown
are the System owner's call.

```python
stt = MockSTTStrategy()                 # -> real STTStrategy later
command_service = CommandService(use_mock=True)
auth_service = AuthService(command_service)   # -> pass real FaceRecognizer later

# (1) Observer wiring — MUST run, or automation rules fail SILENTLY.
#     No error, no log, tests still pass; rules just never fire.
hardware = command_service.hardware_module
hardware.attach(RuleObserver(command_service.rule_service, command_service))

while True:
    hardware.poll_sensors()             # (2) heartbeat that drives the rules

    audio = capture_audio()             # System/STT owner supplies this
    transcript = stt.transcribe(audio)  # (3a) STT contract: bytes -> str
    result = command_service.handle_transcript(transcript, session_id=session_id)

    if result["next_step"] == "auth_required":
        # (3b) auth handoff: pass the enforced command straight through.
        frame = capture_frame()         # Face owner supplies this
        outcome = auth_service.authorize_and_execute(result, frame=frame)
        say(outcome["response"])
    else:
        say(result["response"])

    time.sleep(2)
```

Check the exact `RuleObserver` name/signature against `system_core/observers.py`
and `services/rule_service.py` before wiring — treat the import above as a
placeholder, not a guarantee.

The three numbered points map to the contracts above: **(1)+(2)** the observer +
poll loop (the silent-failure risk), **(3a)** Contract A, **(3b)** Contract B.
