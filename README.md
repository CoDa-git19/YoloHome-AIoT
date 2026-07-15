# YoloHome-AIoT - Multimodal Smart Home System

Welcome to the **YoloHome-AIoT** project! This repository contains the source code for our multimodal smart home system (FaceID, Voice Recognition, LLM, and Yolo:Bit Hardware). 

Since our team consists of 5 members working across completely different domains (AI, UI, and Hardware), strict adherence to this workflow is **mandatory** to prevent integration conflicts and broken builds.


## 🗂️ Project Structure

```text
.
├── benchmark/          # Labelled dataset for LLM evaluation
├── config/             # Shared runtime config, schemas, aliases, capabilities
├── database/           # SQLite schema, initializer, and DB documentation
├── diagrams/           # Design pattern and architecture diagrams
├── docs/               # Project documentation
├── modules/            # AIoT modules: LLM, face, speech, hardware gateway
├── services/           # Application services and orchestration logic
├── system_core/        # Core abstractions and design pattern implementations
├── tests/              # Unit and integration tests grouped by concern
├── tools/              # Diagnostic & benchmark scripts (run via python -m tools.*)
├── web_dashboard/      # Flask dashboard UI
├── .env.example        # Example environment variables
├── docker-compose.yml  # Optional containerized runtime setup
├── README.md           # English documentation
└── README-vi.md        # Vietnamese documentation
```

The LLM command pipeline spans several directories. Key files:

```text
modules/llm_integration/
├── llm_module.py         # Core parser: prompt, Gemini call, retry, routing
├── llm_strategy.py       # GeminiLLMStrategy, MockLLMStrategy
├── openai_strategy.py    # OpenAILLMStrategy (proves the Strategy Pattern)
├── validator.py          # Schema validation + server-side face_auth policy
└── prompt_template.txt   # Prompt template (placeholders filled from config)

services/
├── command_service.py    # Orchestrates the full transcript -> hardware pipeline
├── session_service.py    # Multi-turn slot filling (pending command store)
├── rule_service.py       # Edge-triggered automation rules
├── logging_service.py    # Command / error logging to SQLite
└── auth_service.py       # Face-auth flow (owned by the Face module)

system_core/
├── commands.py           # Command Pattern (device actions, undo, registry)
├── strategies.py         # LLMStrategy interface
├── observers.py          # Observer Pattern: sensors -> rules pipeline
├── contracts.py          # Startup interface-contract checks
└── main.py               # Application entry point

tools/
├── check_setup.py        # Verify config + DB + pipeline (offline)
├── probe_gemini.py       # List models available to your API key
├── smoke_gemini.py       # 10 real Gemini calls, checks the pipeline
└── benchmark_llm.py      # Provider-agnostic benchmark, Markdown output
```

Important configuration files:

```text
config/
├── command_schema.json       # JSON command schema, intents, sensors, operators
├── device_registry.json      # Supported rooms, devices, and actions
├── language_aliases.json     # Vietnamese aliases for mock command parsing
├── device_capabilities.json  # Generic/special command behavior and safety policy
├── capabilities.py           # Capability policy resolver
└── settings.py               # Runtime paths and environment variables
```

Runtime files such as `database/yolohome.db`, `__pycache__/`, `.pytest_cache/`,
`.env`, and `venv/` should not be committed.

For the LLM command pipeline, Strategy Pattern, and Command Pattern design,
see [`docs/LLM-Command-Strategy-Overview.md`](docs/LLM-Command-Strategy-Overview.md).

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
git clone https://github.com/<username-or-org>/YoloHome-AIoT.git
cd YoloHome-AIoT

```

### 2.2 Create a virtual environment:

```bash
python -m venv venv

```

### 2.3 Activate the virtual environment:

- Windows PowerShell:
```powershell
.\venv\Scripts\Activate.ps1
```

- Windows Git Bash: 
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

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Or on macOS/Linux:

```bash
cp .env.example .env
```

Ask the team member in charge of each module for the secret keys. Create a .env file in the root directory and add them:
```bash
# LLM engine: keep true for offline dev/tests (no API quota used)
USE_MOCK_LLM=true
GEMINI_API_KEY="your_api_key"

# IMPORTANT: pin a stable model. As of 07/2026 gemini-2.5-flash and
# gemini-2.5-flash-lite return 404 for new users. Do NOT use "-latest"
# aliases (they hot-swap and break benchmark reproducibility).
# Run `python -m tools.probe_gemini` to see which models your key can use.
GEMINI_MODEL="gemini-3.1-flash-lite"
GEMINI_THINKING_LEVEL=minimal
GEMINI_STRUCTURED_OUTPUT=true

ADAFRUIT_IO_USERNAME="your_username"
ADAFRUIT_IO_KEY="your_key"

DATABASE_URL="sqlite:///database/yolohome.db"
FLASK_ENV="development"

