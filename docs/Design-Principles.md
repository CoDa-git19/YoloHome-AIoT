# Design Principles

This document records the **eight principles** that govern how YoloHome-AIoT is
written. They are not style conventions. Each one came out of a real bug during
development, and each one has a test locking it in place.

Use this as a checklist when reviewing code or adding a module.

---

## 1. The model interprets. The server decides.

A probabilistic component — the LLM or the face recognition model — is allowed to
**report what it observed**. But whatever turns observation into **authority** is
always server-side configuration.

**Applied in five places, with the same reasoning:**

| # | Component reports | Server decides | Enforced in |
|---|---|---|---|
| 1 | LLM returns `face_auth` | `device_capabilities.json` | `validator.enforce_policy()` |
| 2 | Face model returns `confidence` | `FACE_AUTH_THRESHOLD` | `AuthService` |
| 3 | LLM writes the clarification question | `device_registry.json` decides **the set of options** | `missing_slot_question()` |
| 4 | LLM fills in slot values | the user's utterance decides **whether there is evidence** | `ground_slots()` |
| 5 | LLM classifies an unknown room | the registry decides which rooms exist | `detect_unsupported_room()` |

`enforce_policy()` overrides in **both directions**: it forces `True` when policy
requires it but the LLM returned `false`, and forces `False` when policy does not
require it but the LLM returned `true`.

`AuthService` **deliberately ignores** an `authorized` flag if the face module
returns one. Trusting it would let a swapped-in model grant itself permission.

Item 4 came from a case observed on the console:

```
Bot : Nhà bếp chưa được đăng ký. Bạn có muốn gửi yêu cầu không?
User: có
Bot : Đã bật đèn phòng ngủ.
```

The word `"có"` ("yes") mentions no room at all. The model picked `bedroom` on
its own, the server saw a valid combination and let it run — and **hardware was
actually actuated** on a value nobody uttered.

Item 3 came from the mirror image: the bot suggested *"bedroom, living room,
main door"* when the user asked about a light — but the main door has no light.
The user followed the bot's own advice and was still rejected.

**Locked in by:** `test_security_policy.py`, `test_auth_service.py`,
`test_slot_suggestions.py`, `test_slot_grounding.py`, `test_unsupported_room.py`

---

## 2. Fail closed

Missing module, camera failure, model exception, dead network, locked database —
all of these lead to **DENIAL**. There is no branch that means "not sure, so let
it through".

The cost of a false rejection is that the user scans again. The cost of a false
acceptance is an open front door.

**Worth remembering:** the `face_auth=True` flag only **routes** a command to
`AuthService`; it blocks nothing by itself.
`CommandService.execute_authorized_command()` does **not** re-check it either.
That makes `AuthService` the **sole enforcement point at execution time**.

Direct consequence: `execute_authorized_command()` is called in exactly **two
places** system-wide — `AuthService.authorize_and_execute()` (after
authentication) and `RuleObserver.update()` (only for commands that do **not**
require face auth).

**Fail-closed can be defeated from outside.** `face_recognition` calls `quit()`
when its model package is missing; `quit()` raises `SystemExit`, which inherits
from `BaseException`, not from `Exception`. A plain `except Exception` lets it
through and the entire gateway exits mid-boot. `_build_face()` and `_build_stt()`
therefore catch `(Exception, SystemExit)` — but **not** `BaseException`, so
Ctrl+C still stops the program.

**And fail-closed has a hard limit.** `tokenizers` (a Rust library) crashes at
the native layer when run in the wrong environment: the process dies without
Python ever regaining control, so **no** `except` block can catch it. Fail-soft
only works while the error is still at the Python layer. See
`Setup-Windows.md` §10.

**Locked in by:** every test in `test_auth_service.py` asserts on the number of
calls to `execute_authorized_command()`, not just on the returned string. A test
that only checks the reply text would stay green even if the system opened the
door for a stranger.

---

## 3. If it breaks, it must make noise

The worst bug is not the one that crashes the program. The worst bug is the one
that makes the system **report success while doing nothing** — or **report the
wrong cause**.

**Catalogue of silent failures encountered in this project:**

