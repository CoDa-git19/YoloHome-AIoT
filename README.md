# YoloHome-AIoT - Multimodal Smart Home System

Welcome to the **YoloHome-AIoT** project! This repository contains the source code for our multimodal smart home system (FaceID, Voice Recognition, LLM, and Yolo:Bit Hardware). 

Since our team consists of 5 members working across completely different domains (AI, UI, and Hardware), strict adherence to this workflow is **mandatory** to prevent integration conflicts and broken builds.

---

## 1. Prerequisites & Toolchain

Before you write any code, ensure your local machine has the following installed:
1. **Python 3.10+** (Ensure Python is added to your system PATH).
2. **Git** (For version control).
3. **Visual Studio Code (VS Code)** (Recommended IDE).
4. **C++ Build Tools** (Required for compiling `dlib` in the FaceID module - install via Visual Studio Build Tools on Windows).

---

## 2. Local Environment Setup

We use Python Virtual Environments to ensure everyone uses the exact same library versions without breaking their personal computers.

### 2.1 Clone the repository:

```bash
git clone [https://github.com/](https://github.com/)[your-username]/YoloHome-AIoT.git
cd YoloHome-AIoT

```

### 2.2 Create a virtual environment:

```bash
python -m venv venv

```

### 2.3 Activate the virtual environment:

- Windows (Git Bash/PowerShell): 
```bash
source venv/Scripts/activate
```

- Mac/Linux:
```bash
source venv/bin/activate
```
*(You should see (venv) appear at the beginning of your terminal line).*

### 2.4 Install project dependencies:

```bash
pip install -r requirements.txt

```

### 2.5 Set up Environment Variables:

Ask the team member in charge of each module for the secret keys. Create a .env file in the root directory and add them:
```bash
GEMINI_API_KEY="your_api_key"
ADAFRUIT_IO_USERNAME="your_username"
ADAFRUIT_IO_KEY="your_key"

```

### 2.6 Run the Application:
To verify your setup is fully working, run the main gateway:
```bash
python system_core/main.py
```

---

## 3. Project Structure & Boundaries

To avoid merge conflicts, **only work within your assigned module directory**:

`speech_recognition/`: PhoWhisper and VAD integration.

`llm_integration/`: Gemini API prompting and JSON parsing.

`face_recognition/`: dlib embeddings and OpenCV frame processing.

`hardware_gateway/`: Yolo:Bit serial communication and MQTT.

`system_core/`: System integration and Design Patterns (Observer, Strategy).

`web_dashboard/`: Flask application and UI.

---

## 4. Branching Strategy (GitHub Flow)

We use a strict branching model to protect the system's stability.

- `main`: The "sacred" branch. Always stable, ready for presentation. **DO NOT touch**.

- `develop`: The integration playground where all modules meet. **DO NOT code directly here**.

---

## 5. How to work on your daily tasks

### 5.1 Always sync with the latest integration code first:

```bash
git checkout develop
git pull origin develop
```

### 5.2 Create a specific feature branch for your work:
Name your branch clearly based on the feature.

```bash
git checkout -b feature/face-auth-pipeline
# or
git checkout -b bugfix/stt-latency
```

### 5.3 Write code, test locally, and commit:
```bash
git add .
git commit -m "Feat: Implement SVM classifier for FaceID"
git push origin feature/face-auth-pipeline
```

---

## 6. Pull Requests & Reviews

Once your feature works perfectly on your machine, it's time to merge it into `develop`.

### 6.1 Open a Pull Request (PR):

- Go to GitHub and click **Compare & pull request** on your pushed branch.

- Set the base branch to `develop`.

### 6.2 The PR Review Rule (CRITICAL):

- **You absolutely cannot merge your own code**.

- Under the **Reviewers** section on the right, you **must tag the main reviewer** .

- Other team members are encouraged to review the code to learn.

### 6.3 Wait for Approval:

Your code must receive at least **1 Approval** before the "Merge pull request" button becomes active.

The integration lead will pull your branch locally to test memory consumption and threading conflicts before approving.

## 🆘 7. Troubleshooting & Rules of Thumb
- **Never push AI models (.pt, .h5, .bin) to GitHub:** Our `.gitignore` blocks them. Download weights locally and put them in the `models/` folder.

- **Run the main gateway before PR:** Always test your module by running `python system_core/main.py` to ensure it doesn't break the global application state.

- If a bug holds you up for *more than 48 hours (2 days)*, push your current branch and flag it in the team group chat so we can pair-program and unblock you.

---
***Let's collaborate effectively and ace this project together. Happy coding! 🚀🚀🚀***

---
### 🇻🇳 [Xem phiên bản Tiếng Việt (Vietnamese Version)](README-vi.md)
