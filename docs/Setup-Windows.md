# Setup on Windows

This document records **fourteen traps** actually hit while setting up and running
the full environment (LLM + STT + Face Recognition + web dashboard) on Windows 11
with Python 3.14.

Read it before installing. Total time lost to them: about a day.

Traps 0–10 are installation problems. Traps 11–14 were hit later, while the
system was already running, and they share a different character: **the code was
correct on disk but was not the code being executed**.

---

## 0. The project path MUST be pure ASCII

**This is the most expensive trap, and its symptom points entirely the wrong way.**

```
D:\253\ĐAĐN\YoloHome-AIoT      ← BROKEN
D:\253\DADN\YoloHome-AIoT      ← CORRECT
```

`dlib` and `OpenCV` are C++ libraries that open files through the ANSI system
API. The character `Đ` (U+0110) does not exist in the cp1252 code page, so they
**cannot open the file** even though it is in the right place with the right size.

The error message never mentions the path:

```
RuntimeError: Unable to open D:\...\shape_predictor_68_face_landmarks.dat
```

It is very easy to assume the file is corrupt and re-download it several times
for nothing.

How to confirm:

```powershell
mkdir C:\dlibtest -Force
copy "venv\Lib\site-packages\face_recognition_models\models\shape_predictor_68_face_landmarks.dat" C:\dlibtest\
python -c "import dlib; dlib.shape_predictor(r'C:\dlibtest\shape_predictor_68_face_landmarks.dat'); print('opened')"
```

Opens from `C:\` but not from the project folder → it is a path problem.

**Update 2026-08-22 — the predicted second occurrence has arrived.** This section
used to warn that `cv2.imwrite()` has the same limitation and that it would
surface "once face images are saved to `data/`". Face Auth now does exactly that:
`face_module.save_snapshot()` writes a JPEG per scan to `data/snapshots/`.

It works, because the project path is ASCII. Anyone who ignores this section and
uses a Vietnamese folder name will find that authentication succeeds, the door
opens, and `face_log.snapshot_path` is `NULL` on every row — because
`cv2.imwrite()` returns `False` rather than raising, and `save_snapshot()` is
deliberately fail-soft. The only trace is a line in `error_log` with
`module='face'`.

After renaming the folder you **must recreate the venv** — a venv hard-codes
absolute paths internally.

---

## 1. numpy must be decided BEFORE any other scientific package

`requirements.txt` used to pin `numpy==1.24.3`. That version has no wheel for
Python 3.14, so pip builds it from source with MinGW and produces a numpy that
warns about itself:

```
Numpy built with MINGW-W64 on Windows 64 bits is experimental...
CRASHES ARE TO BE EXPECTED - PLEASE REPORT THEM TO NUMPY DEVELOPERS
```

Followed by a stream of `RuntimeWarning: invalid value encountered in exp2` at
import time — that build computes floating-point limits incorrectly.

**Do not ignore this.** `dlib`, `face_recognition` and `scikit-learn` all work
heavily with numpy arrays; a broken numpy causes random, hard-to-reproduce errors
that typically show up during a demo.

---

## 2. Upgrading numpy after installing other packages → ABI mismatch

If `opencv-python` was installed while numpy was still 1.x and numpy is then
upgraded to 2.x:

```
AttributeError: _ARRAY_API not found
ImportError: numpy.core.multiarray failed to import
```

The compiled package still links against the old ABI. Fix:

```powershell
pip install --force-reinstall --no-cache-dir opencv-python
```

`--no-cache-dir` is required, otherwise pip reinstalls the same old build from
cache.

Other likely candidates: `dlib-bin`, `scikit-learn`, `soundfile`, `torch`,
and — since the STT work — `ctranslate2`, `onnxruntime` and `av`, which are all
C++ extensions linking against the numpy C API.

**How to avoid it:** install numpy 2.x from the start, or install everything in a
SINGLE command so pip resolves dependencies once against a consistent target.

---

## 3. Old `scikit-learn` blocks numpy 2

```
scikit-learn 1.4.1.post1 requires numpy<2.0,>=1.19.5, but you have numpy 2.5.1
```

The chain of constraints:

```
Python 3.14  →  no wheel for numpy 1.x  →  numpy 2.x required
             →  scikit-learn >= 1.5 required
             →  face_model.pkl must load under scikit-learn >= 1.5
