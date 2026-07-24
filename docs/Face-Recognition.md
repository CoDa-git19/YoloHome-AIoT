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

### 1.3. Model Evaluation & Storage
The system uses a **Confidence Threshold >= 80%** mechanism. If the SVM's predicted probability falls below 80%, the result is forced to the `Unknown` class.
The model is then evaluated using the following metrics:
- **Accuracy, Precision, Recall, F1-score**.
- **FAR (False Accept Rate):** The rate at which strangers are incorrectly recognized as household members (lower is more secure).
- **FRR (False Reject Rate):** The rate at which household members are incorrectly rejected.
- Upon completion, the evaluation results are written to `evaluation_metrics.txt` and the model is packaged into `face_model.pkl`.

---

## 2. Inference Phase

The `face_model.pkl` file obtained from Phase 1 is loaded into the Gateway system to run in real-time.

### 2.1. Liveness Detection Integration (Anti-Spoofing)
To prevent attackers from using printed photos or videos played on a phone to unlock the system, users are required to **blink** in front of the camera.
- The module calculates the **Eye Aspect Ratio (EAR)** based on 6 coordinate points around the eye.
- If the EAR drops below a threshold (e.g., `0.20`) and then rises again, the system registers a genuine blink (Liveness Confirmed!).
- After confirming a real person, a clean frame is then passed for feature extraction and face recognition.

### 2.2. Gateway Processing Flow
1. **Voice Command:** The user issues a security-sensitive command (e.g., *"Open the front door"*).
2. **Camera Activation:** `AuthService` calls `capture_frame(require_blink=True)` to activate the webcam and display the scanning interface (drawing green/yellow bounding boxes).
3. **Analysis:** The frame is passed to `SvmFaceRecognizer`. The system converts it to an RGB image, detects faces using `face_recognition`, and makes predictions using `face_model.pkl`.
4. **Command Approval:** If the user is confirmed to be a household member, the Gateway sends a command to the hardware to unlock the door and automatically logs the face into the database.

### 2.3. Standalone Testing
You can test the recognition and anti-spoofing features without running the entire AIoT system using the command:
```bash
python -m tools.test_face
```
This command makes it easy to fine-tune the blink threshold `EAR_THRESHOLD` to suit the actual room lighting and camera angle.
