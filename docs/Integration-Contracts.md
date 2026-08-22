# Integration Contracts

The precise, buildable spec for the **three seams** where modules plug into the
command pipeline: **STT → LLM**, **LLM → Face Auth**, and **LLM → Hardware**.

The LLM side of all three is implemented and contract-checked at startup
(`system_core/contracts.py`), so interface mismatches surface on boot, not
mid-demo.

> **Governing principle: the model interprets, the server decides.**
> The LLM infers intent but makes no security decisions. `face_auth` is decided
> by `validator.enforce_policy()`, the recognition threshold by `AuthService`,
> and hardware runs only after authentication actually succeeds.
> See `Design-Principles.md` §1 for the five places this principle is applied.

---

## Contract A — STT → LLM

### Interface

Already defined in `system_core/strategies.py`:

```python
class STTStrategy(ABC):
    @abstractmethod
    def transcribe(self, audio_data: bytes) -> str: ...
```

The entire contract is **`bytes → str`**. STT never calls the LLM directly; the
orchestrator (`system_core/main.py`) wires the two:

```python
transcript = stt.transcribe(audio_bytes)
result = command_service.handle_transcript(transcript, sensor_data, session_id)
```

Two implementations satisfy this contract, selected by `STT_ENGINE` in `.env`:

| `STT_ENGINE` | Class | Backend | File |
|---|---|---|---|
| `phowhisper` (default) | `PhoWhisperSTT` | transformers / PyTorch | `stt_module.py` |
| `faster-whisper` | `FasterWhisperSTT` | CTranslate2 | `stt_faster_whisper.py` |

Both load lazily, both preserve punctuation and diacritics as required below, and
both return `""` rather than raising on failure. Swapping engines must not change
observable behaviour beyond accuracy and latency — that is the point of the
Strategy boundary, and it held up in practice: adding the second engine required
one branch in `_build_stt()` and one line in `.env`, with no change to
`CommandService`, `AuthService`, or the dashboard.

### Requirements on the returned string

**Vietnamese with diacritics — this is a hard requirement, not a preference.**

Since slot grounding was introduced (`llm_module.ground_slots`), the server
matches every `room` / `device` value against the aliases in
`config/language_aliases.json`, and those aliases carry diacritics. An
ASCII-folded transcript loses every slot:

| Transcript | Outcome |
|---|---|
| `bật đèn phòng khách` | executes |
| `bat den phong khach` | all slots dropped, bot re-asks |
| `bật đèn phòng khác` | `"khác"` ≠ `"khách"`, `room` dropped |

The third case is the nastiest: one tone mark off, and a listener hears no
difference.

**Only `room` and `device` are grounded.** `action` is not, so errors on the verb
usually don't break the command. Observed in a live run:

```
Spoken : tắt quạt phòng ngủ
STT    : tắc đoạt phòng ngủ.          <- 2 of 4 words wrong
Bot    : Bạn muốn tắt thiết bị nào ở phòng ngủ? Hiện có: quạt, đèn.
```

`action` and `room` survived, only `device` was lost, and the bot re-asked for
exactly the missing slot.

That tolerance has a limit, and the limit is the **first syllable**. A dropped
leading consonant does not degrade the command, it removes the verb entirely:

```
Spoken : tắt quạt đi
STT    : quạt đi                      <- 1 of 3 words lost
Result : device with no action; the LLM has nothing to route
```

Observed with `faster-whisper` at `compute_type="int8"` — see *Current
implementation* below. When choosing between engines, weight errors on the verb
far more heavily than the raw WER suggests.

Remaining requirements:

- **Do not pre-lowercase or strip punctuation.** `llm_module` lowercases
  internally on the mock path; doing it upstream is harmless but pointless.
  Note this cuts the other way when *measuring* quality: `evaluate_model.py`
  normalises punctuation before comparing, because a trailing period is the
  grader's problem, not the model's.
