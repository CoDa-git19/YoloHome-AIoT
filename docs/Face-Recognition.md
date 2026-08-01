# Face Recognition Module

This document describes in detail the 2 main phases for building and operating the face authentication module for the YoloHome-AIoT system: the **Model Training Phase** and the **Inference Phase**.

---

## 1. Model Training Phase

This phase focuses on collecting real-world data from household members and training an AI model capable of high-accuracy classification.

### 1.1. Image Dataset Collection
The dataset is built by collecting images from the internet to simulate the identities of household members. You can access the full image dataset and training scripts on Colab via the link below:

**🔗 [Link Google Drive: Face_recognition Dataset & Colab Notebooks](https://drive.google.com/drive/folders/1x1XhBWTxF0KHK5n6sOTO_CTqfcDZ2btS?usp=sharing)**

- **Quantity:** Download approximately **100 images from the Internet** to represent each person (each class).
- **Diversity:** The collected images must cover a wide range of angles and conditions: front-facing, left/right profile, good lighting, low lighting, etc., so the model can learn the best possible features.
- **"Unknown" Group:** Download an additional ~100 images of random people (from the internet) and assign them to the `Unknown` class. This helps the AI learn to reject faces that are not in the database.
- **Directory Structure:** Data must be stored in the following standard structure:
  ```text
  dataset/
  ├── [Member_Name_1]/
  │   ├── img_1.jpg
  │   └── ...
  ├── [Member_Name_2]/
  ├── Unknown/
  └── ...
  ```

### 1.2. Embedding Extraction & SVM Training
Use a Python script (or Google Colab) to scan through the `dataset/` directory:
1. **Feature Extraction (Embedding):** Use the `dlib` library to convert each face into a 128-dimensional vector (128D).
2. **Classifier Training (SVM):**
   - Use the **Support Vector Machine (SVM)** algorithm from the `scikit-learn` library.
   - Data is split at an **80% Train / 20% Hold-out Test** ratio.
   - **Hyperparameter Tuning (GridSearchCV):** Run a hyperparameter search over the `rbf` kernel (values of `C` and `gamma`) using Stratified 5-Fold Cross Validation.
   - **Class Imbalance Handling:** SVM is configured with `class_weight='balanced'` and `probability=True`.

> **The scikit-learn version must match the runtime environment.**
> sklearn pickles usually load across minor versions with an
> `InconsistentVersionWarning`, but when they do break, they break in the worst
> possible way: **unpickling succeeds but predictions are wrong**, with no error
> raised.
>
> In Colab, run `!pip install -U "scikit-learn==1.9.0"` then **Runtime → Restart
> session** before training. Without the restart, `import sklearn` still picks up
> the old version even though pip reported success.
>
> The notebook also writes `model_env.json` recording the versions used — ship
> that file alongside `face_model.pkl`.

### 1.3. Model Evaluation & Storage
The system uses a **Confidence Threshold >= 80%** mechanism. If the SVM's predicted probability falls below 80%, the result is forced to the `Unknown` class.
The model is then evaluated using the following metrics:
- **Accuracy, Precision, Recall, F1-score**.
- **FAR (False Accept Rate):** The rate at which strangers are incorrectly recognized as household members (lower is more secure).
- **FRR (False Reject Rate):** The rate at which household members are incorrectly rejected.
- Upon completion, the evaluation results are written to `evaluation_metrics.txt` and the model is packaged into `face_model.pkl`.

> **State the measurement conditions in the report.** The test set is drawn from
> the same source as the training set, so the measured FAR/FRR are optimistic.
> Webcam frames differ substantially in resolution, lighting, angle and
> compression.
>
> The FAR/FRR curve shows EER ≈ 0% at a threshold of 0.10 — meaning that on this
> set, members and strangers are perfectly separable. That indicates the task is
> **easier than reality**, not that the model is flawless.
>
> The strongest presentation: keep the numbers, but add a small hold-out set
> (~20 images per person) **captured with the actual demo webcam**, and report
> both figures.

---

## 2. Inference Phase

The `face_model.pkl` file obtained from Phase 1 is loaded into the Gateway system to run in real-time.

### 2.1. Liveness Detection Integration (Anti-Spoofing)
To prevent attackers from using printed photos or videos played on a phone to unlock the system, users are required to **blink** in front of the camera.
- The module calculates the **Eye Aspect Ratio (EAR)** based on 6 coordinate points around the eye.
- If the EAR drops below a threshold (`EAR_THRESHOLD = 0.21` in `face_module.py`) and then rises again, the system registers a genuine blink (Liveness Confirmed!). Checking only for "eyes closed" would let a photo taken mid-blink through.
- After confirming a real person, a clean frame is then passed for feature extraction and face recognition.

**Known limitation:** liveness is currently confirmed for the **whole frame**,
not bound to a specific face. A stranger could hold a printed photo of a member
next to their own face and blink themselves. Marked as a `TODO` in `recognize()`
— the fix is to reject frames containing more than one face.

### 2.2. Gateway Processing Flow
1. **Voice Command:** The user issues a security-sensitive command (e.g., *"Open the front door"*). `enforce_policy()` forces `face_auth=True` server-side and the pipeline returns `next_step="auth_required"`.
2. **Camera Activation:** **`MainOrchestrator.capture_frame()`** calls `face_module.capture_frame(require_blink=True, camera_index=...)` to activate the webcam and display the scanning interface (drawing green/yellow bounding boxes). The function reads **many frames** during one scan — a blink cannot be detected in a single frame — then closes the camera and returns a clean frame, or `None` on timeout.
3. **Analysis:** `AuthService.authorize_and_execute(result, frame=frame)` passes the frame to `SvmFaceRecognizer`. The system converts it to an RGB image, detects faces using `face_recognition`, and makes predictions using `face_model.pkl`.
4. **Command Approval:** `AuthService` compares the confidence against `FACE_AUTH_THRESHOLD`. Only if authorized does it call `execute_authorized_command()`, then close the `command_log` row and write the `face_log` entry.

> **Revised from the previous version.** This document used to state that
> *"`AuthService` calls `capture_frame(require_blink=True)`"*. That is no longer
> correct.
>
> **`AuthService` does not acquire the frame itself** — it receives `frame` from
> the orchestrator. If two places open the webcam, on Windows the second one gets
> `None` and Face Auth rejects every command for no visible reason.
>
> The camera is opened **on demand** and closed at the end of a single scan
> rather than held for the whole session, so the webcam stays free while the
> system isn't scanning — OBS, Zoom and screen recorders keep working during the
> demo.

### 2.3. Security Guards

**The `Unknown` label never becomes an identity.** `"Unknown"` is a truthy
string, so returning it directly would make the rule
`bool(person_name) and confidence >= threshold` **open the door** at precisely
the moment the model correctly identifies a stranger. Blocked at two layers:
`SvmFaceRecognizer.REJECT_LABELS` and `auth_service.REJECT_NAMES`. The
duplication is deliberate — the Face module could be swapped for another
implementation that forgets this rule.

**The threshold is a server decision.** The Face module only returns
`person_name` and `confidence`. If it also returns an `authorized` flag,
`AuthService` deliberately ignores it — trusting that flag would let a
swapped-in model grant itself permission.

**Fail closed on every path.** Missing module, camera failure, `frame=None`,
model exception, confidence below threshold — all lead to denial. There is no
branch that means "not sure, so let it through".

### 2.4. Decision Rule

`recognize()` uses **`argmax(predict_proba)`**, not `predict()`.

With `SVC(probability=True)` the two can disagree: `predict()` uses the
one-vs-one decision function, while `predict_proba()` uses Platt scaling
calibrated by a separate internal cross-validation. The training notebook
evaluates with `argmax(predict_proba)`, so using `predict()` at runtime would
mean the accuracy figure in `evaluation_metrics.txt` does not describe the
behaviour actually running.

### 2.5. Standalone Testing
You can test the recognition and anti-spoofing features without running the entire AIoT system using the command:
```bash
python -m tests.modules.face.test_face
```
The tool prints `person_name`, `confidence`, the server threshold and an
ALLOW / DENY verdict. It also displays the live EAR value on screen, which makes
it easy to fine-tune the blink threshold `EAR_THRESHOLD` to suit the actual room
lighting and camera angle.

This is **not** an automated test — it needs a webcam, a model file and a person
sitting in front of the machine. The corresponding automated test is
`tests/modules/face/test_face_module_contract.py`, which runs offline without
dlib.

**Four cases to verify by hand before the demo:**

| Case | Expected |
|---|---|
| Household member, blinking | authorized, door opens |
| Stranger, blinking | denied — `person_name=None` with high confidence |
| Printed photo on a phone | denied (no blink → timeout → `frame=None`) |
| Camera covered / unplugged | denied, system does not crash |

The second case is the `Unknown` vulnerability that was patched. A result of
`person_name=None` with `confidence=0.96` is **correct by design**: the model
recognised with high certainty that this is a stranger.

---

## 3. Configuration

```ini
USE_MOCK_FACE=false          # true -> MockFaceRecognizer; ANYONE can open the door
FACE_AUTH_THRESHOLD=0.80
FACE_REQUIRE_BLINK=true      # disabling this genuinely lowers security
FACE_SCAN_TIMEOUT_SECONDS=15
CAMERA_INDEX=0               # OBS/Zoom/DroidCam often occupy index 0
```

`check_config()` warns at startup if: `USE_MOCK_FACE` is enabled,
`face_model.pkl` is missing, `FACE_REQUIRE_BLINK` is off, or the threshold is
below 0.5.

## 4. Installation

See `docs/Setup-Windows.md` — four traps apply specifically to this module: the
project path must be pure ASCII (dlib is C++), `face_recognition` pulls in the
source build of `dlib`, the 130 MB `.dat` model files can be missing, and
`setuptools` must be below version 81.