```

**What to ask the person who trains the model:** the versions used in the
notebook.

```python
import sklearn, numpy, sys
print(sklearn.__version__, numpy.__version__, sys.version)
```

If it was trained with sklearn < 1.5, upgrade in Colab and export a new `.pkl`.
sklearn pickles usually load across minor versions with an
`InconsistentVersionWarning`, but "usually" is not "always" — and when they do
break, they break in the worst way: **unpickling succeeds but predictions are
wrong**.

Check a model file with:

```powershell
python -W error::UserWarning -c "import pickle; m = pickle.load(open('models/face_model.pkl','rb')); print(type(m), m.classes_)"
```

`-W error::UserWarning` turns the version-mismatch warning into an error so it
cannot slip past silently.

---

## 4. `face_recognition` pulls in the source build of `dlib`

The package declares a dependency on `dlib>=19.7`. `dlib-bin` provides the same
`dlib` module at import time, but to pip it is a **different package name** — so
pip still downloads and builds `dlib` from source, which needs CMake and Visual
Studio Build Tools, takes 15–20 minutes and often fails halfway.

Symptom: the screen shows `Building wheel for dlib (setup.py)` and then sits
there.

```powershell
pip install dlib-bin
pip install face_recognition --no-deps
```

---

## 5. `face_recognition_models` needs `pkg_resources`

```
ModuleNotFoundError: No module named 'pkg_resources'
```

`pkg_resources` was removed from `setuptools` in version 81, and `torch` pulls in
setuptools 83.

```powershell
pip install "setuptools<81"
```

**The misleading symptom:** the library wraps the import in `except Exception`
and prints a suggestion that is simply untrue:

```
Please install `face_recognition_models` with this command before using `face_recognition`:
pip install git+https://github.com/ageitgey/face_recognition_models
```

The package is already installed. Reinstalling it any number of times changes
nothing, because the real cause was swallowed. Run
`python -c "import face_recognition_models"` directly to expose it.

> This constraint is easy to lose later. Installing anything new into a working
> venv can pull setuptools forward again — the faster-whisper install offered
> setuptools 84. Verify with
> `python -c "import face_recognition, sklearn, cv2; print('face stack ok')"`
> after any dependency change, because the STT work will not exercise this path.

---

## 6. `face_recognition_models` may be missing its `.dat` files

The package carries ~130 MB of model data. If it is built under a newer
setuptools the data files can be dropped, leaving only the code:

```
RuntimeError: Unable to open ...\shape_predictor_68_face_landmarks.dat
```

Check:

```powershell
dir venv\Lib\site-packages\face_recognition_models\models
```

Four files must be present:

| File | Size |
|---|---|
| `shape_predictor_68_face_landmarks.dat` | ~99 MB |
| `dlib_face_recognition_resnet_model_v1.dat` | ~22 MB |
| `shape_predictor_5_face_landmarks.dat` | ~9 MB |
| `mmod_human_face_detector.dat` | ~713 KB |

If any are missing, download them manually:

```powershell
$dir = "venv\Lib\site-packages\face_recognition_models\models"
mkdir $dir -Force
$base = "https://github.com/ageitgey/face_recognition_models/raw/master/face_recognition_models/models"