- **Empty/whitespace must be safe, not a crash.** An empty transcript makes
  `parse_and_validate` return `ok=False, next_step="stop"`.

STT has no obligation to supply `sensor_data` or `pending_command` — those belong
to the orchestrator.

### Current implementation

`modules/speech_recognition/stt_module.py`:

- `PhoWhisperSTT` — HuggingFace model, loaded **lazily** on the first
  `transcribe()` call. That first call absorbs ~10 s of model loading on CPU;
  subsequent calls average **1.29 s** for 4 s of audio.
- `MockSTTStrategy` — returns a canned transcript, used when `USE_MOCK_STT=true`.

`modules/speech_recognition/stt_faster_whisper.py`:

- `FasterWhisperSTT` — the same checkpoint converted to CTranslate2, averaging
  **0.77 s** on the same audio. Requires a separate conversion step; the command
  is in that file's header.

Measured 2026-08-22, Windows/CPU, n=3. faster-whisper is ~1.7× faster but dropped
the leading syllable on two of three samples (WER 0.194 vs 0.000), which is why
`phowhisper` remains the default. Full numbers, method notes and caveats:
`docs/STT-Evaluation.md`.

**System dependency: `ffmpeg` must be on PATH.** Both engines call
`normalize_audio_bytes()`, so without ffmpeg `transcribe()` returns an empty
string **silently** — it looks like the model heard nothing. See
`docs/Setup-Windows.md` §8.

**Model weights are not in the repository.** `.gitignore` excludes
`*.safetensors` and `*.bin`, so `finetune_final/` and `finetune_final_ct2/`
contain only JSON config after a fresh clone. Weights come from HuggingFace; see
`.env.example`. Without them the system falls back to the base
`vinai/PhoWhisper-base` and keeps working — which is exactly the danger, since
the fine-tuning is silently absent. `check_config()` warns at startup and
`/api/status` reports the model actually loaded.

**Converting to CTranslate2 requires `--copy_files tokenizer.json`.** Without it
faster-whisper silently downloads `openai/whisper-tiny`'s tokenizer instead —
wrong vocabulary, degraded transcripts, and a single `httpx` log line buried in
the INFO stream as the only sign.

---

## Contract B — LLM → Face Auth

Sensitive actions (currently only `door.open`, per
`config/command_schema.json` → `sensitive_actions`) route through Face Auth
before any hardware runs.

### Step 1 — the pipeline emits the handoff

`CommandService.handle_transcript(...)` returns:

```python
{
    "command_id": 42,                 # MUST be kept in order to close the log
    "next_step": "auth_required",
    "command": {                      # face_auth already FORCED True server-side
        "intent": "control_device",
        "action": "open",
        "device": "door",
        "room": "main_door",
        "face_auth": True,
        "condition": None,
        "response": "...",
    },
    "execution_status": "waiting_auth",
    ...
}
```

The `command` dict has already been through `enforce_policy()`. **Pass it through
unchanged** — do not rebuild it.

### Step 2 — obtain a frame

> **REVISED FROM THE PREVIOUS VERSION.** The old contract said the orchestrator
> opens `cv2.VideoCapture` once at boot and hands down **one** frame. That is
> incompatible with liveness detection: a blink cannot be detected in a single
> frame.

Current architecture:

```python
frame = orchestrator.capture_frame()
```

`capture_frame()` delegates to `face_module.capture_frame(...)`, which **opens
the camera, reads many frames during one scan, then closes it**. The webcam is
therefore free whenever the system isn't scanning — OBS, Zoom and screen
recorders keep working during the demo.

`orchestrator.camera` is a secondary path reserved for tests and headless
contexts: it grabs a **single** frame and therefore **cannot** detect a blink.
Using it while `FACE_REQUIRE_BLINK=true` writes an `error_log` warning, because
anti-spoofing is silently disabled with nothing else to signal it.