| Bug | Symptom | How it is now caught |
|---|---|---|
| Observer never attached | Rule saved to DB, "created" reported, never fires | explicit attach + `test_observer_pattern.py` |
| `use_mock=True` by default | Bypasses Gemini entirely, output still looks right | `[Config] USE_MOCK_LLM` printed at boot |
| Missing ffmpeg | `transcribe()` returns `""`, looks like the model heard nothing | `_check_ffmpeg()` runs first |
| Fallback ABC in `except ImportError` | `isinstance()` returns `False` while the manual tool works fine | `_check_strategy_identity()` |
| Renamed sensor key | Every automation rule stops firing — no error, no log | the 4 keys documented as a **contract** |
| Missing `"state"` on `get_status` | User always hears "trạng thái không xác định" | `contracts.check_status_result()` |
| Truthy `Unknown` label | A stranger **opens the door** | `_as_identity()` + `REJECT_NAMES`, two layers |
| **Reply written by the LLM before validation** | Log says `failed`, user hears "Đã bật đèn" | `failure_response()` |
| **Room name swallowing the device name** | `"bật đèn cửa chính"` becomes an **open-door** command | `detect_device_excluding_room()` |
| **Library printing a false suggestion** | Tells you to install a package that is already installed | run the `import` directly to expose the real error |
| **`tokenizers` crashing at the Rust layer** | Process exits silently — no traceback, no exit code | upgrade `transformers`; isolate by loading each component |
| **CLI flag silently overriding `.env`** | `USE_MOCK_STT=false` has no effect, log still says MOCK | CLI flag returns `None` so `settings` decides |

The last five rows are recent. Two of them come from **outside the team's code**:
this principle does not stop at the project boundary — a library that catches
`Exception` and prints a misleading message costs just as many hours as a
self-inflicted bug. See `Setup-Windows.md` §5 and §10.

The last row is a case hit while enabling STT: `main()` contained
`use_mock_stt=True if (args.mock_stt or not args.voice) else False`, so running
without `--voice` forced mock regardless of `.env`. The system ran smoothly, the
replies were sensible, no errors appeared — but it was doing something entirely
different from what the user believed. The only thing that gave it away was the
`[Config]` line printed at boot.

The rule: **if something can break without anyone noticing, there must be
somewhere it shouts** — an ERROR log, a `[Config]` line at boot, or a failing
test.

---

## 4. One fact, one source of truth

Rooms, devices, actions, sensors, questions, replies — each is defined **exactly
once**.

**This failure mode recurred three times in a single round of fixes:**

| # | Duplicated in | Consequence |
|---|---|---|
| 1 | Clarification question copied into `validate_mock_slots` | mock asked differently from Gemini |
| 2 | Success reply copied in 3 places | mock returned a generic phrase for both on and off |
| 3 | Three separate branches composing clarify | Gemini's first clarify turn lost the option list |

All three were found by **comparing mock against Gemini on the same input**, not
by reading code. `MockLLMStrategy` is therefore not merely a test tool — it is an
**executable specification** of the intended behaviour. Wherever the two engines
diverge, there is a bug.

Same reasoning: `settings.PHOWHISPER_MODEL` reads **the same environment
variable** that `stt_module` already reads, rather than introducing a new name.

**A security consequence, recorded rather than hidden:** write access to
`command_schema.json` and `device_capabilities.json` is equivalent to control
over the entire authentication policy. The risk is **relocated**, not eliminated.

---

## 5. Tests must run offline

The whole test suite runs on a machine with **no** dlib, **no** webcam, **no**
microphone, **no** API key and **no** hardware.

Violating this breaks CI, and blocks team members who are not working on the AI
parts.

**Three techniques that keep it that way:**

1. **Lazy imports.** `cv2` / `face_recognition` / `numpy` are imported **inside
   functions**. In exchange, `_build_face()` preloads them at boot so the first
   recognition doesn't stall and a missing library still surfaces early.
2. **Separating pure logic from I/O logic.** `SvmFaceRecognizer._as_identity()`
   only manipulates strings, so it is testable without a camera or a model file.