FACE_AUTH_THRESHOLD=0.80
```

For offline development and unit tests, keep `USE_MOCK_LLM=true`.
For real Gemini calls, set `USE_MOCK_LLM=false` and provide `GEMINI_API_KEY`.

Never commit the real `.env` file. Commit `.env.example` only.

---

### 2.6 Verify your setup:

Run the full offline test suite first (no API quota used):
```bash
python -m pytest -q
```

Then check config, database, and the command pipeline end-to-end:
```bash
python -m tools.check_setup
```

To verify the whole gateway, run the main entry point:
```bash
python system_core/main.py
```
To run the Flask dashboard separately:
```bash
python web_dashboard/app.py
```

### 2.7 LLM diagnostic tools (optional, uses API quota):
```bash
python -m tools.probe_gemini     # list models available to your key
python -m tools.smoke_gemini     # 10 real Gemini calls, checks the pipeline
python -m tools.benchmark_llm --models gemini-3.1-flash-lite --rpm 15
```
> Gemini free tier allows 15 requests/minute. Always pass `--rpm 15` so quota
> errors don't pollute your benchmark results.
---

## 3. Project Structure & Boundaries

To avoid merge conflicts, **only work within your assigned module directory**:

`modules/llm_integration/`: Gemini prompting, JSON parsing, mock command parsing, and validation integration.

`modules/speech_recognition/`: Speech-to-Text integration.

`modules/face_recognition/`: Face recognition and camera processing.

`modules/hardware_gateway/`: Yolo:Bit, serial, MQTT, or IoT gateway communication.

`services/`: Application-level services such as command orchestration, auth flow, logging, and rule handling.

`system_core/`: Core abstractions and design pattern implementations, including Command and Strategy.

`config/`: Shared runtime configuration, schemas, device registry, aliases, and capability policy.

`database/`: SQLite schema, initialization script, and database documentation.

`web_dashboard/`: Flask dashboard application and templates.

`tests/`: Unit and integration tests grouped into `db/`, `llm/`, `pattern/`, and `service/`. The full suite runs offline with mocked LLM (no API quota): `python -m pytest -q`.

## 4. Branching Strategy (GitHub Flow)

- `main`: The central branch for the project. All working code lives here. **DO NOT push directly to this branch**; always use a Pull Request.

---

## 5. How to work on your daily tasks

### 5.1 Always sync with the latest integration code first:

```bash
git checkout main
git pull origin main
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

Once your feature works perfectly on your machine, it's time to merge it into `main`.

### 6.1 Open a Pull Request (PR):

- Go to GitHub and click **Compare & pull request** on your pushed branch.

- Set the base branch to `main`.

### 6.2 The PR Review Rule (CRITICAL):

- **You absolutely cannot merge your own code**.

- Under the **Reviewers** section on the right, you **must tag the main reviewer** .

- Other team members are encouraged to review the code to learn.

### 6.3 Wait for Approval:

- Your code must receive at least **1 Approval** before the "Merge pull request" button becomes active.

- After creating the PR, simply click **Merge pull request**.

## 7. LLM Module Integration Contracts

The LLM command pipeline was hardened with server-side security, multi-turn
conversation, API resilience, and edge-triggered automation rules. If you
integrate with it, three contracts matter:

1. **`LLMStrategy.parse_and_validate()`** takes a `pending_command` argument
   (multi-turn slot filling). Any custom strategy must accept it, or
   `CommandService` raises `ContractError` at startup.

2. **Hardware `execute_command(action="get_status")`** must return
   `{"status": "success", "state": "on"|"off"|"open"|"closed"}`. A missing
   `"state"` does not crash the system, but the user always hears
   "unknown state".

3. **`main.py` must wire the Observer**, or automation rules never run — and
   they fail silently, with no error and no log:
   ```python
   hardware.attach(RuleObserver(rules, service))
   while True:
       hardware.poll_sensors()   # the system heartbeat
       time.sleep(2)
   ```

Security principle: **the LLM understands intent; it does not make security
decisions.** The server enforces `face_auth` from config, ignoring whatever the
LLM returns. See
[`docs/LLM-Command-Strategy-Overview.md`](docs/LLM-Command-Strategy-Overview.md).

---

## 🆘 8. Troubleshooting & Rules of Thumb
- **Never push AI models (.pt, .h5, .bin) to GitHub:** Our `.gitignore` blocks them. Download weights locally and put them in the `models/` folder.

- **Run the main gateway before PR:** Always test your module by running `python system_core/main.py` to ensure it doesn't break the global application state.

- **Gemini returns 404 for a model that used to work:** Google deprecates models for new users. Run `python -m tools.probe_gemini` and update `GEMINI_MODEL`.

- **The dashboard always shows "unknown state":** the hardware module is not returning `"state"` from `get_status`. Check the logs for `CONTRACT VIOLATION`.

- **You created an automation rule but nothing happens:** `main.py` is probably missing `hardware.attach(RuleObserver(...))` or the `poll_sensors()` loop. Rules do not run on their own.

- If a bug holds you up for *more than 48 hours (2 days)*, push your current branch and flag it in the team group chat so we can pair-program and unblock you.

---
***Let's collaborate effectively and ace this project together. Happy coding! 🚀🚀🚀***

---
### 🇻🇳 [Xem phiên bản Tiếng Việt (Vietnamese Version)](README-vi.md)