`capture_frame()` returns `None` on camera failure, timeout, or no blink
detected. **`None` must be treated as `no_face` and denied** — fail closed.

> **The preview window is a console-only affordance.** `capture_frame()` calls
> `cv2.imshow()` when `show_window=True`, which is safe from `system_core/main.py`
> because the scan runs on the main thread. It is **not** safe from a Flask
> worker thread: on Windows the window either fails to appear or hangs the
> request. The dashboard therefore passes `show_window=False` and shows the saved
> snapshot after the fact instead — see Step 4.

### Step 3 — authenticate

`services/auth_service.py` is implemented:

```python
AuthService(
    face_recognizer=...,      # object with recognize(frame) -> dict
    command_service=...,      # object with execute_authorized_command(cmd) -> bool
    threshold=None,           # None -> FACE_AUTH_THRESHOLD
    logging_service=None,     # None -> real functions; tests inject a fake
)

outcome = auth_service.authorize_and_execute(result, frame=frame)
```

The decision rule:

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

> The threshold decision lives in `auth_service`, **not** in the Face module. If
> the Face module also returns an `authorized` flag, it is **advisory only** —
> `auth_service` deliberately ignores it and recomputes, because the threshold is
> server configuration.

**Stranger labels must never become an identity.** The training set contains an
`Unknown` class, and `"Unknown"` is a **truthy** string — meaning that when the
model correctly identifies a stranger with 0.93 confidence, the rule above would
**open the door**: right model, right threshold, wrong data type.

Blocked at **two layers**, deliberately:

- `SvmFaceRecognizer.REJECT_LABELS` — converts the label to `person_name=None`
- `auth_service.REJECT_NAMES` — checks again

The duplication is not redundant: the Face module could be swapped for another
implementation that forgets this rule, and the last layer before hardware runs
should not rely on the goodwill of the layer above it.

**`denied` has two distinct causes, and conflating them is expensive.** Either
the model classified a stranger (`person_name=None`, confidence possibly *high*),
or it recognised a known person below the threshold. Reporting only "confidence
below threshold" sends people to lower `FACE_AUTH_THRESHOLD` on a door lock to
fix something that was never a threshold problem — a real incident, observed at
87% confidence against an 80% threshold. Anything consuming `face_log.status`
should read `person_name` before explaining *why* a scan was denied.

### Step 4 — execute and close the log

`AuthService` handles both; callers do not touch them:

```python
executed = command_service.execute_authorized_command(command)   # -> bool
update_command_result(command_id, result=..., execution_status=...)
log_face(
    person_name=..., confidence=..., status=..., command_id=...,
    snapshot_path=...,      # filename of the captured frame, or None
)
```

Valid `face_log.status` values: `authorized`, `denied`, `no_face`, `timeout`.

A stranger **still gets** a `face_log` row — `person_name` is `NOT NULL`, so
`"unknown"` is written. Without that row every rejection disappears from history,
which is exactly what you need most when investigating an incident.

`confidence` is preserved even when `person_name=None`, so the audit trail can
record *"recognised with 0.93 confidence that this is a stranger"*.

#### Snapshot evidence

`AuthService` saves the captured frame to disk and records the **filename** — not
an absolute path — in `face_log.snapshot_path`. Files live under
`data/snapshots/`; the dashboard serves them at `/snapshots/<filename>`.

Storing a bare filename is deliberate. The database gets copied between machines;
`D:\253\DADN\YoloHome-AIoT\...` is not portable, a filename is.

The capture happens **before** `recognize()` runs, so a crash inside the
recogniser still leaves photographic evidence of who was at the door.

Saving is **fail-soft**, for the same reason as logging (see below): any error
goes to `error_log` and the authorisation decision proceeds unchanged. A full
disk must not open the door, and must not close it either. `NULL` therefore means
one of two things — the row predates the feature, or saving failed. Check
`error_log` with `module='face'` to tell them apart.

