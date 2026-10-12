# Self-AI

An autonomous AI agent powered by **DeepSeek**, with a ChatGPT-style chat interface.

- **Chat** like ChatGPT: streaming answers, Markdown and code highlighting, conversation history, and a model picker (`deepseek-chat` / `deepseek-reasoner`, which shows its thought process).
- **Internet access**: the AI searches the web, reads pages (HTML, JSON, PDF), and downloads files.
- **Creates files**: reports, code, data, and web pages are saved in its workspace. You can browse, preview, download, and upload files in the **Files** panel.
- **Works on its own**: **background tasks** run autonomously with no one watching, either once, after a delay, or on a repeating schedule (for example every 60 minutes). You can start them from the Tasks panel, or just ask in chat ("every morning, check … and write a summary"). The AI creates the task itself. Every step it takes is logged so you can review it later.

## Quick start

You need **Python 3.10+** and a DeepSeek API key from <https://platform.deepseek.com/api_keys>.

**macOS / Linux**

```bash
./start.sh
```

**Windows:** double-click `start.bat`.

The first run sets everything up and asks for your API key. Then open **<http://localhost:8000>**.

<details>
<summary>Manual setup</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then put your key in DEEPSEEK_API_KEY
python -m selfai                   # options: --host, --port
```
</details>

### Run it 24/7 with Docker

Background tasks only run while Self-AI is running. To keep it always on, so it restarts automatically and keeps working after you close the browser:

```bash
cp .env.example .env   # add your DEEPSEEK_API_KEY
docker compose up -d --build
```

Data (chats, tasks, workspace files) is kept in the `selfai-data` Docker volume.

## Using it

| You want to… | Do this |
|---|---|
| Ask anything, with current info from the web | Just chat. The AI decides when to search and read pages, and cites URLs. |
| Get a document, script, dataset, or web app | Ask for it. The AI writes it to the workspace and links the file. |
| Give the AI a file (PDF, CSV, text…) | Click 📎 or drag the file into the chat. It is uploaded to `uploads/`. |
| Hand off a long job | "Do this in the background: …" or use **Background tasks → New task**. |
| Repeat a job on a schedule | Tick **Repeat every N min**, or say "every hour, …". |
| Watch what the AI did | Expand any tool step in chat, or open a task to see each run's full log and final report. |

Each recurring run gets the previous run's report, and the workspace persists. That lets the AI build on earlier work, for example by keeping notes or appending to a log file.

## Configuration

All settings live in `.env` (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | Your DeepSeek API key (required) |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Default model |
| `DEEPSEEK_MODELS` | `deepseek-chat,deepseek-reasoner` | Models offered in the picker |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Any OpenAI-compatible endpoint (see below) |
| `TAVILY_API_KEY` | — | Use [Tavily](https://tavily.com) for web search instead of DuckDuckGo |
| `SELFAI_DATA_DIR` | `data` | Where chats, tasks, and the workspace are stored |
| `SELFAI_HOST` / `SELFAI_PORT` | `127.0.0.1` / `8000` | Web server address |
| `SELFAI_PASSWORD` | — | Require a password (browser login prompt) |
| `SELFAI_ALLOWED_HOSTS` | `localhost,127.0.0.1,::1` | Host names the UI may be opened under (`*` = any) |
| `SELFAI_MAX_CONCURRENT_TASKS` | `2` | Background tasks that may run at the same time |
| `SELFAI_TASK_MAX_STEPS` / `SELFAI_CHAT_MAX_STEPS` | `50` / `25` | Tool-call budget per task run / chat reply |
| `SELFAI_ALLOW_PRIVATE_NETWORK` | `false` | Let the AI fetch URLs on your local network |
| `SELFAI_MAX_DOWNLOAD_MB` | `50` | Max size of downloads and uploads |

**Running DeepSeek locally:** point `DEEPSEEK_BASE_URL` at a local OpenAI-compatible server, such as Ollama at `http://localhost:11434/v1`. Then set `DEEPSEEK_MODEL` and `DEEPSEEK_MODELS` to your local model. The model must support tool (function) calling.

**Accessing it from your phone or another computer:** set `SELFAI_HOST=0.0.0.0`, set a `SELFAI_PASSWORD`, and add the address you'll use (for example `192.168.1.20`) to `SELFAI_ALLOWED_HOSTS`.

## How it works

```
Browser UI ──HTTP/SSE──▶ FastAPI server ──▶ Agent loop ──▶ DeepSeek API
                              │                 │
                              │                 └─▶ Tools: web_search · fetch_url · download_file
                              │                            write_file · read_file · list_files · delete_file
                              │                            create_task · list_tasks · cancel_task
                              ├─▶ Task scheduler (runs due tasks with the same agent, no human in the loop)
                              └─▶ SQLite (chats, tasks, run logs) + workspace folder (files)
```

- **Agent loop** (`selfai/agent.py`): sends the conversation and tool definitions to DeepSeek, runs the tools the model calls, and feeds results back. This repeats until the model answers without calling a tool, or the step budget runs out.
- **Chat** (`selfai/chat.py`): each reply runs as a background job, so it keeps going and is saved even if you close the tab. **Stop** cancels it and keeps the partial answer.
- **Tasks** (`selfai/tasks.py`): a scheduler starts due tasks, records every tool call, result, and final report, and reschedules recurring tasks. Tasks interrupted by a restart are picked up again on the next start.
- **Tools** (`selfai/tools/`): file tools are sandboxed to the workspace folder. Web tools refuse private and local-network addresses by default.

## Safety notes

The AI acts on its own, so a few guardrails are built in:

- File access is confined to the workspace directory; attempts to escape it (`../`, symlinks) are rejected.
- Web tools block `localhost`, private IP ranges, and cloud metadata endpoints, so a malicious page can't steer the AI into your network.
- The server only listens on `127.0.0.1` by default. It rejects requests from other websites (CSRF) and from unexpected host names (DNS rebinding).
- Files the AI creates are served in a sandbox, so a saved web page can't use the app's API.
- Limits on steps, concurrent tasks, active tasks, and download sizes keep runaway jobs in check. API usage costs money, so keep an eye on recurring tasks.

## Development

```bash
pip install -r requirements-dev.txt
pytest          # tests use a scripted fake model; no API key or network needed
ruff check .
```
