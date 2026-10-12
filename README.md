# Self-AI

Your own autonomous AI assistant with a ChatGPT-style interface. It runs on a **free AI model downloaded to your device** (an Android phone or a computer), or on the DeepSeek API if you prefer.

- **Chat** like ChatGPT: streaming answers, Markdown and code highlighting, and conversation history. A **Think** button makes the model reason step by step before answering.
- **Internet access**: the AI searches the web, reads pages (HTML, JSON, PDF), and downloads files.
- **Creates files**: reports, code, data, and web pages are saved in its workspace. You can browse, preview, download, and upload files in the **Files** panel.
- **Works on its own**: **background tasks** run autonomously, either once, after a delay, or on a schedule (for example every 60 minutes). Start one from the Tasks panel, or just ask in chat and the AI creates the task itself.
- **Free and private**: with a local model, everything runs on your device. No account, no API costs, and it works offline (except web browsing).

## The free models

Self-AI uses [Ollama](https://ollama.com) to download and run open models. Pick one in the **Models** panel; Self-AI suggests the best fit for your device's memory.

| Model | Download | Good for |
|---|---|---|
| **Qwen 3.5 · 4B** (`qwen3.5:4b`) | ≈3.4 GB | Phones with 12 GB+ RAM, and computers. The default on computers. |
| **Qwen 3.5 · 2B** (`qwen3.5:2b`) | ≈2.7 GB | Phones with 8 GB RAM. Leaves room for Android and the browser. |
| Qwen 3.5 · 0.8B (`qwen3.5:0.8b`) | ≈1 GB | Phones with 4–6 GB RAM. Simple tasks. |
| Qwen 3.5 · 9B (`qwen3.5:9b`) | ≈6.6 GB | Computers with 16 GB RAM. Much smarter. |
| Gemma 4 · E2B (`gemma4:e2b`) | ≈7.2 GB | Google's model; also understands images and audio |

You can also download any other model from [ollama.com/library](https://ollama.com/library) by typing its name in the Models panel. For web access and files, the model must support **tools**; the Models panel shows this for each model.

## Install on an Android phone

1. Install **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/). The Google Play version is outdated.
2. Open Termux and paste:

   ```bash
   pkg install -y git && git clone https://github.com/24316bmvlm2038-tech/self-ai && cd self-ai && bash install-android.sh
   ```

   This installs everything, downloads a model that fits your phone, and opens Self-AI in your browser. The download is several GB, so use Wi-Fi.
3. In Chrome, tap **⋮ → Add to Home screen** (or **Install app**) so Self-AI opens like a normal app.

Next time, open Termux and type `selfai`. Keep Termux running in the background while you use the app. Background tasks also only run while Termux is running, and the phone stays awake while it does.

**What to expect:** the AI runs on the phone's processor. Replies take a few seconds to start, and longer for the very first message or with **Think** on. A phone with 8 GB+ RAM and a recent chip works best.

### If you see "Lost connection" or "network error"

This means Android stopped Termux, usually to free memory while the model was working. Open Termux and type `selfai`; the page reconnects by itself. On a phone, `start.sh` also restarts Self-AI automatically, and Self-AI restarts Ollama if it was stopped. To stop it happening:

1. **Use a smaller model.** In **Models**, pick the one marked *Best for this device* (on an 8 GB phone, that's Qwen 3.5 · 2B).
2. **Let Termux run in the background:** Android Settings → Apps → Termux → Battery → **Unrestricted**.
3. **Turn off Android's background process limit** (Android 12 and newer), in **Developer options**:
   - Android 14+: turn on **Disable child process restrictions**.
   - Android 12–13: open **Feature flags** and turn off **settings_enable_monitor_phantom_procs**.

   To show Developer options, go to Settings → About phone and tap **Build number** 7 times.

**iPhone:** iOS doesn't allow apps like Termux, so Self-AI can't run on an iPhone itself. Run it on a computer and open it from the iPhone's browser instead (see [Use from your phone](#use-from-your-phone-or-another-device)).

## Install on a computer

You need **Python 3.10+**. For free local models, also install [Ollama](https://ollama.com/download).

- **macOS / Linux:** `./start.sh`
- **Windows:** double-click `start.bat`

On the first run, press Enter to use a free local model, or paste a DeepSeek API key. Then open **<http://localhost:8000>**, go to **Models**, and download a model.

<details>
<summary>Manual setup</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
ollama serve &                     # if Ollama isn't already running
ollama pull qwen3.5:4b
python -m selfai                   # options: --host, --port
```
</details>

### Run it 24/7 with Docker

```bash
docker compose up -d --build
```

This starts Self-AI and Ollama together. Both restart automatically, and background tasks keep running with the browser closed. Open <http://localhost:8000> and download a model in **Models**.

### Use DeepSeek instead

Set `DEEPSEEK_API_KEY` in `.env` (get one at <https://platform.deepseek.com/api_keys>). Self-AI then uses the DeepSeek API, which is faster and smarter than a phone-sized model but costs money per use. You can choose `deepseek-chat` or `deepseek-reasoner` in the model picker.

## Using it

| You want to… | Do this |
|---|---|
| Ask anything, with current info from the web | Just chat. The AI decides when to search and read pages, and cites URLs. |
| Get better answers to hard questions | Turn on **Think** next to the message box. |
| Get a document, script, dataset, or web app | Ask for it. The AI writes it to the workspace and links the file. |
| Give the AI a file (PDF, CSV, text…) | Click 📎 or drag the file into the chat. |
| Hand off a long job | "Do this in the background: …" or use **Background tasks → New task**. |
| Repeat a job on a schedule | Tick **Repeat every N min**, or say "every hour, …". |
| Watch what the AI did | Expand any tool step in chat, or open a task to see each run's log and final report. |

### Use from your phone or another device

To open Self-AI running on a computer from your phone's browser, set these in `.env`:

```bash
SELFAI_HOST=0.0.0.0
SELFAI_PASSWORD=choose-a-password
SELFAI_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.20   # add your computer's LAN address
```

Then open `http://192.168.1.20:8000` on the phone.

## Configuration

All settings live in `.env` (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `SELFAI_PROVIDER` | `ollama` (`deepseek` if a key is set) | Free local models, or the DeepSeek API |
| `SELFAI_MODEL` | `qwen3.5:4b` | Default model (you can also switch in the app) |
| `SELFAI_OLLAMA_URL` | `http://127.0.0.1:11434` | Where Ollama runs |
| `SELFAI_CONTEXT_TOKENS` | `8192` | How much text a local model considers at once (more uses more RAM) |
| `SELFAI_TASK_THINKING` | `true` | Background tasks think step by step when the model supports it |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key |
| `DEEPSEEK_MODEL` / `DEEPSEEK_MODELS` | `deepseek-chat` | DeepSeek model and the picker's list |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Any OpenAI-compatible endpoint |
| `TAVILY_API_KEY` | — | Use [Tavily](https://tavily.com) for web search instead of DuckDuckGo |
| `SELFAI_DATA_DIR` | `data` | Where chats, tasks, and the workspace are stored |
| `SELFAI_HOST` / `SELFAI_PORT` | `127.0.0.1` / `8000` | Web server address |
| `SELFAI_PASSWORD` | — | Require a password (browser login prompt) |
| `SELFAI_ALLOWED_HOSTS` | `localhost,127.0.0.1,::1` | Host names the UI may be opened under (`*` = any) |
| `SELFAI_MAX_CONCURRENT_TASKS` | `1` local / `2` DeepSeek | Background tasks running at the same time |
| `SELFAI_TASK_MAX_STEPS` / `SELFAI_CHAT_MAX_STEPS` | `30` / `15` local | Tool-call budget per task run / chat reply |
| `SELFAI_ALLOW_PRIVATE_NETWORK` | `false` | Let the AI fetch URLs on your local network |
| `SELFAI_MAX_DOWNLOAD_MB` | `50` | Max size of downloads and uploads |

## How it works

```
Browser UI ──HTTP/SSE──▶ Self-AI server ──▶ Agent loop ──▶ Ollama (local model)  or  DeepSeek API
                              │                 │
                              │                 └─▶ Tools: web_search · fetch_url · download_file
                              │                            write_file · read_file · list_files · delete_file
                              │                            create_task · list_tasks · cancel_task
                              ├─▶ Task scheduler (runs due tasks with the same agent, no human in the loop)
                              └─▶ SQLite (chats, tasks, run logs) + workspace folder (files)
```

- **Agent loop** (`selfai/agent.py`): sends the conversation and tool definitions to the model, runs the tools the model calls, and feeds results back. This repeats until the model answers without calling a tool, or the step budget runs out.
- **Model backends** (`selfai/llm.py`, `selfai/ollama.py`): Ollama's native API, where the app sets the context size, separates "thinking" from the answer, and keeps the model loaded between messages. Or the DeepSeek/OpenAI-compatible API.
- **Phone-friendly**: all dependencies are pure Python (Starlette, httpx…), so they install on Android without compilers. For small models, prompts and tool output are kept compact, and the system prompt stays stable so the model can reuse its cache between messages.
- **Chat** (`selfai/chat.py`): each reply runs as a background job, so it keeps going and is saved even if you close the tab. **Stop** keeps the partial answer.
- **Tasks** (`selfai/tasks.py`): a scheduler starts due tasks, logs every step, and reschedules recurring tasks. Tasks interrupted by a restart are picked up again.

## Safety notes

- File access is confined to the workspace directory; attempts to escape it (`../`, symlinks) are rejected.
- Web tools block `localhost`, private IP ranges, and cloud metadata endpoints, so a malicious page can't steer the AI into your network.
- The server only listens on `127.0.0.1` by default. It rejects requests from other websites (CSRF) and from unexpected host names (DNS rebinding).
- Files the AI creates are served in a sandbox, so a saved web page can't use the app's API.
- Limits on steps, concurrent tasks, and download sizes keep runaway jobs in check.

## Development

```bash
pip install -r requirements-dev.txt
pytest          # uses a scripted fake model and a fake Ollama server; no network needed
ruff check .
```
