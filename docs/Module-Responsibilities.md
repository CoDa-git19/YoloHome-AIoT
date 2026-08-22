# Module Responsibilities

Who owns which file, what each module is allowed to do, and — more importantly —
what it must **not** do.

This document complements `Integration-Contracts.md`. That one describes the
*shape of the data* crossing each integration seam; this one describes the
*boundaries of responsibility*.

---

## Ownership table

| File | Owner | Responsible for | Must NOT |
|---|---|---|---|
| `modules/llm_integration/llm_module.py` | Uyên | transcript → validated JSON command | make security decisions |
| `modules/llm_integration/validator.py` | Uyên | enforce policy server-side | trust the `face_auth` the LLM returned |
| `modules/speech_recognition/stt_module.py` | Ly | `bytes` → Vietnamese transcript with diacritics | call the LLM, lowercase, strip punctuation |
| `modules/speech_recognition/stt_faster_whisper.py` | Ly | same contract, CTranslate2 backend | diverge in behaviour from `stt_module` |
| `modules/speech_recognition/evaluate_model.py` | Ly | measure WER / CER / latency | change runtime behaviour |
| `modules/face_recognition/face_module.py` | Bình | frame → `person_name` + `confidence`; save snapshots | decide `authorized`, **open the camera itself** |
| `services/auth_service.py` | Uyên | compare against threshold, execute, close logs | rebuild the `command` |
| `modules/hardware_gateway/hardware_module.py` | Khang | execute commands, read sensors | rename the four sensor keys |
| `services/command_service.py` | Uyên | pipeline from transcript to execution | re-check `face_auth` |
| `services/rule_service.py` | Uyên | store and evaluate automation rules | run rules that require face auth |
| `system_core/main.py` | Danh | wiring, sensor loop, camera ownership | rebuild the pipeline by hand |
| `database/*` | Khang | persistence | — |
| `web_dashboard/*` | Danh | presentation, delegating back to the orchestrator | rebuild the pipeline, **call private methods** |

---

## The boundaries that matter most

### `execute_authorized_command()` may be called from exactly two places

This function is **not a security gate**. It drives the hardware and returns a
`bool`; it does not check `face_auth`. Calling it anywhere other than the two
places below opens the door to a stranger:

1. `AuthService.authorize_and_execute()` — only after `authorized=True`
2. `RuleObserver.update()` — only for commands that do **not** require face auth,
   with its own guard, because rules fire automatically and nobody is standing at
   the camera to authenticate

### The face module does not own the camera

Contract B assigns frame acquisition to the System owner, not the Face owner.
`MainOrchestrator.capture_frame()` is the single entry point, and it draws from
two sources in order:

1. **`self.camera`** — a pre-opened `VideoCapture`. Grabs exactly one frame and
   returns; it does not wait for anyone to appear. Intended for tests and
   headless contexts. Default `None`.
2. **`self.frame_capturer`** — the face module's scanner, wired up in
   `_build_face()`. Opens the camera itself, waits until it sees a face (plus a
   blink if `FACE_REQUIRE_BLINK` is on), returns a clean frame, then releases the
   camera.

Both `None` → `None` → rejection. Fail closed.

> **Path 2 is the production path.** `capture_frame(cap=None)` opening and
> closing the device per scan is deliberate: it means the webcam is not held open
> while the system is idle, so other applications can use it and Windows will not
> hand back a stale handle.

> **`self.camera` is a trap when liveness is required.** That path grabs a single
> frame, and a blink cannot be detected in one frame. Setting `self.camera` while
> `FACE_REQUIRE_BLINK=true` silently disables anti-spoofing — Face Auth still
> runs, still recognises, and a printed photo passes. Nothing else reports this,
> so `capture_frame()` writes an ERROR to `error_log` when it happens. The
> attribute is named `camera`, which makes it the obvious place to put a
> `VideoCapture`; that is exactly why the trap is easy to fall into.

> **Amendment to Contract B.** The original text said the orchestrator supplies
> **one** frame. That is incompatible with liveness detection: a blink cannot be
> detected in a single frame. The current contract: **the orchestrator owns frame
> acquisition; the face module reads as many frames as it needs.** Ownership is
> unchanged; only the frame count is.

### The dashboard may only call the orchestrator's public API

`web_dashboard/app.py` used to call `orchestrator._handle_auth_required()`. A
leading underscore means "internal, may change at any time" — binding the
dashboard to it invites exactly the kind of drift that `app.py`'s own docstring
recounts.

The orchestrator now exposes `handle_auth_required()`. The dashboard calls that.
If the dashboard needs more from the orchestrator, **add a public method**; do
not reach into the underscored surface.

The wider rule: the dashboard is a *shell*. It does not build pipelines, does not
make security decisions, and does not open cameras. All of that belongs to
`system_core/main.py`.

### The four sensor keys are a contract

```python
{"temperature": ..., "humidity": ..., "light": ..., "motion": ...}
```

`RuleService` matches `condition["sensor"]` against exactly these four names.
Renaming them when wiring real hardware will stop **every** automation rule from
firing — with no error, no log, and no red test.

