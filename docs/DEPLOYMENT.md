# Local OpenTCM Setup

This guide covers running OpenTCM on your own computer. The public app listens on `127.0.0.1` only. The website link in the README is a project demonstration, not an installation requirement.

## 1. Install dependencies

Use Python 3.10 or newer with SQLite FTS5 support. From the repository root:

```bash
python -m venv .venv
```

Activate the virtual environment and create your local configuration:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

```bash
# macOS / Linux
source .venv/bin/activate
cp .env.example .env
```

Then install the dependencies:

```bash
python -m pip install -r requirements.txt
```

## 2. Configure your local instance

Edit your local `.env` and replace the placeholder values for `DEEPSEEK_API_KEY`, `OPENTCM_PASSWORD`, and `FLASK_SECRET_KEY`. Generate a random session secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Use your own API key and a model available to your account. The supplied API settings select the LLM service, not where your website runs. Model generation requires internet access; retrieval and conversation storage use local SQLite databases. A GPU is not required for API-based generation.

Never publish `.env`, real passwords, keys, or consultation histories. Keep the default `PORT=8000`, or set an unused port if another application occupies it.

## 3. Prepare the local indexes

Follow [data preparation](DATA_PREPARATION.md) to build an authorized classical KG index and, optionally, a modern-literature index. You can also configure paths to existing authorized databases:

```dotenv
OPENTCM_KG_DB=data/opentcm_kg.sqlite
OPENTCM_MODERN_DB=data/opentcm_modern.sqlite
OPENTCM_CONVERSATION_DB=data/opentcm_conversations.sqlite
```

The full corpora and databases are not included in the public repository. The classical index is required for the retrieval service to initialize. Allow enough disk space for the source data, generated indexes, and conversations; a large paragraph KG can occupy several gigabytes.

## 4. Start and open OpenTCM

From the repository root, with the virtual environment active:

```bash
python app.py
```

Open **http://127.0.0.1:8000/** in a browser on the same computer, then enter the password you configured. If you changed `PORT`, use that port in the URL instead.

Keep the terminal running while using the app. Press `Ctrl+C` to stop it. Closing the process, sleeping, or shutting down the computer stops the local service; start `python app.py` again after returning.

## 5. Verify local operation

1. Confirm that unauthenticated requests redirect to login or receive an authentication error.
2. Log in and open the welcome and chat pages.
3. Ask a non-sensitive demonstration question and inspect the answer and a citation popover.
4. Check the three languages, Standard/Deep Reasoning modes, a follow-up, and history restoration.
5. Refresh the page and confirm that the saved conversation remains available locally.

## Conversation privacy

The password-gated app uses one shared SQLite conversation store, not separate user accounts. Anyone using the same local installation can access its saved histories. Keep backups private and remove patient details from questions, screenshots, and issue reports.

## Troubleshooting

- **A configuration error at startup:** replace all required credential placeholders in `.env` and run the command from the repository root.
- **Retrieval is unavailable:** prepare the classical database and check `OPENTCM_KG_DB`.
- **The port is already in use:** set an unused `PORT` in `.env` and use the matching local URL.
- **An API request fails:** check your API key, balance, selected model, and internet connection. Do not post your key in an issue report.