@(
  "shape_predictor_68_face_landmarks.dat",
  "shape_predictor_5_face_landmarks.dat",
  "dlib_face_recognition_resnet_model_v1.dat",
  "mmod_human_face_detector.dat"
) | ForEach-Object {
    Invoke-WebRequest "$base/$_" -OutFile "$dir\$_"
}
```

A file only a few KB in size means an HTML page was downloaded instead.

---

## 7. `adafruit-io` cannot be installed on Python 3.12+

```
ModuleNotFoundError: No module named 'distutils'
OSError: Could not build the egg.
```

The package uses `ez_setup.py`, a bootstrap mechanism from around 2014: it
downloads `setuptools 4.0.1`, which needs `distutils` — removed from Python 3.12.

**Not fixable.** But not needed either: the package only served
`publish_to_adafruit()` in `hardware_module.py`, and that call site was already
wrapped in `try/except ImportError`, so its absence never crashed anything.

**Resolved:** the package has been removed from `requirements.txt`.
`publish_to_adafruit()` now uses the existing MQTT client (`paho-mqtt`) instead of
REST — which both drops the dependency and eliminates the feed echo bug.

---

## 8. `ffmpeg` cannot be installed with pip

STT needs it to normalise audio. Without it, `transcribe()` returns an empty
string **silently** — it looks like the model heard nothing. This applies to
**both** STT engines; they share `normalize_audio_bytes()`.

```powershell
winget install Gyan.FFmpeg
```

After installing you **must open a new terminal** (for the updated PATH). With
the VS Code integrated terminal you may need to close VS Code entirely.

To avoid restarting:

```powershell
$env:Path += ";$env:LOCALAPPDATA\Microsoft\WinGet\Links"
```

Verify with `ffmpeg -version`.

---

## 9. `SystemExit` slips past `except Exception`

`face_recognition` calls `quit()` when its model package is missing. `quit()`
raises `SystemExit`, which inherits from `BaseException`, **not** from
`Exception`.

This means the entire fail-soft design can be defeated by a single `quit()` call
in a third-party library: set `USE_MOCK_FACE=false` without the package present
and the gateway **exits mid-boot**.

Fixed in `system_core/main.py`:

```python
except (Exception, SystemExit) as exc:
```

Not `except BaseException` — that would also swallow `KeyboardInterrupt` and
Ctrl+C would stop working.

The same guard now wraps the `faster_whisper` import in `_build_stt()`, for the
same reason: `ctranslate2` and `av` are C++ extensions and can fail at import in
ways Python cannot recover from.

---

## 10. Old `transformers` KILLS THE PROCESS with no traceback

The worst symptom of all: the program **exits silently** right after the model
finishes downloading. No error, no traceback, no exit code — the terminal simply
returns to the prompt.

```
INFO:yolohome.stt:Đang tải model PhoWhisper: vinai/PhoWhisper-base (device=cpu) ...
(venv) PS D:\253\DADN\YoloHome-AIoT>
```

Cause: `transformers==4.38.0` ships with `tokenizers==0.15.2`, a library compiled
in **Rust**. That version was released in February 2024, built before numpy 2 and
Python 3.14 existed, so it crashes at the native layer.

**`except Exception` cannot catch it** because Python never regains control. Same
family as trap 9 but worse: `SystemExit` can at least be caught if named
explicitly.

How to isolate it — load each component separately:

```powershell
python -c "from transformers import WhisperFeatureExtractor; WhisperFeatureExtractor.from_pretrained('vinai/PhoWhisper-base'); print('OK')"
python -c "from transformers import WhisperTokenizerFast; WhisperTokenizerFast.from_pretrained('vinai/PhoWhisper-base'); print('OK')"
```

The feature extractor loads (pure Python + numpy) while the tokenizer dies
silently → that identifies the culprit.

```powershell
pip install -U transformers tokenizers
```

Upgrading to `transformers 5.14.1` + `tokenizers 0.22.2` resolves it. Re-run
`pytest` afterwards — that is a major version jump.

**Status of the `.safetensors` workaround.** `stt_module._get_pipeline()` still
tries `pytorch_model.bin` first and falls back to `.safetensors`, because the
crash originally happened on the safetensors path. The fine-tuned checkpoint was
saved by transformers 5.x and contains **only** `model.safetensors`, so every run
now takes the fallback branch. Re-verified 2026-08-22 on Windows / Python 3.14 /
CPU: 245 tensors load and transcription completes normally. The ordering is kept
because machines using the original PhoWhisper (which ships a `.bin`) still
benefit from the safer path.

---

## 11. Duplicate pins in `requirements.txt` make pip refuse everything

**Symptom**

```
ERROR: Could not find a version that satisfies the requirement ctranslate2==4.1.0
       (from versions: 4.6.1, 4.6.2, ..., 4.8.1)