### `execute_command()` must return `state` for `get_status`

```python
{"status": "success", "state": "on" | "off" | "open" | "closed"}
```

A missing `state` does not crash anything — the user simply gets "could not
determine the state". `contracts.check_status_result()` logs an ERROR so that it
does not pass silently.

### Face Auth snapshots are evidence, not a feature

`AuthService` captures a frame **before** calling `recognize()`, so a crash inside
the recogniser still leaves photographic evidence of who was at the door. The
filename — not an absolute path, because the database gets copied between
machines — goes into `face_log.snapshot_path`.

Saving is **fail-soft**: any error goes to `error_log` and the authorisation
decision proceeds unchanged. A full disk must not open the door, and must not
close it either.

Every scan is recorded, including rejections. That is precisely the row you want
when investigating an incident.

Images live under `data/snapshots/`, which is gitignored. **These are biometric
images of real people and must never be committed** — Git retains them
permanently, even after deletion.

---

## Import rules

**Inside modules with heavy dependencies** (`cv2`, `dlib`, `torch`,
`transformers`, `faster_whisper`):

```python
# At the top of the file: stdlib and config only
import math, pickle
from config.settings import MODELS_DIR

class SvmFaceRecognizer:
    def recognize(self, frame):
        import cv2                 # ← heavy, inside the function
        import face_recognition
```

Rationale and exceptions: `Design-Principles.md` §5.

**Inside manual tools placed in `tests/`** (their names start with `test_`, so
pytest will import them): every heavy dependency must sit inside `main()`, and the
file must set `__test__ = False`.

**Across package boundaries**: always import through the package, never by bare
module name.

```python
from .stt_module import STTStrategy                      # correct
from modules.speech_recognition.stt_module import ...    # correct
from stt_module import STTStrategy                       # WRONG
```

The last form bypasses the package, so `stt_module`'s
`from system_core.strategies import STTStrategy` fails and falls into its
`except ImportError` branch, which defines an ABC with the **same name but a
different identity**. The tool runs fine; `isinstance(stt, STTStrategy)` returns
`False` in the real system. The failure only surfaces at integration time. See
`tests/modules/stt/test_stt.py` for the full account.

---

## Tests: two kinds, do not mix them

| | Automated tests | Manual tools |
|---|---|---|
| Examples | `test_face_module_contract.py`, `test_auth_service.py` | `test_face.py`, `test_stt.py` |
| Requires | nothing | webcam, microphone, model, a person at the machine |
| Has `assert` | yes | no |
| pytest runs it | yes | no (`__test__ = False`) |
| Purpose | lock behaviour in place | tune parameters against a real room |

Manual tools still live under `tests/` so they are easy to find, but they must
import lightly so they do not turn the whole suite red.

---

## Module toggles

Wiring is **fail soft**: a missing library, a missing model, or a missing webcam
still lets the gateway boot. Only that feature switches off, and a
`[Config] ... = OFF (reason)` line explains why. A teammate who cannot get dlib
installed must not be able to break the whole team's system.

`_build_face()` constructs `face_module`, the recogniser, and `frame_capturer`
inside a **single** `try/except`, so the invariant *`auth_service` exists ⟺
`face_module` exists* is guaranteed by the structure of the code rather than by
discipline.

Because `AuthService` needs `command_service`, the face wiring **must** come after
the `--- SERVICES ---` block in `MainOrchestrator.__init__`.

> **Turning a module off does not mean skipping authentication.** The system still
> fails closed: with no face module, `door.open` is refused. The flags decide
> whether a module is **loaded**, not whether a check is **performed**.

> **`ENABLE_FACE_AUTH` and `ENABLE_STT` are dead configuration.** They appear in
> `.env.example` but no code reads them — verified 2026-08-22 across
> `config/settings.py`, `system_core/main.py`, and `web_dashboard/app.py`.
> Use `USE_MOCK_FACE` and `USE_MOCK_STT` instead. Configuration that looks
> effective but is not costs hours of debugging; remove these two lines from
> `.env.example`.

---

## Design patterns: where they are used, and what they solve

| Pattern | Used in | Problem it solves |
|---|---|---|
| Strategy | `LLMStrategy`, `STTStrategy` | swap Gemini ↔ OpenAI ↔ mock, and PhoWhisper ↔ faster-whisper, without touching `CommandService` |
| Observer | `Subject` / `Observer` in `observers.py` | one sensor-reading loop feeding several consumers (rules, logging, cloud) that know nothing about each other |
| Command | `commands.py` | wrap a command as an object with `execute()` / `undo()`, so history can be kept |

Patterns are not decoration for the report. The test is: remove the pattern — what
breaks? Remove Strategy → `CommandService` hard-depends on Gemini and tests must
call the real API. Remove Observer → adding one more consumer of sensor data means
editing `HardwareModule`. Remove Command → undo becomes impossible.

`STTStrategy` earned its keep in practice: adding `FasterWhisperSTT` required no
change to `CommandService`, `AuthService`, or the dashboard — only one branch in
`_build_stt()` and one line in `.env`.