3. **Dependency injection.** `AuthService(logging_service=...)` accepts a fake
   logging service, so its tests need no database.

**Warning:** "fixing" this with `pytest.importorskip` is **wrong**. On a machine
missing the library the test **silently skips** and pytest still reports green —
the most important security test disappears without anyone noticing. That is
precisely the failure mode principle 3 forbids.

**Manual tools placed under `tests/`** (their names start with `test_`, so pytest
imports them) must keep every heavy dependency inside `main()` and set
`__test__ = False`.

---

## 6. One resource, one owner

Every system resource has **exactly one** owner responsible for opening and
closing it.

| Resource | Owner | Note |
|---|---|---|
| `cv2.VideoCapture` | `face_module.capture_frame()` | opened on demand, closed after one scan |
| MQTT connection | `HardwareModule` | lazy, shared by both subscribe and publish |
| `LLMStrategy` | `MainOrchestrator` | shared between `CommandService` and the benchmark |

If two places open the webcam, on Windows the second one receives `None` and Face
Auth rejects every command **for no visible reason**.

`capture_frame()` expresses this with the `owns_capture` flag: it closes the
camera only if it opened it. A caller that passes `cap` in is responsible for
closing it.

**The camera is opened on demand, not held for the session.** Holding it locks
the webcam all afternoon, so OBS, Zoom and screen recorders stop working —
exactly during the demo. The trade-off is about one extra second when unlocking
the door, which is acceptable since the user is already standing there waiting to
be scanned.

The same principle applies at the network layer: the system once had **two paths**
to Adafruit IO — MQTT to receive sensors, REST (the `adafruit-io` package) to push
data up. The second was both redundant and harmful: the device publishes
`home-temperature`, the backend subscribes, then republishes to that same feed,
doubling data points against a free tier limited to 30 per minute. Now removed;
`publish_to_adafruit()` uses the existing MQTT client.

---

## 7. Contracts are checked at startup

Interface mismatches must surface **at boot**, not mid-demo.

`system_core/contracts.py` checks the signature of `LLMStrategy` and the presence
of `execute_command()` at construction time. `settings.check_config()` prints
warnings at import — setting `USE_MOCK_FACE=false` with no `face_model.pkl`
present is reported immediately, and setting `USE_MOCK_FACE=true` is also flagged
because the mock lets **anyone** open the door.

The principle extends to **runtime dependencies**, not just internal interfaces.
The `scikit-learn` version used to train `face_model.pkl` is an implicit
contract: pickles load across minor versions with a warning, but when they break
they break in the worst way — **unpickling succeeds but predictions are wrong**.
See `Setup-Windows.md` §3.

---

## 8. Record trust assumptions, don't hide them

Every system has places where it must trust something. The principle is not to
eliminate them — it is to **write them down**.

- `pickle.load()` on `models/face_model.pkl` **executes code** from that file.
  Anyone who can write to it controls the gateway process.
- Write access to `command_schema.json` / `device_capabilities.json` is
  equivalent to control over the authentication policy.
- `FACE_REQUIRE_BLINK=false` lets a printed photo pass authentication.
  `check_config()` warns when this is set.
- Liveness is currently confirmed for the **whole frame**, not bound to a
  specific face: a stranger could hold a printed photo of a member next to their
  own face and blink themselves. Marked as a `TODO` in `recognize()`.
- The project path must be pure ASCII — `dlib` and `OpenCV` are C++ libraries and
  cannot open files through paths containing Vietnamese characters.

A recorded assumption is an engineering decision. An unrecorded assumption is a
vulnerability waiting to happen.

---

## Checklist when adding a module

- [ ] Does the model/LLM only **report**, with the decision made server-side?
- [ ] Does every error path lead to **denial** rather than passage?
- [ ] Is there anything that can break **without anyone noticing**? If so, where
      does it shout?
- [ ] Does this data already exist in a config file?
- [ ] Do the tests run on a machine **without** the heavy libraries?
- [ ] Does every resource (camera, connection, client) have **exactly one** owner?
- [ ] Would an interface mismatch surface **at boot** or during the demo?
- [ ] Is there a trust assumption that hasn't been written down?