```

or, with two `==` pins for the same package,

```
ERROR: ResolutionImpossible
```

Nothing installs at all — not even the packages that were fine.

**Cause**

Two separate causes that look similar:

1. **A pin with no wheel for this Python.** The `(from versions: ...)` list *is*
   the answer: PyPI has nothing older than 4.6.1 built for cp314. C++ extensions
   like `ctranslate2`, `onnxruntime` and `av` only publish wheels for a narrow
   range of Python versions.
2. **The same package pinned twice with different versions.** This happens when
   a merge appends a new dependency block instead of editing the existing one.
   We ended up with six conflicting pairs — `numpy` 2.5.1 vs 1.26.4, `torch`
   2.13.0 vs 2.0.0, `transformers` 5.14.1 vs 4.38.0, and three more — several of
   which directly contradicted warnings written in the file's own header.

**Fix**

Do not guess replacement versions one at a time; you will hit the same error once
per package. Resolve them all in one throwaway environment:

```powershell
python -m venv .venv-probe
.venv-probe\Scripts\activate
pip install faster-whisper jiwer pandas    # NO --no-deps here: we want the tree
pip freeze
deactivate
```

Copy the real numbers out of `pip freeze`, then delete `.venv-probe`.

**Two things to check before pasting them in.** The probe environment resolves
*without* the packages already pinned in the project, so its answers are not
automatically correct:

- Packages already pinned keep their existing version unless a test proves
  otherwise. The probe suggested `setuptools 84.0.0`, which would have broken
  Face Auth (trap 5), and newer `tokenizers` / `huggingface_hub` than
  `transformers 5.14.1` expects.
- `requirements.txt` installs with `--no-deps`, so every transitive dependency
  must be listed explicitly. Missing ones do not fail at install time — they fail
  at `import`, later, somewhere else.

Verify with a dry run before committing:

```powershell
pip install --no-deps --dry-run -r requirements.txt
```

---

## 12. Running a file by path instead of `python -m`

**Symptom**

```
ModuleNotFoundError: No module named 'config'
```

Hit on both `python web_dashboard/app.py` and `python system_core/main.py`.

**Cause**

Running a file by path puts *that file's own directory* on `sys.path` — here
`web_dashboard\`, not the repository root. `from config import settings` then
finds nothing. Python 3.11+ does not add the working directory either, and the
project has no `setup.py`, so there is no installed-package fallback.

**Fix**

Always run from the repository root using module syntax:

```powershell
python -m web_dashboard.app
python -m system_core.main
python -m database.init_db
python -m modules.speech_recognition.evaluate_model --engine phowhisper
```

Both READMEs documented the file-path form. They were wrong, and have been
corrected.

**The same mistake one level down, in imports.** `from stt_module import ...`
inside `modules/speech_recognition/` bypasses the package. `stt_module`'s own
`from system_core.strategies import STTStrategy` then fails, falls into its
`except ImportError` branch, and defines an ABC with the **same name but a
different identity**. Tools run fine; `isinstance(stt, STTStrategy)` returns
`False` in the real system, and the mismatch only surfaces at integration time.
Use `from .stt_module import ...` or the full package path.

---

## 13. Flask does not reload edited `.py` files

**Symptom**

You edit `services/auth_service.py`, reload the page, and the old behaviour
persists. In our case the server kept reporting

```
save_snapshot failed: cannot import name 'save_snapshot' from
'modules.face_recognition.face_module'
```

while the function was plainly in the file, and a fresh `python -c` in another
terminal imported it without complaint. Roughly fifteen minutes went into
suspecting the wrong things — a typo, a stale `__pycache__`, a second copy of the
module — before restarting the server fixed it instantly.

**Cause**

`app.run(debug=False)` has no auto-reloader. The running server holds the module
object loaded at boot in `sys.modules`. A lazy `from ... import x` inside a
function does **not** re-read the file; it reads that cached object, which has no
such attribute.

The lazy-import style used throughout this project (see `Design-Principles.md`
§5) makes this present as an *import* error rather than a staleness problem,
which is what costs the time.

**Fix**

`Ctrl+C` and restart the server after editing any `.py`. Templates (`.html`) do
not need it — Jinja re-reads them per request, so a browser refresh is enough.

Do **not** enable `debug=True` to work around this. The reloader runs
`MainOrchestrator()` twice, giving two sensor threads and two camera handles.

**Quick discriminator.** Run the same import in a fresh `python -c`. Works there
but not in the server → it is staleness. Restart.

---

## 14. `ct2-transformers-converter` does not copy the tokenizer

**Symptom**

None, at first. faster-whisper loads, transcribes, and returns plausible
Vietnamese. Quality is just... worse. The only evidence is one line among the
INFO stream:

```
INFO:httpx:HTTP Request: HEAD https://huggingface.co/openai/whisper-tiny/resolve/main/tokenizer.json
```

**Cause**

The converter transforms the *weights* into `model.bin` and nothing else. When
`tokenizer.json` is absent from the output directory, faster-whisper silently
downloads `openai/whisper-tiny`'s tokenizer instead — a different model, a
different vocabulary — and carries on.

**Fix**

```powershell
ct2-transformers-converter `
    --model ./modules/speech_recognition/finetune_final `
    --output_dir ./modules/speech_recognition/finetune_final_ct2 `
    --quantization int8 `
    --copy_files tokenizer.json processor_config.json
