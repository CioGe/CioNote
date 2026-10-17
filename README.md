<div align="center">

# 📝 CioNote

**A local-first note app with a built-in AI assistant — pure Python, zero dependencies.**

Bi-directional links · Backlinks · Knowledge graph · Local LLM chat · Your data never leaves your machine

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)]()

[Features](#-features) · [Quick Start](#-quick-start) · [AI Assistant](#-ai-assistant) · [中文说明](README.zh-CN.md)

![CioNote — main window](assets/screenshot.png)

</div>

---

## ✨ Features

- **📝 WYSIWYG markdown editor** — bold / italic / headings / code / lists, with live preview
- **🔗 Bi-directional links** — type `[[Note Name]]` to connect ideas, `Ctrl+Click` to jump
- **🔙 Backlinks panel** — every note shows what points back to it
- **🕸 Knowledge graph** — visualize your whole vault (Ctrl+G)
- **🤖 Built-in AI assistant** — chat with a **local model via Ollama** (offline & private) or any **OpenAI-compatible API** (DeepSeek, GLM, SiliconFlow, OpenAI…)
- **🔒 Privacy by design** — plain `.md` files on your disk; API keys are encrypted with Windows DPAPI, never stored in plaintext
- **📦 Zero dependencies** — pure Python standard library (tkinter). No pip install, no node_modules, no wallet drain
- **🌍 Bilingual UI** — English / 中文, switchable in-app

## 🚀 Quick Start

1. Install [Python 3.8+](https://www.python.org/downloads/) — **check "Add Python to PATH"** during setup
2. Double-click **`Launch CioNote.bat`** — the launcher auto-detects your Python, probes Ollama, and starts the app (logs to `launcher.log`)
3. Menu **File → Open Vault** → pick any folder as your notes vault. Done.

> No response when double-clicking? Run **`DEBUG-Launcher.bat`** to see the full error.

## 🤖 AI Assistant

Two ways to power the chat panel — pick either:

### Option A — Local & free: Ollama + China mirror

1. Install [Ollama](https://ollama.com)
2. Pull a model. In mainland China the official registry often fails (`registry.ollama.ai: no such host`), so use the ModelScope mirror:

   ```
   ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF
   ```

   Or just double-click **`Download-Model-ChinaMirror.bat`**, or use the in-app menu **AI Assistant → Download model (China mirror)**

3. Back in the app: **AI Assistant → Check Ollama connection** — installed models appear in the dropdown automatically. Start chatting.

| Model (ModelScope mirror) | Notes |
|---|---|
| `modelscope.cn/Qwen/Qwen2.5-1.5B-Instruct-GGUF` | lighter, for low-spec machines |
| `modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF` | balanced (recommended) |
| `modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF` | better quality, ~5 GB RAM |

> **Check the "Route" indicator before chatting** — it must be on **💻 Local** for messages to go to Ollama. If you previously configured an online API (`api_mode=openai`), messages go there instead even when Ollama shows "connected".

### Option B — Online: any OpenAI-compatible API

Menu **AI Assistant → Online API Settings** → Backend `openai`:

| Service | Base URL | Model example |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

> **No API key ships with this app** — get your own (most services have a free tier). Keys are DPAPI-encrypted on disk and masked in the UI (`sk-****7f3a`).

## ⌨️ Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` / `Ctrl+N` | Open vault / New note |
| `Ctrl+S` / `Ctrl+P` | Save / Quick switch note |
| `Ctrl+B` / `Ctrl+I` | Bold / Italic |
| `` Ctrl+` `` | Insert wiki link |
| `Ctrl+G` / `Ctrl+E` | Knowledge graph / AI panel |
| `Ctrl+Click` | Follow a wiki link |
| `Ctrl+Enter` | Send chat message |

## 📦 Build a standalone .exe (optional)

Double-click **`Build EXE.bat`** — produces a portable `CioNote.exe` in `dist/` (no Python needed on target machines).

## 🗂 Project Structure

| File | Purpose |
|---|---|
| `cionote.py` | The entire app (~2000 lines, single file) |
| `launch.ps1` | Launcher: interpreter auto-detect + Ollama probe + logging |
| `Launch CioNote.bat` / `DEBUG-Launcher.bat` | Normal / debug entry points |
| `Download-Model-ChinaMirror.bat` | One-click model download via ModelScope mirror |
| `Build EXE.bat` | Package into a standalone .exe |

## 🔧 Troubleshooting

- Won't launch → read `launcher.log`
- AI won't chat → read `~/.cionote_ai.log` (connection & request errors, with real causes)
- Launcher exits with code 9009 → your `python` is the Microsoft Store placeholder; the launcher auto-skips it, otherwise install real Python with "Add to PATH" + "tcl/tk"

## 🤝 Contributing

Issues and PRs are welcome! For big changes, open an issue first to discuss. Areas that would love attention: Linux/macOS support, themes, export, tests.

## 📄 License

[MIT](LICENSE) © 2026 [CioGe](https://github.com/CioGe) — free to use, modify and distribute, including commercially. Just keep the copyright notice.

## ™️ Trademark Notice

**Obsidian** is a registered trademark of Dynalist Inc. **CioNote** is an independent open-source project and is **not affiliated with, endorsed by, sponsored by, or associated with Obsidian or Dynalist Inc.**

- CioNote contains no Obsidian source code, assets, or branding — it is written from scratch against the Python standard library.
- It simply reads and writes plain Markdown `.md` files in folders, following a note-taking workflow shared by that entire category of apps.
- Any mention of "Obsidian" in this repository is a descriptive comparison only (nominative fair use), not a claim of affiliation.

Third-party trademarks appearing in documentation (Obsidian, ModelScope, DeepSeek, Zhipu GLM, SiliconFlow, OpenAI, Ollama) belong to their respective owners and are referenced solely for compatibility and identification.

## ⭐ Share

If CioNote saves you time or money, a **Star** ⭐ helps others find it. Feel free to post it to your blog, newsletter or group chats.