Combined with the `person_name` rule above, a denied stranger produces a complete
audit row: identity `unknown`, the confidence at which they were judged a
stranger, and their photograph.

> **These are biometric images of real people.** `data/snapshots/` is gitignored
> and must stay that way. Git retains committed files permanently, even after
> deletion. Verify with `git status --short data/` before committing.

### Two things you MUST know

**1. `execute_authorized_command` is not a security gate.** It runs the hardware
action and returns a bool — it does **not** re-check `face_auth`. The
`face_auth=True` flag only *routes* to auth; it blocks nothing. Therefore
**`AuthService` is the sole enforcement point at execution time.**

The function is called in exactly **two places** system-wide:

- `AuthService.authorize_and_execute()` — after `authorized=True`
- `RuleObserver.update()` — only for commands that do **not** require face auth,
  with its own guard

**2. Logging failures must not change the security decision.** If the database is
locked or the disk is full, the door must still open for someone who
authenticated correctly, and must still stay shut for a stranger. Every logging
call inside `AuthService` is wrapped in `try/except`, and the same rule extends
to snapshot saving.

### A note for callers outside the console

`AuthService` closes the `command_log` row it was handed. A caller that
implements its own auth flow instead of delegating will leave that row stuck at
`waiting_auth` forever, with `latency_ms` empty — and dashboard statistics will
count it as a failure.

`web_dashboard/app.py` therefore delegates to
`orchestrator.handle_auth_required()` rather than reimplementing the flow. Any
future shell — a mobile client, a REST API — must do the same. Note the method
is public: an earlier version of the dashboard called the underscored internal
version, which is exactly the coupling this contract exists to prevent.

---

## Contract C — LLM → Hardware

### Interface

```python
class HardwareModule:
    def execute_command(self, command: dict) -> dict: ...
    def read_sensors(self) -> dict: ...
    def poll_sensors(self) -> dict: ...
```

`execute_command` always returns `{"status": "success" | "error", ...}`.

For `action="get_status"` it **must** also include `"state"`:
`"on"` | `"off"` | `"open"` | `"closed"`. Without it the user always hears
*"trạng thái không xác định"* — `contracts.check_status_result()` logs an ERROR
so this cannot happen silently.

### The four sensor keys are a contract

```python
{"temperature": ..., "humidity": ..., "light": ..., "motion": ...}
```

`RuleService` matches them against `condition["sensor"]`. Renaming a key makes
**every** automation rule stop firing — no error, no log, no failing test.

### Two modes

```python
HardwareModule(mode="simulation" | "real")
```

- **simulation** — state kept in RAM, no network, sensors return fixed values.
  The entire test suite runs in this mode.
- **real** — subscribes to Yolo:Bit sensor feeds over MQTT and publishes commands.

Selected via `HARDWARE_MODE` in `.env`.

> The old `serial_port` parameter has been **removed**. The system uses no serial
> port anywhere — all communication goes through Adafruit IO MQTT. Keeping that
> name made readers believe a serial path existed.

### Adafruit IO feeds

Sensors (Yolo:Bit publishes → backend subscribes):

```
home-temperature   home-humidity   home-light   home-motion
```

Commands (backend publishes → Yolo:Bit subscribes):

| Feed | Payload | Device |
|---|---|---|
| `home-light-living` | `"1"` / `"0"` | living room light |
| `home-light-bed` | `"1"` / `"0"` | bedroom light |
| `home-fan-living` | `"0"`–`"100"` | living room fan |
| `home-fan-bed` | `"0"`–`"100"` | bedroom fan |
| `home-door` | `"open"` / `"close"` | main door |

**ONE FEED PER ROOM.** The previous feed map was keyed by `(device, action)` with
no `room`, so "turn on the bedroom light" and "turn on the living room light"
published to the same feed — both lights came on, and the entire multi-room
capability of the system could not be realised in hardware.

