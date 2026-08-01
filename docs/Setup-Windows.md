# Setup on Windows

This document records **ten traps** actually hit while setting up the full
environment (LLM + STT + Face Recognition) on Windows 11 with Python 3.14.

Read it before installing. Total time lost to them: about half a day.

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

**Note for later:** `cv2.imread()` and `cv2.imwrite()` have the same limitation.
The system currently only uses `cv2.VideoCapture(0)` (a camera index, no path) so
it hasn't surfaced — but it will once face images are saved to `data/`, and that
time it will be harder to diagnose because it only breaks when writing an image,
not at import.

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

Other likely candidates: `dlib-bin`, `scikit-learn`, `soundfile`, `torch`.

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
string **silently** — it looks like the model heard nothing.

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

---

## 10. Old `transformers` KILLS THE PROCESS with no traceback

The worst symptom of all ten: the program **exits silently** right after the
model finishes downloading. No error, no traceback, no exit code — the terminal
simply returns to the prompt.

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

---

## Verification after installing

```powershell
python -c "import numpy, cv2, dlib, face_recognition, sklearn, torch; print('all ok')"
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
PHOWHISPER_MODEL=vinai/PhoWhisper-base
```

> The flag names changed: `ENABLE_FACE_AUTH` / `ENABLE_STT` no longer exist. They
> are now `USE_MOCK_FACE` / `USE_MOCK_STT` with the **opposite** meaning — set
> them to `false` to use the real modules.

If the camera doesn't come up, try `CAMERA_INDEX=1` — OBS, Zoom and DroidCam
often occupy index 0.

---

## The common thread

Nine of the ten traps share one root cause: **`requirements.txt` pinned versions
from the Python 3.10 era, running on Python 3.14**.

Pinning versions exists to make an environment reproducible. But a pin left
untouched for years becomes the opposite: a list of versions with no remaining
wheels, forcing pip to build from source, each build failing in its own way.

The two remaining traps — number 9 (`SystemExit`) and number 10 (`tokenizers`
crashing at the Rust layer) — are real-world illustrations of
`Design-Principles.md` §3, and they reveal a genuine limit of the fail-soft
design:

> Fail-soft only works while the error is still at the Python layer. A call into
> a C++ or Rust library can kill the process in a way nothing can catch.

All four cases encountered came from **third-party libraries**, not the team's own
code: `quit()` raising `SystemExit`, `face_recognition` printing a false
suggestion, dlib unable to open a Vietnamese path, and `tokenizers` dying
silently. The only defences are loading in a subprocess, or avoiding the failing
path entirely.