```

For an already-converted directory, copy the file across and verify:

```powershell
copy modules\speech_recognition\finetune_final\tokenizer.json modules\speech_recognition\finetune_final_ct2\
Get-FileHash modules\speech_recognition\finetune_final\tokenizer.json
Get-FileHash modules\speech_recognition\finetune_final_ct2\tokenizer.json
```

The hashes must match. Weights and tokenizer are a matched pair — a mismatched
tokenizer maps the model's token IDs back to the wrong characters.

**Note on what this did *not* fix.** We first blamed this for faster-whisper
dropping leading syllables. Fixing the tokenizer changed the WER by exactly
nothing. The real cause is elsewhere — most likely int8 quantisation, still
unverified. Correlation is not causation, even when the correlated bug is real
and worth fixing on its own terms.

---

## Verification after installing

```powershell
python -c "import numpy, cv2, dlib, face_recognition, sklearn, torch; print('all ok')"
python -c "import faster_whisper, jiwer, pandas; print('stt eval ok')"
ffmpeg -version
python -m pytest tests\ -q
```

The remaining `pkg_resources is deprecated` warning is harmless.

The test suite must be **fully green**. This is where you confirm that numpy 2 +
torch + sklearn did not break the LLM side — which is pure Python and should be
unaffected, but you need to see it rather than assume it.

Try each module standalone before enabling it in the system:

```powershell
python -m tests.modules.stt.test_stt --duration 4
python -m tests.modules.face.test_face
```

Only once those work, enable them in `.env`:

```ini
USE_MOCK_FACE=false
USE_MOCK_STT=false
CAMERA_INDEX=0

# Leave blank: settings.py picks the fine-tuned checkpoint when its weights are
# present on disk, and falls back to vinai/PhoWhisper-base when they are not.
PHOWHISPER_MODEL=
STT_ENGINE=phowhisper
```

> The flag names changed: `ENABLE_FACE_AUTH` / `ENABLE_STT` no longer exist. They
> are now `USE_MOCK_FACE` / `USE_MOCK_STT` with the **opposite** meaning — set
> them to `false` to use the real modules. Verified 2026-08-22: no code reads the
> old names anywhere. If they are still in your `.env.example`, delete them —
> configuration that looks effective but is not costs hours.

**Model weights are not in the repository.** `.gitignore` excludes
`*.safetensors`, `*.bin` and `*.pkl`, so after a fresh clone
`modules/speech_recognition/finetune_final/` holds only JSON and `models/` holds
only `.gitkeep`. Neither absence crashes anything:

- Missing STT weights → the system quietly uses base `vinai/PhoWhisper-base`.
  `check_config()` warns at startup; `/api/status` reports the model in use.
- Missing `face_model.pkl` → `face_module` is `None`, and every command requiring
  authentication is refused with *"hệ thống xác thực khuôn mặt chưa sẵn sàng"*.

See `models/README.md` and `.env.example` for where to obtain them.

If the camera doesn't come up, try `CAMERA_INDEX=1` — OBS, Zoom and DroidCam
often occupy index 0.

---

## The common thread

**Traps 1–4 and 11 share one root cause:** `requirements.txt` pinned versions from
the Python 3.10 era, running on Python 3.14.

Pinning versions exists to make an environment reproducible. But a pin left
untouched becomes the opposite: a list of versions with no remaining wheels,
forcing pip to build from source, each build failing in its own way. Trap 11 adds
the sequel — once a stale file is being repaired by several people at once, the
repairs themselves collide.

**Traps 9 and 10 mark the limit of fail-soft design.** They are real-world
illustrations of `Design-Principles.md` §3:

> Fail-soft only works while the error is still at the Python layer. A call into
> a C++ or Rust library can kill the process in a way nothing can catch.

All four cases encountered came from **third-party libraries**, not the team's own
code: `quit()` raising `SystemExit`, `face_recognition` printing a false
suggestion, dlib unable to open a Vietnamese path, and `tokenizers` dying
silently. The only defences are loading in a subprocess, or avoiding the failing
path entirely.

**Traps 12–14 are a different species, and arguably the most expensive per
minute of debugging.** In each, the code on disk is correct — but it is not the
code being executed, or not the whole of it:

- **12** — the interpreter was started in a way that put the wrong directory on
  `sys.path`
- **13** — a running process was serving a module it had cached at boot
- **14** — a tool copied part of a model and silently substituted the rest

None of the three reports an error that points at the real cause. Trap 14 reports
nothing at all. The general defence:

> When behaviour contradicts what the file plainly says, first establish that the
> file you are reading is the file being run.

And its corollary, learned the hard way in trap 14: when a plausible culprit is
found and fixed, **re-measure before believing it**. A real bug sitting next to a
symptom is not the same as the cause of that symptom.