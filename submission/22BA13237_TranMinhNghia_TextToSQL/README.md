# Multi-Agent Text-to-SQL System

Bachelor thesis source-code submission by Tran Minh Nghia (22BA13237).

## Package contents

- `app/`: Streamlit user interface.
- `src/`: multi-agent pipeline, SQL validation, execution, RAG, and cache.
- `tools/`: CLI visualization helpers.
- `data/Chinook_VN.sqlite`: small demonstration database with 11 tables.
- `test/evaluation_runs/`: compact final Chapter 4 evaluation artifacts.
- `docs/ARCHITECTURE.md`: implementation architecture.

Large development files, virtual environments, logs, credentials, the 24 MB
Northwind database, and the 74 MB FAISS index are intentionally excluded to
meet the 30 MB upload limit. The application can run without a prebuilt FAISS
index; retrieval falls back to zero-shot generation when no index is present.

## Requirements

- Windows 10/11
- Python 3.11 (64 bit)
- Internet access for the configured LLM provider
- Google Cloud Vertex AI credentials for the thesis configuration

## Installation

From PowerShell or Command Prompt in this directory:

```powershell
setup_windows.bat
```

Manual installation:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

## Vertex AI configuration

The evaluated thesis configuration uses Google Cloud Vertex AI. Install the
Google Cloud CLI, authenticate, and set the project in `.env`:

```powershell
gcloud auth application-default login
```

Then edit `.env`:

```text
LLM_PROVIDER=google
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

The Google Cloud project must have Vertex AI enabled. No API key or credential
file is included in this submission.

## Verify the local installation

This check does not call an external LLM:

```powershell
.venv\Scripts\python.exe verify_installation.py
```

Expected result: the Chinook VN schema is loaded, a read-only SQL query is
validated and executed, and `INSTALLATION CHECK PASSED` is printed.

## Run the Streamlit interface

```powershell
run_ui.bat
```

Or:

```powershell
.venv\Scripts\python.exe -m streamlit run app\main.py
```

Open `http://localhost:8501` and select `Chinook_VN.sqlite`.

Example question:

```text
Liet ke 5 nghe si co nhieu album nhat.
```

Vietnamese text with diacritics is also supported.

## Run the CLI

Interactive mode:

```powershell
run_cli.bat
```

Single question:

```powershell
.venv\Scripts\python.exe -m src.cli.main --db-path data\Chinook_VN.sqlite -q "Nghe si nao co nhieu album nhat?"
```

Type `exit` to leave interactive mode. The first LLM request may take longer
because schema context and provider connections are initialized.

## Evaluation artifacts

The compact artifacts used in Chapter 4 are under:

```text
test/evaluation_runs/chapter4_chapter4_final_20260628/
```

They include the final benchmark summaries, ablation results, cache evaluation,
consistency analysis, error audit, experiment manifest, and frozen environment.

## Security note

The application performs read-only SQL validation at the application layer.
Production deployment still requires read-only database credentials, access
control, query limits, logging, monitoring, and a dedicated security review.