**9 feeds total.** The free tier allows 10, leaving exactly one slot — adding a
room to `device_registry.json` will require revisiting `COMMAND_FEED_MAP`.

> Naming note: `home-light` is the ambient **light sensor**, while
> `home-light-living` is the living room **lamp** command. Different things
> despite similar names.

### Three behaviours that must be preserved

**Subscribe inside `on_connect`.** `loop_start()` reconnects automatically after
a network drop, but paho does **not** re-subscribe. Subscribing once after
`connect()` means a three-second Wi-Fi drop stops all sensor messages from then
on — no error, no log, cache frozen at stale values forever.

**Real mode starts with an EMPTY cache.** Do not seed it with simulated values,
or the rule *"if temperature above 25 then turn on the fan"* fires at boot on
data that never came from a sensor. `read_sensors()` returns only sensors that
have actually reported and are newer than `SENSOR_STALE_AFTER_SECONDS`
(default 60).

**An unmapped command returns `error`.** It must not silently do nothing and
report `success` — the user would hear "bedroom light turned on" while no signal
ever left the machine.

---

## Summary

| Seam | Module's obligation | LLM side (done) |
|---|---|---|
| STT → LLM | `transcribe(bytes) -> str`, Vietnamese **with diacritics** | `parse_and_validate()`, contract-checked at boot |
| LLM → Auth | `recognize(frame) -> {person_name, confidence}` | emits `next_step="auth_required"` with enforced `command` + `command_id` |
| LLM → Hardware | `execute_command(cmd) -> {status, state?}`, keep the 4 sensor keys | routes via `CommandService`, checks `state` at runtime |

See also [`LLM-Command-Strategy-Overview.md`](LLM-Command-Strategy-Overview.md)
for the full pipeline and the `next_step` routing table.

---

## Reference — how `main.py` wires it together

Reference only, **not prescriptive**. Loop structure, threading and shutdown are
the System owner's call.

```python
# --- Construction, driven by .env ---
self.hardware_module = HardwareModule(mode=settings.HARDWARE_MODE)
self.stt_engine = self._build_stt()          # None if unavailable
self.face_module = self._build_face()        # None if unavailable

# auth_service is a PROPERTY derived from face_module, not stored.
# Building it once in __init__ would freeze the reference: reassigning
# face_module afterwards would leave AuthService authenticating with the old
# recognizer, silently.

# --- (1) Observer wiring. MUST run, or automation rules die SILENTLY.
#         No error, no log, tests still pass; rules simply never fire.
self.hardware_module.attach(RuleObserver(rule_service, command_service))

# --- Loop ---
while True:
    self.hardware_module.poll_sensors()      # (2) heartbeat that drives the rules

    transcript = self.stt_engine.transcribe(audio)      # (3a) Contract A
    result = command_service.handle_transcript(transcript, session_id=session_id)

    if result["next_step"] == "auth_required":
        frame = self.capture_frame()                     # (3b) Contract B
        outcome = self.auth_service.authorize_and_execute(result, frame=frame)
        say(outcome["response"])
    else:
        say(result["response"])
```

The numbered points map to the contracts above: **(1)+(2)** observer + poll loop
(the biggest silent-failure risk), **(3a)** Contract A, **(3b)** Contract B.

`_build_stt()` and `_build_face()` **fail soft**: a missing library, model or
webcam still lets the gateway boot, with only that feature disabled and the
reason printed. Both catch `(Exception, SystemExit)` — `face_recognition` calls
`quit()` when its model package is missing, and `SystemExit` does not inherit
from `Exception`, so a plain `except Exception` lets it through and the entire
gateway exits mid-boot.

`_build_stt()` also branches on `STT_ENGINE`, importing the CTranslate2 engine
lazily and inside the same `(Exception, SystemExit)` guard — `ctranslate2` and
`av` are C++ extensions and can fail at import in the same unrecoverable way.