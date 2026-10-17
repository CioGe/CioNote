# -*- coding: utf-8 -*-
# CioNote - Local notes + AI assistant
# Copyright (c) 2026 CioGe
#
# Released under the MIT License. See the LICENSE file in the project root
# for the full text. In short: free to use, modify and distribute, including
# commercially -- just keep the copyright notice.
#
# 本软件以 MIT 许可证开源发布，全文见项目根目录下的 LICENSE 文件。
# 摘要：可自由使用、修改、分发（含商用），只需保留版权声明。
import os
import json
import time
import threading
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import html as html_lib
import re
import random
import math
import base64

# ========== 全局常量 ==========
OLLAMA_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:3b"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".cionote_config.json")
AI_LOG_FILE = os.path.join(os.path.expanduser("~"), ".cionote_ai.log")
# 旧版（PyNote 时期）的配置文件，仅用于一次性迁移，之后不再读取
_LEGACY_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".pynote_config.json")


def _migrate_legacy_config():
    """程序从 PyNote 改名 CioNote 后配置文件换了名字。

    首次运行时把旧配置原样搬过来，用户保存的语言、接口设置等不丢；
    新配置已存在（迁移过/用过新版）就不动。
    """
    if os.path.exists(CONFIG_FILE) or not os.path.exists(_LEGACY_CONFIG_FILE):
        return
    try:
        import shutil
        shutil.copy2(_LEGACY_CONFIG_FILE, CONFIG_FILE)
        _ai_log("已从旧配置 %s 迁移到 %s" % (_LEGACY_CONFIG_FILE, CONFIG_FILE))
    except Exception as e:
        _ai_log("迁移旧配置失败: %r" % (e,))


def _resolve_ollama_host():
    """解析 Ollama 服务地址，支持环境变量 OLLAMA_HOST 覆盖（可连远程/自定义端口）。

    环境变量常见写法有两种：`http://1.2.3.4:11434`（带协议）和 `0.0.0.0:11434`（不带协议），
    后者必须补上 http:// 才能被 urlopen 接受。
    """
    raw = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not raw:
        return OLLAMA_HOST
    if "://" not in raw:
        raw = "http://" + raw
    # 0.0.0.0 是监听地址，不是可连接地址，换成回环地址
    return raw.rstrip("/").replace("://0.0.0.0", "://127.0.0.1")


def _ai_log(msg):
    """把 AI 连接过程写到 ~/.cionote_ai.log，便于排查"检测不到模型/连不上"这类问题。

    日志写入失败绝不能影响主程序运行，所以这里静默吞掉所有异常。
    """
    try:
        line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
        with open(AI_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ========== 密钥本机加密存储 ==========
# Windows 下用系统自带的 DPAPI（CryptProtectData）先把 API Key 加密，再写入配置文件。
# 密文只能被「当前 Windows 账户 + 当前机器」解开，配置文件被拷到别的电脑或别的账户下
# 都无法还原。非 Windows 平台没有 DPAPI，此时密钥不落盘，只在本次运行的内存里有效，
# 以保证任何情况下都不会把明文密钥写进磁盘。
# 全部通过 ctypes 调用系统接口完成，不引入任何第三方依赖。
_SECRET_PREFIX = "dpapi1:"


def _dpapi_available():
    """当前环境是否支持 Windows DPAPI。"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        return hasattr(ctypes, "windll") and ctypes.windll.crypt32 is not None
    except Exception:
        return False


def _make_blob_class():
    """运行时构造 DATA_BLOB 结构体（仅 Windows 会走到这里）。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.c_void_p)]

    return DATA_BLOB


def _dpapi_call(func_name, raw):
    """调用 CryptProtectData / CryptUnprotectData，返回处理后的 bytes。"""
    import ctypes
    from ctypes import wintypes

    DATA_BLOB = _make_blob_class()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    fn = getattr(crypt32, func_name)
    fn.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                   ctypes.POINTER(DATA_BLOB)]
    fn.restype = wintypes.BOOL

    # LocalFree 必须声明参数类型，否则 64 位下指针会被当成 32 位整数截断
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = DATA_BLOB()
    # 0x01 = CRYPTPROTECT_UI_FORBIDDEN，禁止弹出任何系统交互界面
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x01,
            ctypes.byref(blob_out))
    if not ok:
        raise OSError(func_name + " failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _encrypt_secret(plain):
    """把明文密钥加密成可写进配置文件的字符串；无法安全存储时返回 None。"""
    if not plain:
        return None
    if not _dpapi_available():
        return None
    try:
        blob = _dpapi_call("CryptProtectData", plain.encode("utf-8"))
        return _SECRET_PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception:
        return None


def _decrypt_secret(stored):
    """把配置文件里的加密串还原成明文；任何异常都返回空串。"""
    if not stored or not isinstance(stored, str):
        return ""
    if not stored.startswith(_SECRET_PREFIX):
        return ""
    try:
        blob = base64.b64decode(stored[len(_SECRET_PREFIX):].encode("ascii"))
        return _dpapi_call("CryptUnprotectData", blob).decode("utf-8")
    except Exception:
        return ""


def _mask_secret(plain):
    """把密钥打码成 sk-****abcd 形式，仅用于界面提示。"""
    if not plain:
        return ""
    if len(plain) <= 8:
        return "*" * len(plain)
    return plain[:3] + "****" + plain[-4:]

# 多语言文本
I18N = {
    "en": {
        "app_title": "CioNote - Local Notes + AI Assistant",
        "menu_file": "File",
        "menu_edit": "Edit",
        "menu_view": "View",
        "menu_ai": "AI Assistant",
        "file_open_vault": "Open Vault (Ctrl+O)",
        "file_new_note": "New Note (Ctrl+N)",
        "file_new_folder": "New Folder",
        "file_import_md": "Import .md files",
        "file_save": "Save (Ctrl+S)",
        "file_exit": "Exit",
        "edit_bold": "Bold (Ctrl+B)",
        "edit_italic": "Italic (Ctrl+I)",
        "edit_wikilink": "Insert Wikilink (Ctrl+`)",
        "edit_delete_line": "Delete Line (Ctrl+D)",
        "edit_toggle_lang": "ENG-中文",
        "view_quick_switch": "Quick Switch Note (Ctrl+P)",
        "view_open_graph": "Open Knowledge Graph (Ctrl+G)",
        "view_open_ai": "Open AI Assistant (Ctrl+E)",
        "ai_check_ollama": "Check Ollama Connection",
        "ai_install_ollama": "Install Ollama Local LLM",
        "ai_select_model": "Select AI Model",
        "ai_model_label": "Model:",
        "ai_refresh_models": "Refresh",
        "route_label": "Route:",
        "route_local": "Local",
        "route_online": "Online",
        "msg_mode_local": "✅ Switched to local reasoning - messages now go to your local Ollama model.",
        "ai_download_model": "📥 Download model (China mirror)",
        "ai_api_settings": "⚙️ Online API settings...",
        "sidebar_vault": "📁 Vault",
        "btn_new_note": "➕ New Note",
        "btn_import_md": "📥 Import md",
        "menu_cut": "✂️ Cut",
        "menu_copy": "📋 Copy",
        "menu_paste": "📌 Paste",
        "menu_bold": "🅱️ Bold",
        "menu_italic": "ℹ️ Italic",
        "menu_wikilink": "🔗 Insert Wikilink",
        "menu_ask_ai": "🤖 Ask AI about Selection",
        "tree_new_note": "📝 New Note",
        "tree_new_folder": "📁 New Folder",
        "tree_rename": "✏️ Rename",
        "tree_delete": "🗑️ Delete",
        "tree_show_explorer": "📂 Show in Explorer",
        "tab_ai": "🤖 AI Assistant",
        "tab_backlinks": "🔗 Backlinks",
        "tab_graph": "🕸️ Knowledge Graph",
        "ollama_connected": "✅ Ollama Connected",
        "ollama_not_detected": "⚠️ Ollama Not Detected",
        "btn_install_guide": "Install Guide",
        "btn_clear_chat": "Clear Chat",
        "btn_send": "Send (Ctrl+Enter)",
        "btn_refresh_graph": "🔄 Refresh Graph",
        "status_ready": "Ready | Open vault to start (Ctrl+O)",
        "welcome_title": "# Welcome to CioNote 📝",
        "welcome_intro": "A lightweight local note-taking tool with built-in local AI assistant:\n\n## Quick Start\n- **Ctrl+O** Open vault (select an empty folder)\n- **Ctrl+N** Create new note\n- Type `[[NoteName]]` to create bidirectional links\n- **Ctrl+G** Open knowledge graph\n- **Ctrl+E** Open AI assistant chat panel\n\n## AI Assistant Setup\n1. Download and install Ollama from [ollama.com](https://ollama.com) (one-click install)\n2. Download the model via the ModelScope China mirror: `ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF` (~2GB, CPU is fine)\n3. Come back and start chatting with local AI - completely free, offline, private\n\n## Shortcuts\n- **Ctrl+B** Bold | **Ctrl+I** Italic | **Ctrl+`** Insert wikilink\n- **Ctrl+P** Quick switch note | **Ctrl+D** Delete current line\n- **Ctrl+Click** Click wikilink to jump directly",
        "ai_welcome": "Hello! I'm your local AI assistant.\n\n✅ Ollama installed and running? Just ask me anything here.\n🌐 In mainland China, `ollama pull qwen2.5:3b` often fails with \"lookup registry.ollama.ai: no such host\". Use the ModelScope China mirror instead: menu AI Assistant -> Download model (China mirror), it works without a proxy.\n❓ Not installed yet? Click \"Install Guide\" for free offline local AI.\n\n💡 You can also select text in the editor, right-click and choose \"Ask AI about Selection\".",
        "btn_ok": "OK",
        "btn_cancel": "Cancel",
        "prompt_new_note": "Note name:",
        "prompt_new_folder": "Folder name:",
        "prompt_rename": "New name:",
        "confirm_delete": "Are you sure you want to delete this item?",
        "error_open_file": "Failed to open file: {}",
        "error_save": "Save failed: {}",
        "msg_open_vault_first": "Please open a vault first (Ctrl+O)",
        "msg_saved": "Saved: {}",
        "msg_opened": "Opened: {}",
        "msg_imported": "Imported {} files",
        "msg_vault_opened": "Vault opened: {}",
        "status_sending": "Sending message to AI...",
        "error_ollama_not_running": "Ollama is not running. Please install and start Ollama first.",
        "error_ai_request": "AI request failed: {}",
        "ollama_guide_title": "Ollama Installation Guide",
        "ollama_guide_content": "To use local AI assistant:\n\n1. Download Ollama from https://ollama.com (Windows version, one-click install)\n2. After installation, open Command Prompt (cmd)\n3. In Command Prompt run the command below (uses the ModelScope (MoDa) China mirror - this bypasses registry.ollama.ai when it cannot be resolved):\n   ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF\n   (Downloads a ~2GB Chinese model, runs on CPU)\n4. After the download finishes, click \"Check Ollama Connection\" to start using\n\nIf you get \"dial tcp: lookup registry.ollama.ai: no such host\", the official registry is unreachable in your network - the China-mirror command above avoids it.\n\nModels on the China mirror:\n- modelscope.cn/Qwen/Qwen2.5-1.5B-Instruct-GGUF - smaller / faster\n- modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF - balanced (recommended)\n- modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF - better quality, needs ~5GB RAM\n\nOptional: append :Q3_K_M / :Q4_K_M / :Q5_K_M to pick quantization precision.",
        "prompt_select_model": "Enter model name (e.g. modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF):",
        "msg_model_selected": "Model changed to: {}",
        "msg_ollama_ok": "✅ Connected to Ollama\n\n{} model(s) installed, currently using:\n`{}`\n\nSwitch anytime from the Model dropdown, or hit ↻ to re-fetch the list.",
        "msg_ollama_fail": "❌ Cannot reach Ollama\n\nEndpoint: {}\nReason: {}\n\nChecklist:\n1. Make sure Ollama is running (tray icon, or `ollama list` returns something)\n2. Custom port / remote server? Set the OLLAMA_HOST environment variable\n3. A running proxy/VPN tool can hijack localhost traffic - try disabling it\n\nFull diagnostic log: `{}`",
        "msg_thinking": "Thinking...",
        "msg_no_response": "❌ No response received. Model in use: `{}`\n\nClick ↻ to refresh the model list, then pick one you actually have installed.",
        "msg_http_error": "❌ Ollama rejected the request (HTTP {})\n\nRequested model: `{}`\nServer says: {}\n\nThat exact model name is probably not installed. Click ↻ and pick from the Model dropdown - run `ollama list` in cmd to see the real names.",
        "msg_conn_error": "❌ Cannot reach Ollama: {}\n\nChecklist:\n1. Is Ollama running? Look for its tray icon, or run `ollama list`\n2. Custom port / remote host? Set the OLLAMA_HOST environment variable\n3. A proxy/VPN tool can hijack localhost traffic - try disabling it",
        "msg_request_error": "❌ Request failed: {}: {}\n\nDetails were written to the AI log file.",
    },
    "zh": {
        "app_title": "CioNote - 本地笔记+AI助手",
        "menu_file": "文件",
        "menu_edit": "编辑",
        "menu_view": "视图",
        "menu_ai": "AI助手",
        "file_open_vault": "打开笔记库 (Ctrl+O)",
        "file_new_note": "新建笔记 (Ctrl+N)",
        "file_new_folder": "新建文件夹",
        "file_import_md": "导入.md文件",
        "file_save": "保存 (Ctrl+S)",
        "file_exit": "退出",
        "edit_bold": "加粗 (Ctrl+B)",
        "edit_italic": "斜体 (Ctrl+I)",
        "edit_wikilink": "插入双链 (Ctrl+`)",
        "edit_delete_line": "删除当前行 (Ctrl+D)",
        "edit_toggle_lang": "ENG-中文",
        "view_quick_switch": "快速切换笔记 (Ctrl+P)",
        "view_open_graph": "打开知识图谱 (Ctrl+G)",
        "view_open_ai": "打开AI助手 (Ctrl+E)",
        "ai_check_ollama": "检测Ollama连接",
        "ai_install_ollama": "安装Ollama本地大模型",
        "ai_select_model": "选择AI模型",
        "ai_model_label": "模型:",
        "ai_refresh_models": "刷新",
        "route_label": "路由:",
        "route_local": "💻 本地",
        "route_online": "🌐 在线",
        "msg_mode_local": "✅ 已切换为本地推理，消息将发送给本机 Ollama 模型。",
        "ai_download_model": "📥 下载模型（国内源）",
        "ai_api_settings": "⚙️ 在线API设置...",
        "sidebar_vault": "📁 笔记库",
        "btn_new_note": "➕ 新建笔记",
        "btn_import_md": "📥 导入md",
        "menu_cut": "✂️ 剪切",
        "menu_copy": "📋 复制",
        "menu_paste": "📌 粘贴",
        "menu_bold": "🅱️ 加粗",
        "menu_italic": "ℹ️ 斜体",
        "menu_wikilink": "🔗 插入双链",
        "menu_ask_ai": "🤖 选中内容提问AI",
        "tree_new_note": "📝 新建笔记",
        "tree_new_folder": "📁 新建文件夹",
        "tree_rename": "✏️ 重命名",
        "tree_delete": "🗑️ 删除",
        "tree_show_explorer": "📂 在资源管理器中显示",
        "tab_ai": "🤖 AI助手",
        "tab_backlinks": "🔗 反向链接",
        "tab_graph": "🕸️ 知识图谱",
        "ollama_connected": "✅ Ollama已连接",
        "ollama_not_detected": "⚠️ 未检测到Ollama",
        "btn_install_guide": "安装指引",
        "btn_clear_chat": "清空对话",
        "btn_send": "发送 (Ctrl+Enter)",
        "btn_refresh_graph": "🔄 刷新图谱",
        "status_ready": "就绪 | 打开笔记库开始使用 (Ctrl+O)",
        "welcome_title": "# 欢迎使用 CioNote 📝",
        "welcome_intro": "这是一个轻量级本地笔记工具，自带本地AI助手：\n\n## 快速开始\n- **Ctrl+O** 打开笔记库（选一个空文件夹）\n- **Ctrl+N** 新建笔记\n- 输入 `[[笔记名]]` 创建双向链接\n- **Ctrl+G** 打开知识图谱\n- **Ctrl+E** 打开AI助手聊天面板\n\n## AI助手使用说明\n1. 去 [ollama.com](https://ollama.com) 下载安装Ollama（一键安装）\n2. 在命令提示符里用 ModelScope 国内源下载模型：`ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF`（约2GB，CPU 即可运行）\n3. 回到这里就能直接和本地AI聊天了，完全免费、离线、隐私安全\n\n## 快捷键\n- **Ctrl+B** 加粗 | **Ctrl+I** 斜体 | **Ctrl+`** 插入双链\n- **Ctrl+P** 快速切换笔记 | **Ctrl+D** 删除当前行\n- **Ctrl+Click** 点击双链直接跳转",
        "ai_welcome": "你好！我是本地AI助手。\n\n✅ 已安装并启动Ollama？直接在这里提问即可。\n🌐 国内网络下 `ollama pull qwen2.5:3b` 常报 “lookup registry.ollama.ai: no such host”，请改用 ModelScope 国内源：菜单「AI助手 → 下载模型（国内源）」一键下载，无需代理。\n❓ 还没装？点「安装指引」查看完全免费、离线、隐私安全的本地大模型方案。\n\n💡 也可以在编辑器里选中文字，右键「选中内容提问AI」。",
        "btn_ok": "确定",
        "btn_cancel": "取消",
        "prompt_new_note": "笔记名称:",
        "prompt_new_folder": "文件夹名称:",
        "prompt_rename": "新名称:",
        "confirm_delete": "确定要删除这个项目吗？",
        "error_open_file": "打开文件失败: {}",
        "error_save": "保存失败: {}",
        "msg_open_vault_first": "请先打开笔记库 (Ctrl+O)",
        "msg_saved": "已保存: {}",
        "msg_opened": "已打开: {}",
        "msg_imported": "已导入 {} 个文件",
        "msg_vault_opened": "已打开笔记库: {}",
        "status_sending": "正在发送消息给AI...",
        "error_ollama_not_running": "Ollama未运行，请先安装并启动Ollama。",
        "error_ai_request": "AI请求失败: {}",
        "ollama_guide_title": "Ollama安装指引",
        "ollama_guide_content": "使用本地AI助手步骤：\n\n1. 从 https://ollama.com 下载Windows版Ollama（一键安装）\n2. 安装完成后打开命令提示符（cmd）\n3. 在命令提示符里运行下面这条命令（用 ModelScope 魔搭国内源，官方支持，可绕开 registry.ollama.ai 无法解析的问题）：\n   ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF\n   （下载约2GB中文模型，CPU即可运行）\n4. 下载完成后点「检测Ollama连接」即可开始使用\n\n如果报错 “dial tcp: lookup registry.ollama.ai: no such host”，那是官方模型仓库在国内无法解析，用上面这条国内源命令即可绕过。\n\n国内源可选模型：\n- modelscope.cn/Qwen/Qwen2.5-1.5B-Instruct-GGUF - 更小更快\n- modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF - 均衡（推荐）\n- modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF - 效果更好，需要约5GB内存\n\n可选：在命令末尾加 :Q3_K_M / :Q4_K_M / :Q5_K_M 指定量化精度。",
        "prompt_select_model": "输入模型名称（如 modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF）:",
        "msg_model_selected": "模型已切换为: {}",
        "msg_ollama_ok": "✅ Ollama 已连接\n\n已安装 {} 个模型，当前使用：\n`{}`\n\n可在上方「模型」下拉框切换，或点 ↻ 重新拉取列表。",
        "msg_ollama_fail": "❌ 连不上 Ollama\n\n服务地址：{}\n失败原因：{}\n\n排查建议：\n1. 确认 Ollama 正在运行（任务栏应有图标，或运行 `ollama list` 能出结果）\n2. 若改过端口/连远程服务，设置环境变量 OLLAMA_HOST\n3. 若开着代理软件，可能被系统代理拦截，试试临时关闭\n\n完整诊断日志写在：`{}`",
        "msg_thinking": "思考中...",
        "msg_no_response": "❌ 未收到响应。当前使用的模型：`{}`\n\n建议点 ↻ 刷新模型列表，然后从「模型」下拉框里选一个本机确实装了的。",
        "msg_http_error": "❌ 请求被 Ollama 拒绝（HTTP {}）\n\n请求的模型名：`{}`\n服务返回：{}\n\n大概率是这个名字的模型本机没装。点 ↻ 刷新后从「模型」下拉框选；也可以在 cmd 里跑 `ollama list` 看真实名字。",
        "msg_conn_error": "❌ 连不上 Ollama：{}\n\n排查建议：\n1. 确认 Ollama 正在运行（任务栏应有图标，或运行 `ollama list` 能出结果）\n2. 若改过端口/连远程服务，设置环境变量 OLLAMA_HOST\n3. 若开着代理软件，可能被系统代理拦截，试试临时关闭",
        "msg_request_error": "❌ 请求出错：{}：{}\n\n详细信息已写入 AI 日志文件。",
    }
}

class CioNoteApp:
    def __init__(self, root):
        self.root = root
        
        # 在线API配置（OpenAI兼容接口），必须在 _load_config 之前初始化
        self.api_mode = "ollama"   # "ollama" 本地Ollama / "openai" 在线API
        self.api_base = ""
        self.api_key = ""
        self.api_model = ""
        
        # 加载语言配置
        self.lang = "en"  # 默认英文
        _migrate_legacy_config()
        self._load_config()
        # 旧版明文密钥在首次加载后立即迁移为本机加密存储
        if getattr(self, "api_key_was_plaintext", False):
            self._save_config()
        self.T = I18N[self.lang]
        
        self.root.title(self.T["app_title"])
        self.root.geometry("1280x800")
        self.root.minsize(800, 600)
        
        # 状态变量
        self.vault_path = None
        self.current_file = None
        self.current_content = ""
        self.backlinks = {}
        self.files_tree = {}
        self.is_preview_mode = False
        self.save_after_id = None
        self.preview_after_id = None
        self.chat_history = []
        self.ollama_running = False
        self.available_models = []
        self.current_chat_model = DEFAULT_MODEL
        # 最近一次连接失败的原因，用于在界面上给出可操作的提示而不是笼统的"未检测到"
        self.ollama_error = ""
        self.ollama_host = _resolve_ollama_host()
        
        # 保存UI组件引用，用于语言切换
        self.ui_refs = {}
        
        # 检测Ollama状态
        self._check_ollama_running()
        
        # 构建UI
        self._setup_styles()
        self._build_ui()
        self._build_shortcuts()
        self._update_status(self.T["status_ready"])
        
        # 欢迎提示
        self._show_welcome()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _load_config(self):
        """加载配置文件"""
        self.api_key_was_plaintext = False
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.lang = config.get("lang", "en")
                    self.api_mode = config.get("api_mode", "ollama")
                    self.api_base = config.get("api_base", "")
                    self.api_model = config.get("api_model", "")
                    # 优先读取本机加密存储的密钥
                    encrypted = config.get("api_key_enc", "")
                    if encrypted:
                        self.api_key = _decrypt_secret(encrypted)
                    elif config.get("api_key"):
                        # 兼容旧版明文密钥：先读进内存，随后自动迁移为加密存储
                        self.api_key = config.get("api_key", "")
                        self.api_key_was_plaintext = True
                    else:
                        self.api_key = ""
        except:
            self.lang = "en"
    
    def _save_config(self):
        """保存配置文件（API Key 以本机加密形式存储，绝不写入明文）"""
        try:
            config = {"lang": self.lang, "api_mode": self.api_mode,
                      "api_base": self.api_base, "api_model": self.api_model}
            if self.api_key:
                encrypted = _encrypt_secret(self.api_key)
                if encrypted:
                    config["api_key_enc"] = encrypted
                # 加密不可用（如非 Windows 平台）时留空：密钥只在本次运行的内存里有效
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f)
        except:
            pass
    
    def _toggle_language(self):
        """切换中英文语言"""
        self.lang = "zh" if self.lang == "en" else "en"
        self.T = I18N[self.lang]
        self._save_config()
        self._apply_language()
    
    def _apply_language(self):
        """应用当前语言到所有UI元素"""
        T = self.T
        # 窗口标题
        if self.current_file:
            self.root.title(f"CioNote - {os.path.basename(self.current_file)[:-3]}")
        else:
            self.root.title(T["app_title"])
        
        # 重建菜单
        self._build_menu()
        
        # 左侧侧边栏
        self.ui_refs["sidebar_vault_label"].config(text=T["sidebar_vault"])
        self.ui_refs["btn_new_note"].config(text=T["btn_new_note"])
        self.ui_refs["btn_import_md"].config(text=T["btn_import_md"])
        
        # 文件树右键菜单
        self.tree_menu.entryconfig(0, label=T["tree_new_note"])
        self.tree_menu.entryconfig(1, label=T["tree_new_folder"])
        self.tree_menu.entryconfig(3, label=T["tree_rename"])
        self.tree_menu.entryconfig(4, label=T["tree_delete"])
        self.tree_menu.entryconfig(6, label=T["tree_show_explorer"])
        
        # 编辑器右键菜单
        self.editor_menu.entryconfig(0, label=T["menu_cut"])
        self.editor_menu.entryconfig(1, label=T["menu_copy"])
        self.editor_menu.entryconfig(2, label=T["menu_paste"])
        self.editor_menu.entryconfig(4, label=T["menu_bold"])
        self.editor_menu.entryconfig(5, label=T["menu_italic"])
        self.editor_menu.entryconfig(6, label=T["menu_wikilink"])
        self.editor_menu.entryconfig(8, label=T["menu_ask_ai"])
        
        # 右侧Notebook标签
        self.right_notebook.tab(0, text=T["tab_ai"])
        self.right_notebook.tab(1, text=T["tab_backlinks"])
        self.right_notebook.tab(2, text=T["tab_graph"])
        
        # AI面板
        self.ui_refs["btn_install_guide"].config(text=T["btn_install_guide"])
        self.ui_refs["btn_clear_chat"].config(text=T["btn_clear_chat"])
        self.ui_refs["btn_send"].config(text=T["btn_send"])
        self.ui_refs["btn_refresh_graph"].config(text=T["btn_refresh_graph"])
        lbl_model = self.ui_refs.get("lbl_model")
        if lbl_model:
            lbl_model.config(text=T["ai_model_label"])
        lbl_route = self.ui_refs.get("lbl_route")
        if lbl_route:
            lbl_route.config(text=T["route_label"])
        bl = self.ui_refs.get("btn_mode_local")
        if bl:
            bl.config(text=T["route_local"])
        bo = self.ui_refs.get("btn_mode_online")
        if bo:
            bo.config(text=T["route_online"])
        
        # 更新Ollama状态文字
        if self.ollama_running:
            self.chat_status_label.config(text=T["ollama_connected"])
        else:
            self.chat_status_label.config(text=T["ollama_not_detected"])
        
        # 更新欢迎消息（如果是欢迎页）
        content = self.editor.get("1.0", tk.END).strip()
        if "Welcome to CioNote" in content or "欢迎使用 CioNote" in content:
            self.editor.delete("1.0", tk.END)
            self._show_welcome()
        
        # 更新AI欢迎消息
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        self._add_chat_message("assistant", T["ai_welcome"])
        self.chat_display.config(state=tk.DISABLED)
    
    def _on_close(self):
        """关闭程序时保存配置"""
        self._save_config()
        self.root.quit()
    
    def _check_ollama_running(self):
        """检测本地Ollama是否运行，并记录已安装的模型列表。

        ⚠️ 这里踩过一个坑：urllib.request.Request() 的构造函数**不接受 timeout 参数**，
        写成 Request(url, timeout=2) 会直接抛 TypeError。因为外面套着裸 except，
        异常被静默吞掉，结果就是 Ollama 明明正常、模型也拉好了，界面却永远显示"未检测到Ollama"。
        超时只能传给 urlopen()。下面保留明确的异常分类捕获，失败原因写进 self.ollama_error + 日志。
        """
        self.ollama_host = _resolve_ollama_host()
        try:
            req = urllib.request.Request(self.ollama_host + "/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    self.ollama_running = True
                    self.ollama_error = ""
                    try:
                        body = resp.read().decode("utf-8", "ignore")
                        data = json.loads(body)
                        self.available_models = [
                            m.get("name", "") for m in data.get("models", []) if m.get("name")
                        ]
                    except Exception as e:
                        self.available_models = []
                        _ai_log("解析模型列表失败: %r" % (e,))
                    _ai_log("已连接 %s，本地模型: %s" % (self.ollama_host, self.available_models))
                    self._pick_best_model()
                    return
        except urllib.error.HTTPError as e:
            self.ollama_error = "HTTP %s" % e.code
            _ai_log("Ollama 返回错误: HTTP %s" % e.code)
        except urllib.error.URLError as e:
            self.ollama_error = str(e.reason) if e.reason else str(e)
            _ai_log("连接 Ollama 失败(%s): %s" % (self.ollama_host, self.ollama_error))
        except Exception as e:
            # 兜底：任何其它异常都要留下线索，不能再 silent pass
            self.ollama_error = "%s: %s" % (type(e).__name__, e)
            _ai_log("检测 Ollama 时异常: %r" % (e,))
        self.ollama_running = False
    
    def _pick_best_model(self):
        """若当前模型不在已安装列表里，自动换一个可用的（优先中文模型）"""
        models = getattr(self, "available_models", [])
        if not models:
            return
        if self.current_chat_model in models:
            return
        def _base(n):
            return n.split(":")[0]
        cur_base = _base(self.current_chat_model)
        for m in models:
            if _base(m) == cur_base:
                self.current_chat_model = m
                return
        for m in models:
            if "qwen" in m.lower():
                self.current_chat_model = m
                return
        self.current_chat_model = models[0]

    def _set_api_mode(self, mode):
        """切换消息路由：本地 Ollama / 在线接口。"""
        if mode == self.api_mode:
            return
        if mode == "openai":
            # 切到在线前必须先有 Base URL/Key/模型，直接打开设置面板让用户填
            self._open_api_settings()
            return
        self.api_mode = "ollama"
        self._save_config()
        self._refresh_route_buttons()
        self._refresh_ollama_status()
        self._add_chat_message("assistant", self.T["msg_mode_local"])

    def _refresh_route_buttons(self):
        """让"本地/在线"两个按钮的高亮反映当前真实路由"""
        try:
            local = self.ui_refs.get("btn_mode_local")
            online = self.ui_refs.get("btn_mode_online")
            if not (local and online):
                return
            is_local = self.api_mode != "openai"
            local.configure(style="TButton" if is_local else "Secondary.TButton")
            online.configure(style="Secondary.TButton" if is_local else "TButton")
        except Exception as e:
            _ai_log("刷新路由按钮失败: %r" % (e,))

    def _sync_model_combo(self):
        """把本机已安装的模型列表同步到下拉框，并回显当前选中的模型"""
        try:
            combo = self.ui_refs.get("model_combo") or getattr(self, "model_combo", None)
            if combo is None:
                return
            if self.api_mode == "openai":
                # 在线模式下这里显示的是在线模型名，列表不参与本地推理
                combo.configure(values=[self.api_model] if self.api_model else [])
                self.model_var.set(self.api_model)
            else:
                combo.configure(values=self.available_models)
                self.model_var.set(self.current_chat_model)
        except Exception as e:
            _ai_log("同步模型下拉框失败: %r" % (e,))

    def _on_model_selected(self, event=None):
        """下拉框选中模型"""
        name = (self.model_var.get() or "").strip()
        if not name or name == self.current_chat_model:
            return
        self.current_chat_model = name
        _ai_log("手动切换模型 -> %s" % name)
        self._add_chat_message("assistant", self.T["msg_model_selected"].format(name))
    
    def _setup_styles(self):
        """设置浅色主题样式"""
        self.style = ttk.Style()
        self.style.theme_use("clam")
        
        # 全局配色：白底黑字
        bg_main = "#ffffff"
        bg_side = "#f8f9fa"
        text_main = "#1a1a1a"
        accent = "#2563eb"
        
        self.style.configure(".", background=bg_main, foreground=text_main, font=("Microsoft YaHei UI", 10))
        self.style.configure("TFrame", background=bg_side)
        self.style.configure("Main.TFrame", background=bg_main)
        self.style.configure("TLabel", background=bg_side, foreground=text_main)
        self.style.configure("Main.TLabel", background=bg_main, foreground=text_main)
        self.style.configure("TButton", padding=6, relief="flat", background=accent, foreground="white")
        self.style.map("TButton", background=[("active", "#1d4ed8")])
        self.style.configure("Secondary.TButton", background="#e5e7eb", foreground=text_main)
        self.style.map("Secondary.TButton", background=[("active", "#d1d5db")])
        self.style.configure("TNotebook", background=bg_side, borderwidth=0)
        self.style.configure("TNotebook.Tab", padding=[12, 6], background="#e5e7eb", foreground=text_main)
        self.style.map("TNotebook.Tab", background=[("selected", bg_main)])
        self.style.configure("Treeview", background=bg_main, foreground=text_main, fieldbackground=bg_main, borderwidth=0)
        self.style.map("Treeview", background=[("selected", accent)], foreground=[("selected", "white")])
        self.style.configure("TEntry", fieldbackground=bg_main, foreground=text_main, borderwidth=1)
        self.style.configure("Status.TLabel", background="#f1f5f9", foreground="#64748b", padding=4)
        
        # 配置根窗口背景
        self.root.configure(bg=bg_main)
    
    def _build_ui(self):
        """构建主界面"""
        # 菜单栏
        self._build_menu()
        
        # 主Paned窗口
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        
        # ========== 左侧：文件树 ==========
        left_frame = ttk.Frame(main_paned, width=240)
        main_paned.add(left_frame, weight=1)
        
        sidebar_label = ttk.Label(left_frame, text=self.T["sidebar_vault"], font=("Microsoft YaHei UI", 11, "bold"))
        sidebar_label.pack(anchor=tk.W, padx=8, pady=(8,4))
        self.ui_refs["sidebar_vault_label"] = sidebar_label
        
        # 文件树
        self.tree = ttk.Treeview(left_frame, show="tree", selectmode="browse")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        
        # 文件树右键菜单
        self.tree_menu = tk.Menu(self.root, tearoff=0)
        self.tree_menu.add_command(label=self.T["tree_new_note"], command=self._new_note)
        self.tree_menu.add_command(label=self.T["tree_new_folder"], command=self._new_folder)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label=self.T["tree_rename"], command=self._rename_selected)
        self.tree_menu.add_command(label=self.T["tree_delete"], command=self._delete_selected)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label=self.T["tree_show_explorer"], command=self._show_in_explorer)
        self.tree.bind("<Button-3>", self._show_tree_menu)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        
        # 左侧按钮
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, padx=8, pady=(4,8))
        btn_new = ttk.Button(btn_frame, text=self.T["btn_new_note"], command=self._new_note)
        btn_new.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        self.ui_refs["btn_new_note"] = btn_new
        btn_import = ttk.Button(btn_frame, text=self.T["btn_import_md"], command=self._import_markdown, style="Secondary.TButton")
        btn_import.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        self.ui_refs["btn_import_md"] = btn_import
        
        # ========== 中间：编辑器 ==========
        middle_frame = ttk.Frame(main_paned, width=700)
        main_paned.add(middle_frame, weight=4)
        
        # 编辑区（纯文本编辑器，WYSIWYG实时预览）
        self.editor = tk.Text(
            middle_frame, wrap=tk.WORD, font=("Microsoft YaHei UI", 11),
            bg="#ffffff", fg="#1a1a1a", insertbackground="#1a1a1a",
            padx=16, pady=16, relief=tk.FLAT, borderwidth=0,
            spacing1=2, spacing3=2
        )
        self.editor.pack(fill=tk.BOTH, expand=True)
        
        # 配置Markdown高亮标签
        self._setup_editor_tags()
        
        # 编辑器右键菜单
        self.editor_menu = tk.Menu(self.root, tearoff=0)
        self.editor_menu.add_command(label=self.T["menu_cut"], command=lambda: self.editor.event_generate("<<Cut>>"))
        self.editor_menu.add_command(label=self.T["menu_copy"], command=lambda: self.editor.event_generate("<<Copy>>"))
        self.editor_menu.add_command(label=self.T["menu_paste"], command=lambda: self.editor.event_generate("<<Paste>>"))
        self.editor_menu.add_separator()
        self.editor_menu.add_command(label=self.T["menu_bold"], command=self._insert_bold)
        self.editor_menu.add_command(label=self.T["menu_italic"], command=self._insert_italic)
        self.editor_menu.add_command(label=self.T["menu_wikilink"], command=self._insert_wikilink)
        self.editor_menu.add_separator()
        self.editor_menu.add_command(label=self.T["menu_ask_ai"], command=self._ask_ai_selected)
        self.editor.bind("<Button-3>", self._show_editor_menu)
        
        # 编辑器事件绑定
        self.editor.bind("<KeyRelease>", self._on_editor_key)
        self.editor.bind("<Return>", self._on_enter)
        self.editor.bind("<Control-Button-1>", self._on_ctrl_click)
        self.editor.bind("<Button-1>", lambda e: self._switch_to_edit_mode())
        
        # 滚动条
        scroll = ttk.Scrollbar(self.editor, command=self.editor.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.editor.configure(yscrollcommand=scroll.set)
        
        # ========== 右侧面板 ==========
        right_frame = ttk.Frame(main_paned, width=320)
        main_paned.add(right_frame, weight=2)
        
        self.right_notebook = ttk.Notebook(right_frame)
        self.right_notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        
        # --- AI助手聊天面板（第一个标签） ---
        chat_frame = ttk.Frame(self.right_notebook)
        self.right_notebook.add(chat_frame, text=self.T["tab_ai"])
        
        # 聊天状态区
        chat_status_frame = ttk.Frame(chat_frame)
        chat_status_frame.pack(fill=tk.X, padx=4, pady=4)
        
        if self.ollama_running:
            self.chat_status_label = ttk.Label(chat_status_frame, text=self.T["ollama_connected"], foreground="#16a34a")
        else:
            self.chat_status_label = ttk.Label(chat_status_frame, text=self.T["ollama_not_detected"], foreground="#dc2626")
        self.chat_status_label.pack(side=tk.LEFT)
        
        btn_guide = ttk.Button(chat_status_frame, text=self.T["btn_install_guide"], style="Secondary.TButton", command=self._show_ollama_guide, width=8)
        btn_guide.pack(side=tk.RIGHT, padx=2)
        self.ui_refs["btn_install_guide"] = btn_guide
        btn_clear = ttk.Button(chat_status_frame, text=self.T["btn_clear_chat"], style="Secondary.TButton", command=self._clear_chat, width=8)
        btn_clear.pack(side=tk.RIGHT, padx=2)
        self.ui_refs["btn_clear_chat"] = btn_clear

        # 模型选择行：直接从本机已装列表里挑，不用手打
        # （国内源拉下来的模型名往往很长，例如 modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF:latest，
        #   靠手输几乎不可能输对，这行下拉框就是为了省掉这一步）
        model_row = ttk.Frame(chat_frame)
        model_row.pack(fill=tk.X, padx=4, pady=(0, 4))

        lbl_model = ttk.Label(model_row, text=self.T["ai_model_label"])
        lbl_model.pack(side=tk.LEFT)
        self.ui_refs["lbl_model"] = lbl_model

        self.model_var = tk.StringVar(value=self.current_chat_model)
        self.model_combo = ttk.Combobox(
            model_row, textvariable=self.model_var,
            values=self.available_models, state="normal", width=18
        )
        self.model_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 2))
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)

        btn_refresh_models = ttk.Button(
            model_row, text="↻", width=3, style="Secondary.TButton",
            command=self._refresh_ollama_status
        )
        btn_refresh_models.pack(side=tk.RIGHT)
        self.ui_refs["btn_refresh_models"] = btn_refresh_models

        # 路由指示条：明确显示当前消息发给谁，并可一键切换本地/在线
        # 之前没有这个提示，出现过"Ollama 显示已连接、实际消息却发给了在线接口"的误判
        mode_row = ttk.Frame(chat_frame)
        mode_row.pack(fill=tk.X, padx=4, pady=(0, 4))

        lbl_route = ttk.Label(mode_row, text=self.T["route_label"])
        lbl_route.pack(side=tk.LEFT)
        self.ui_refs["lbl_route"] = lbl_route

        btn_local = ttk.Button(mode_row, text=self.T["route_local"], width=8,
                               command=lambda: self._set_api_mode("ollama"))
        btn_local.pack(side=tk.LEFT, padx=(6, 2))
        self.ui_refs["btn_mode_local"] = btn_local

        btn_online = ttk.Button(mode_row, text=self.T["route_online"], width=8,
                                command=lambda: self._set_api_mode("openai"))
        btn_online.pack(side=tk.LEFT, padx=2)
        self.ui_refs["btn_mode_online"] = btn_online

        self._refresh_route_buttons()

        # 聊天记录区
        self.chat_display = tk.Text(
            chat_frame, wrap=tk.WORD, font=("Microsoft YaHei UI", 10),
            bg="#ffffff", fg="#1a1a1a", padx=8, pady=8, relief=tk.FLAT, borderwidth=0,
            state=tk.DISABLED, spacing1=3, spacing3=3
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._setup_chat_tags()
        
        chat_scroll = ttk.Scrollbar(self.chat_display, command=self.chat_display.yview)
        chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_display.configure(yscrollcommand=chat_scroll.set)
        
        # 输入框区域
        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(fill=tk.X, padx=4, pady=4)
        
        self.chat_input = tk.Text(
            input_frame, height=3, wrap=tk.WORD, font=("Microsoft YaHei UI", 10),
            bg="#ffffff", fg="#1a1a1a", padx=8, pady=6, relief=tk.FLAT, borderwidth=1
        )
        self.chat_input.pack(fill=tk.X, pady=(0,4))
        self.chat_input.bind("<Control-Return>", lambda e: self._send_chat_message())
        
        btn_send = ttk.Button(input_frame, text=self.T["btn_send"], command=self._send_chat_message)
        btn_send.pack(fill=tk.X)
        self.ui_refs["btn_send"] = btn_send
        
        # 欢迎消息
        self._add_chat_message("assistant", self.T["ai_welcome"])
        
        # --- 反向链接面板（第二个标签） ---
        backlinks_frame = ttk.Frame(self.right_notebook)
        self.right_notebook.add(backlinks_frame, text=self.T["tab_backlinks"])
        
        self.backlinks_list = tk.Listbox(
            backlinks_frame, bg="#ffffff", fg="#1a1a1a", font=("Microsoft YaHei UI", 10),
            relief=tk.FLAT, borderwidth=0, highlightthickness=0, selectbackground="#2563eb"
        )
        self.backlinks_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.backlinks_list.bind("<Double-Button-1>", self._open_backlink)
        
        # --- 知识图谱面板（第三个标签） ---
        graph_frame = ttk.Frame(self.right_notebook)
        self.right_notebook.add(graph_frame, text=self.T["tab_graph"])
        
        self.graph_canvas = tk.Canvas(graph_frame, bg="#ffffff", relief=tk.FLAT, borderwidth=0)
        self.graph_canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        
        btn_refresh_graph = ttk.Button(graph_frame, text=self.T["btn_refresh_graph"], command=self._build_graph, style="Secondary.TButton")
        btn_refresh_graph.pack(fill=tk.X, padx=4, pady=4)
        self.ui_refs["btn_refresh_graph"] = btn_refresh_graph
        
        # 图谱交互变量
        self.graph_nodes = {}
        self.graph_edges = []
        self.graph_dragging = None
        self.graph_pan_x = 0
        self.graph_pan_y = 0
        self.graph_scale = 1.0
        self.graph_panning = False
        self.graph_canvas.bind("<MouseWheel>", self._graph_zoom)
        self.graph_canvas.bind("<Button-3>", self._graph_pan_start)
        self.graph_canvas.bind("<B3-Motion>", self._graph_pan_move)
        self.graph_canvas.bind("<ButtonPress-1>", self._graph_node_press)
        self.graph_canvas.bind("<B1-Motion>", self._graph_node_drag)
        self.graph_canvas.bind("<Double-Button-1>", self._graph_node_open)
        
        # ========== 底部状态栏 ==========
        self.status = ttk.Label(self.root, text="", style="Status.TLabel", anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
    
    def _build_menu(self):
        """构建菜单栏"""
        T = self.T
        menubar = tk.Menu(self.root)
        
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label=T["file_open_vault"], command=self._open_vault)
        file_menu.add_separator()
        file_menu.add_command(label=T["file_new_note"], command=self._new_note)
        file_menu.add_command(label=T["file_new_folder"], command=self._new_folder)
        file_menu.add_command(label=T["file_import_md"], command=self._import_markdown)
        file_menu.add_separator()
        file_menu.add_command(label=T["file_save"], command=self._save_current)
        file_menu.add_separator()
        file_menu.add_command(label=T["file_exit"], command=self._on_close)
        menubar.add_cascade(label=T["menu_file"], menu=file_menu)
        
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label=T["edit_bold"], command=self._insert_bold)
        edit_menu.add_command(label=T["edit_italic"], command=self._insert_italic)
        edit_menu.add_command(label=T["edit_wikilink"], command=self._insert_wikilink)
        edit_menu.add_separator()
        edit_menu.add_command(label=T["edit_delete_line"], command=self._delete_line)
        edit_menu.add_separator()
        edit_menu.add_command(label=T["edit_toggle_lang"], command=self._toggle_language)
        menubar.add_cascade(label=T["menu_edit"], menu=edit_menu)
        
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label=T["view_quick_switch"], command=self._quick_switch)
        view_menu.add_command(label=T["view_open_graph"], command=lambda: self.right_notebook.select(2))
        view_menu.add_command(label=T["view_open_ai"], command=lambda: self.right_notebook.select(0))
        menubar.add_cascade(label=T["menu_view"], menu=view_menu)
        
        ai_menu = tk.Menu(menubar, tearoff=0)
        ai_menu.add_command(label=T["ai_check_ollama"], command=self._refresh_ollama_status)
        ai_menu.add_command(label=T["ai_install_ollama"], command=self._show_ollama_guide)
        ai_menu.add_command(label=T["ai_download_model"], command=self._download_model_mirror)
        ai_menu.add_separator()
        ai_menu.add_command(label=T["ai_select_model"], command=self._select_model)
        ai_menu.add_command(label=T["ai_api_settings"], command=self._open_api_settings)
        menubar.add_cascade(label=T["menu_ai"], menu=ai_menu)
        
        self.root.config(menu=menubar)
    
    def _build_shortcuts(self):
        """绑定快捷键，全部标准大写Control，没有小写，没有非标准事件"""
        self.root.bind("<Control-n>", lambda e: self._new_note())
        self.root.bind("<Control-o>", lambda e: self._open_vault())
        self.root.bind("<Control-s>", lambda e: self._save_current())
        self.root.bind("<Control-p>", lambda e: self._quick_switch())
        self.root.bind("<Control-b>", lambda e: self._insert_bold())
        self.root.bind("<Control-i>", lambda e: self._insert_italic())
        self.root.bind("<Control-g>", lambda e: self.right_notebook.select(2))
        self.root.bind("<Control-e>", lambda e: self.right_notebook.select(0))
        self.root.bind("<Control-d>", lambda e: self._delete_line())
        self.root.bind("<Control-question>", lambda e: self._insert_wikilink())  # Ctrl+`
    
    def _setup_editor_tags(self):
        """配置编辑器Markdown高亮标签"""
        self.editor.tag_configure("h1", foreground="#2563eb", font=("Microsoft YaHei UI", 20, "bold"), spacing1=12, spacing3=8)
        self.editor.tag_configure("h2", foreground="#2563eb", font=("Microsoft YaHei UI", 18, "bold"), spacing1=10, spacing3=6)
        self.editor.tag_configure("h3", foreground="#2563eb", font=("Microsoft YaHei UI", 15, "bold"), spacing1=8, spacing3=4)
        self.editor.tag_configure("h4", foreground="#2563eb", font=("Microsoft YaHei UI", 13, "bold"), spacing1=6, spacing3=2)
        self.editor.tag_configure("h5", foreground="#2563eb", font=("Microsoft YaHei UI", 12, "bold"))
        self.editor.tag_configure("h6", foreground="#2563eb", font=("Microsoft YaHei UI", 11, "bold"))
        self.editor.tag_configure("bold", font=("Microsoft YaHei UI", 11, "bold"))
        self.editor.tag_configure("italic", font=("Microsoft YaHei UI", 11, "italic"))
        self.editor.tag_configure("code", foreground="#dc2626", background="#f3f4f6", font=("Consolas", 10))
        self.editor.tag_configure("wikilink", foreground="#7c3aed", font=("Microsoft YaHei UI", 11, "bold", "underline"))
        self.editor.tag_configure("list", foreground="#f97316", font=("Microsoft YaHei UI", 11, "bold"), lmargin1=18, lmargin2=18)
        self.editor.tag_configure("quote", foreground="#6b7280", font=("Microsoft YaHei UI", 11, "italic"), lmargin1=20, lmargin2=20)
        self.editor.tag_configure("highlight", background="#fef08a")
        # 预览模式下用于隐藏Markdown标记的标签：前景色与编辑区背景色完全一致
        # 注意：只改变“看不见”，文字本身依然存在，绝不改动文本内容，因此格式永远不会丢失
        self.editor.tag_configure("mdmark", foreground="#ffffff")
        # 确保 mdmark 拥有最高优先级，这样它盖过标题/粗体等标签的颜色，标记才能真正“隐形”
        self.editor.tag_raise("mdmark")
    
    def _setup_chat_tags(self):
        """配置聊天区标签"""
        self.chat_display.tag_configure("user", foreground="#1a1a1a", font=("Microsoft YaHei UI", 10, "bold"), spacing1=8, lmargin1=40, rmargin=8)
        self.chat_display.tag_configure("assistant", foreground="#1a1a1a", font=("Microsoft YaHei UI", 10), spacing1=8, lmargin1=8, rmargin=40)
        self.chat_display.tag_configure("user_label", foreground="#2563eb", font=("Microsoft YaHei UI", 9, "bold"), lmargin1=40)
        self.chat_display.tag_configure("assistant_label", foreground="#16a34a", font=("Microsoft YaHei UI", 9, "bold"), lmargin1=8)
        self.chat_display.tag_configure("code", foreground="#dc2626", background="#f3f4f6", font=("Consolas", 9))
        self.chat_display.tag_configure("bold", font=("Microsoft YaHei UI", 10, "bold"))
        self.chat_display.tag_configure("error", foreground="#dc2626", font=("Microsoft YaHei UI", 10, "italic"))
    
    def _show_welcome(self):
        """显示欢迎信息"""
        T = self.T
        welcome = T["welcome_intro"]
        self.editor.insert("1.0", welcome)
        self.is_preview_mode = False
        self.root.after(100, self._switch_to_preview_mode)
    
    def _update_status(self, text, timeout=0):
        """更新状态栏"""
        self.status.config(text=text)
        if timeout > 0:
            T = self.T
            self.root.after(timeout, lambda: self.status.config(text=T["status_ready"]))
    
    # ==================== Vault和文件操作 ====================
    def _open_vault(self):
        """打开笔记库"""
        T = self.T
        path = filedialog.askdirectory()
        if not path:
            return
        self.vault_path = path
        self._load_vault_tree()
        self._build_backlink_index()
        self._build_graph()
        self._update_status(T["msg_vault_opened"].format(path))
    
    def _load_vault_tree(self):
        """加载文件树"""
        self.tree.delete(*self.tree.get_children())
        self.files_tree = {}
        if not self.vault_path:
            return
        
        def add_path(parent, full_path):
            for item in sorted(os.listdir(full_path)):
                item_path = os.path.join(full_path, item)
                if os.path.isdir(item_path):
                    if item.startswith("."):
                        continue
                    node = self.tree.insert(parent, "end", text=f"📁 {item}", open=False)
                    self.files_tree[node] = item_path
                    add_path(node, item_path)
                elif item.endswith(".md"):
                    name = item[:-3]
                    node = self.tree.insert(parent, "end", text=f"📝 {name}")
                    self.files_tree[node] = item_path
        
        add_path("", self.vault_path)
    
    def _build_backlink_index(self):
        """构建反向链接索引"""
        self.backlinks = {}
        if not self.vault_path:
            return
        
        # 先收集所有笔记名
        all_notes = set()
        for root, dirs, files in os.walk(self.vault_path):
            for f in files:
                if f.endswith(".md"):
                    all_notes.add(f[:-3])
        
        # 遍历所有文件找双链
        for note_name in all_notes:
            self.backlinks[note_name] = set()
        
        for root, dirs, files in os.walk(self.vault_path):
            for f in files:
                if f.endswith(".md"):
                    source = f[:-3]
                    try:
                        with open(os.path.join(root, f), "r", encoding="utf-8") as fp:
                            content = fp.read()
                        links = re.findall(r"\[\[([^\]]+)\]\]", content)
                        for link in links:
                            link = link.split("|")[0].strip()
                            if link in all_notes:
                                self.backlinks[link].add(source)
                    except:
                        pass
    
    def _on_tree_select(self, event):
        """选中文件树节点"""
        selection = self.tree.selection()
        if not selection:
            return
        path = self.files_tree.get(selection[0])
        if not path or not os.path.isfile(path) or not path.endswith(".md"):
            return
        self._open_file(path)
    
    def _open_file(self, path):
        """打开文件"""
        T = self.T
        if self.current_file and self.editor.get("1.0", tk.END).strip() != self.current_content.strip():
            self._save_current()
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.editor.delete("1.0", tk.END)
            self.editor.insert("1.0", content)
            self.current_file = path
            self.current_content = content
            self.root.title(f"CioNote - {os.path.basename(path)[:-3]}")
            self.is_preview_mode = False
            self.root.after(100, self._switch_to_preview_mode)
            self._update_backlinks_panel()
            self._update_status(T["msg_opened"].format(os.path.basename(path)))
        except Exception as e:
            messagebox.showerror("Error", T["error_open_file"].format(str(e)))
    
    def _save_current(self):
        """保存当前文件"""
        T = self.T
        if not self.current_file:
            if not self.vault_path:
                messagebox.showinfo("Info", T["msg_open_vault_first"])
                return
            name = simpledialog.askstring("New Note", T["prompt_new_note"])
            if not name:
                return
            if not name.endswith(".md"):
                name += ".md"
            self.current_file = os.path.join(self.vault_path, name)
        
        content = self.editor.get("1.0", tk.END)
        try:
            with open(self.current_file, "w", encoding="utf-8") as f:
                f.write(content)
            self.current_content = content
            self._update_status(T["msg_saved"].format(os.path.basename(self.current_file)), 2000)
            self._load_vault_tree()
            self._build_backlink_index()
            self._build_graph()
            self._update_backlinks_panel()
        except Exception as e:
            messagebox.showerror("Error", T["error_save"].format(str(e)))
    
    def _new_note(self):
        """新建笔记"""
        T = self.T
        if not self.vault_path:
            messagebox.showinfo("Info", T["msg_open_vault_first"])
            return
        name = simpledialog.askstring("New Note", T["prompt_new_note"])
        if not name:
            return
        if not name.endswith(".md"):
            name += ".md"
        path = os.path.join(self.vault_path, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {name[:-3]}\n\n")
        self._load_vault_tree()
        self._open_file(path)
        self._build_backlink_index()
        self._build_graph()
    
    def _new_folder(self):
        """新建文件夹"""
        T = self.T
        if not self.vault_path:
            messagebox.showinfo("Info", T["msg_open_vault_first"])
            return
        name = simpledialog.askstring("New Folder", T["prompt_new_folder"])
        if not name:
            return
        path = os.path.join(self.vault_path, name)
        os.makedirs(path, exist_ok=True)
        self._load_vault_tree()
    
    def _rename_selected(self):
        """重命名选中项"""
        T = self.T
        selection = self.tree.selection()
        if not selection:
            return
        path = self.files_tree.get(selection[0])
        if not path:
            return
        old_name = os.path.basename(path)
        new_name = simpledialog.askstring("Rename", T["prompt_rename"], initialvalue=old_name)
        if not new_name or new_name == old_name:
            return
        new_path = os.path.join(os.path.dirname(path), new_name)
        try:
            os.rename(path, new_path)
            self._load_vault_tree()
            self._build_backlink_index()
            self._build_graph()
        except Exception as e:
            messagebox.showerror("Error", f"Rename failed: {str(e)}")
    
    def _delete_selected(self):
        """删除选中项"""
        T = self.T
        selection = self.tree.selection()
        if not selection:
            return
        path = self.files_tree.get(selection[0])
        if not path:
            return
        if not messagebox.askyesno("Confirm Delete", T["confirm_delete"] + f"\n{os.path.basename(path)}"):
            return
        try:
            if os.path.isfile(path):
                os.remove(path)
            else:
                import shutil
                shutil.rmtree(path)
            self._load_vault_tree()
            self._build_backlink_index()
            self._build_graph()
        except Exception as e:
            messagebox.showerror("Error", f"Delete failed: {str(e)}")
    
    def _show_in_explorer(self):
        """在资源管理器中显示"""
        selection = self.tree.selection()
        if not selection:
            return
        path = self.files_tree.get(selection[0])
        if path:
            os.startfile(os.path.dirname(path))
    
    def _import_markdown(self):
        """导入md文件"""
        T = self.T
        if not self.vault_path:
            messagebox.showinfo("Info", T["msg_open_vault_first"])
            return
        files = filedialog.askopenfilenames(filetypes=[("Markdown files", "*.md")])
        if not files:
            return
        count = 0
        import shutil
        for f in files:
            try:
                shutil.copy2(f, os.path.join(self.vault_path, os.path.basename(f)))
                count += 1
            except:
                pass
        self._load_vault_tree()
        self._build_backlink_index()
        self._build_graph()
        self._update_status(T["msg_imported"].format(count), timeout=2000)
    
    def _show_tree_menu(self, event):
        """显示文件树右键菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
        self.tree_menu.tk_popup(event.x_root, event.y_root)
    
    # ==================== 编辑器功能 ====================
    def _switch_to_edit_mode(self):
        """切回编辑模式：把Markdown标记显示出来（只改标签，绝不改动文本内容）"""
        if not self.is_preview_mode:
            return
        self.is_preview_mode = False
        self._apply_markdown_highlighting()

    def _switch_to_preview_mode(self):
        """进入预览模式：隐藏Markdown标记，只保留渲染效果（只改标签，绝不改动文本内容）"""
        if self.is_preview_mode:
            return
        self.is_preview_mode = True
        self.current_content = self.editor.get("1.0", tk.END)
        self._apply_markdown_highlighting()

    def _apply_markdown_highlighting(self):
        """刷新编辑器的Markdown标签。

        这里只做 tag_add / tag_remove，绝不 delete / insert 任何文字。
        因此：光标不会跳、文本不会丢，格式也永远不会像旧版那样
        因为“重写内容”而整体变成没有格式的纯文字。
        预览模式下，只是把标记字符（##、**、`、[[ ]]、-）的前景色设成
        和背景色一致（肉眼看不出），标记字符依然实实在在存在于文本里，
        一按键立刻恢复可见，随时可以继续编辑。
        """
        for tag in ("h1", "h2", "h3", "h4", "h5", "h6", "bold", "italic",
                    "code", "wikilink", "list", "quote", "mdmark"):
            self.editor.tag_remove(tag, "1.0", tk.END)

        content = self.editor.get("1.0", tk.END)
        hide = self.is_preview_mode
        line_num = 1
        for line in content.split("\n"):
            self._tag_line(line_num, line, hide)
            line_num += 1

    def _tag_line(self, line_num, line, hide):
        """给某一行打上格式标签；hide=True 时把标记字符隐藏（前景色=背景色）"""
        def add(tag, a, b):
            if b > a:
                self.editor.tag_add(tag, "%d.%d" % (line_num, a), "%d.%d" % (line_num, b))

        def hide_range(a, b):
            if hide and b > a:
                self.editor.tag_add("mdmark", "%d.%d" % (line_num, a), "%d.%d" % (line_num, b))

        total = len(line)
        is_block = False

        # ---- 块级：标题 ----
        m = re.match(r"^(#{1,6}) ", line)
        if m:
            add("h%d" % len(m.group(1)), m.end(), total)
            hide_range(0, m.end())
            is_block = True
        # ---- 块级：引用 ----
        elif line.startswith("> "):
            add("quote", 2, total)
            hide_range(0, 2)
            is_block = True
        # ---- 块级：列表 ----
        elif re.match(r"^[-*+] ", line):
            mlist = re.match(r"^[-*+] ", line)
            add("list", mlist.end(), total)
            hide_range(0, mlist.end())

        if is_block:
            return

        # ---- 内联格式（只在标签范围内着色，标记字符单独隐藏）----
        for m in re.finditer(r"\*\*([^*\n]+)\*\*", line):
            add("bold", m.start() + 2, m.end() - 2)
            hide_range(m.start(), m.start() + 2)
            hide_range(m.end() - 2, m.end())
        for m in re.finditer(r"(?<!\*)\*([^*\n]+)\*(?!\*)", line):
            add("italic", m.start() + 1, m.end() - 1)
            hide_range(m.start(), m.start() + 1)
            hide_range(m.end() - 1, m.end())
        for m in re.finditer(r"`([^`\n]+)`", line):
            add("code", m.start() + 1, m.end() - 1)
            hide_range(m.start(), m.start() + 1)
            hide_range(m.end() - 1, m.end())
        for m in re.finditer(r"\[\[([^\]\n]+)\]\]", line):
            add("wikilink", m.start() + 2, m.end() - 2)
            hide_range(m.start(), m.start() + 2)
            hide_range(m.end() - 2, m.end())

    def _on_editor_key(self, event):
        """编辑器按键事件：打字时显示标记，停手400ms后隐藏标记，800ms后自动保存"""
        if self.save_after_id:
            self.root.after_cancel(self.save_after_id)
        if self.preview_after_id:
            self.root.after_cancel(self.preview_after_id)

        # 打字时切回“显示标记”（只改标签，不动文字，光标原地不动）
        self.is_preview_mode = False
        self.current_content = self.editor.get("1.0", tk.END)
        self._apply_markdown_highlighting()

        self.preview_after_id = self.root.after(400, self._switch_to_preview_mode)
        self.save_after_id = self.root.after(800, self._auto_save)
        return None

    def _auto_save(self):
        """自动保存"""
        if self.current_file:
            self._save_current()
    
    def _on_enter(self, event):
        """回车自动续列表"""
        self._switch_to_edit_mode()
        # 获取当前行
        cursor_pos = self.editor.index(tk.INSERT)
        line_num = int(cursor_pos.split(".")[0])
        line_start = f"{line_num}.0"
        line_end = f"{line_num}.end"
        current_line = self.editor.get(line_start, line_end)
        
        # 如果当前行是列表项，下一行自动加列表符
        if re.match(r"^[-*+] ", current_line):
            # 如果当前行只有列表符，就取消列表
            if len(current_line.strip()) <= 2:
                self.editor.delete(line_start, line_end)
                self.editor.insert(tk.INSERT, "\n")
                return "break"
            self.editor.insert(tk.INSERT, "\n- ")
            return "break"
        return None
    
    def _on_ctrl_click(self, event):
        """Ctrl+Click跳转双链"""
        index = self.editor.index(f"@{event.x},{event.y}")
        tags = self.editor.tag_names(index)
        if "wikilink" in tags:
            # 找到双链内容
            line_num = int(index.split(".")[0])
            line_start = f"{line_num}.0"
            line_end = f"{line_num}.end"
            line = self.editor.get(line_start, line_end)
            # 找点击位置附近的双链
            pos = int(index.split(".")[1])
            matches = list(re.finditer(r"\[\[([^\]]+)\]\]", line))
            for m in matches:
                if m.start() <= pos <= m.end():
                    note_name = m.group(1).split("|")[0].strip()
                    self._open_note_by_name(note_name)
                    return
    
    def _open_note_by_name(self, name):
        """按名称打开笔记"""
        if not self.vault_path:
            return
        # 查找文件
        for root, dirs, files in os.walk(self.vault_path):
            for f in files:
                if f[:-3] == name and f.endswith(".md"):
                    self._open_file(os.path.join(root, f))
                    return
        # 不存在就询问是否创建
        if messagebox.askyesno("创建笔记", f"笔记「{name}」不存在，是否创建？"):
            path = os.path.join(self.vault_path, name + ".md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {name}\n\n")
            self._load_vault_tree()
            self._build_backlink_index()
            self._open_file(path)
    
    def _show_editor_menu(self, event):
        """显示编辑器右键菜单"""
        self.editor_menu.tk_popup(event.x_root, event.y_root)
    
    def _insert_bold(self):
        try:
            sel_start = self.editor.index(tk.SEL_FIRST)
            sel_end = self.editor.index(tk.SEL_LAST)
            selected = self.editor.get(sel_start, sel_end)
            self.editor.delete(sel_start, sel_end)
            self.editor.insert(tk.INSERT, f"**{selected}**")
        except:
            self.editor.insert(tk.INSERT, "****")
            self.editor.mark_set(tk.INSERT, f"{self.editor.index(tk.INSERT)}-2c")
    
    def _insert_italic(self):
        try:
            sel_start = self.editor.index(tk.SEL_FIRST)
            sel_end = self.editor.index(tk.SEL_LAST)
            selected = self.editor.get(sel_start, sel_end)
            self.editor.delete(sel_start, sel_end)
            self.editor.insert(tk.INSERT, f"*{selected}*")
        except:
            self.editor.insert(tk.INSERT, "**")
            self.editor.mark_set(tk.INSERT, f"{self.editor.index(tk.INSERT)}-1c")
    
    def _insert_wikilink(self):
        try:
            sel_start = self.editor.index(tk.SEL_FIRST)
            sel_end = self.editor.index(tk.SEL_LAST)
            selected = self.editor.get(sel_start, sel_end)
            self.editor.delete(sel_start, sel_end)
            self.editor.insert(tk.INSERT, f"[[{selected}]]")
        except:
            self.editor.insert(tk.INSERT, "[[]]")
            self.editor.mark_set(tk.INSERT, f"{self.editor.index(tk.INSERT)}-2c")
    
    def _delete_line(self):
        cursor_pos = self.editor.index(tk.INSERT)
        line_num = int(cursor_pos.split(".")[0])
        self.editor.delete(f"{line_num}.0", f"{line_num+1}.0")
    
    def _quick_switch(self):
        if not self.vault_path:
            messagebox.showinfo("提示", "请先打开笔记库")
            return
        # 简单快速切换
        notes = []
        for root, dirs, files in os.walk(self.vault_path):
            for f in files:
                if f.endswith(".md"):
                    notes.append(f[:-3])
        name = simpledialog.askstring("快速切换", "输入笔记名称:", initialvalue=notes[0] if notes else "")
        if name:
            self._open_note_by_name(name)
    
    def _update_backlinks_panel(self):
        """更新反向链接面板"""
        self.backlinks_list.delete(0, tk.END)
        if not self.current_file:
            return
        current_name = os.path.basename(self.current_file)[:-3]
        if current_name in self.backlinks:
            for src in sorted(self.backlinks[current_name]):
                self.backlinks_list.insert(tk.END, f"← {src}")
    
    def _open_backlink(self, event):
        selection = self.backlinks_list.curselection()
        if not selection:
            return
        text = self.backlinks_list.get(selection[0])
        note_name = text[2:].strip()
        self._open_note_by_name(note_name)
    
    # ==================== 知识图谱 ====================
    def _build_graph(self):
        """构建知识图谱"""
        self.graph_canvas.delete("all")
        self.graph_nodes = {}
        self.graph_edges = []
        
        if not self.vault_path or not self.backlinks:
            # 提示文字
            w = self.graph_canvas.winfo_width()
            h = self.graph_canvas.winfo_height()
            self.graph_canvas.create_text(w//2, h//2, text="打开笔记库并添加双链后\n这里会显示知识图谱", justify=tk.CENTER, fill="#6b7280", font=("Microsoft YaHei UI", 11))
            return
        
        # 收集所有节点
        notes = list(self.backlinks.keys())
        if not notes:
            return
        
        # 初始化节点位置（随机布局）
        w = self.graph_canvas.winfo_width() or 400
        h = self.graph_canvas.winfo_height() or 400
        cx, cy = w//2, h//2
        for i, note in enumerate(notes):
            angle = 2 * math.pi * i / len(notes)
            r = 120 + random.random() * 40
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            # 节点大小根据被引用次数
            size = 12 + len(self.backlinks[note]) * 2
            self.graph_nodes[note] = {"x": x, "y": y, "size": size}
        
        # 收集边
        current_name = os.path.basename(self.current_file)[:-3] if self.current_file else None
        for note, links in self.backlinks.items():
            for link in links:
                self.graph_edges.append((link, note))
        
        # 简单力导向布局迭代
        for _ in range(50):
            # 斥力
            for n1 in self.graph_nodes.values():
                for n2 in self.graph_nodes.values():
                    if n1 is n2:
                        continue
                    dx = n1["x"] - n2["x"]
                    dy = n1["y"] - n2["y"]
                    dist = max(math.sqrt(dx*dx + dy*dy), 1)
                    force = 2000 / (dist * dist)
                    n1["x"] += dx/dist * force
                    n1["y"] += dy/dist * force
            # 引力
            for src, dst in self.graph_edges:
                if src in self.graph_nodes and dst in self.graph_nodes:
                    n1 = self.graph_nodes[src]
                    n2 = self.graph_nodes[dst]
                    dx = n2["x"] - n1["x"]
                    dy = n2["y"] - n1["y"]
                    dist = max(math.sqrt(dx*dx + dy*dy), 1)
                    force = (dist - 80) * 0.05
                    n1["x"] += dx/dist * force
                    n1["y"] += dy/dist * force
                    n2["x"] -= dx/dist * force
                    n2["y"] -= dy/dist * force
            # 中心引力
            for n in self.graph_nodes.values():
                dx = cx - n["x"]
                dy = cy - n["y"]
                n["x"] += dx * 0.01
                n["y"] += dy * 0.01
        
        self._draw_graph()
    
    def _draw_graph(self):
        """绘制图谱"""
        self.graph_canvas.delete("all")
        
        # 绘制边
        for src, dst in self.graph_edges:
            if src in self.graph_nodes and dst in self.graph_nodes:
                n1 = self.graph_nodes[src]
                n2 = self.graph_nodes[dst]
                x1 = n1["x"] + self.graph_pan_x
                y1 = n1["y"] + self.graph_pan_y
                x2 = n2["x"] + self.graph_pan_x
                y2 = n2["y"] + self.graph_pan_y
                self.graph_canvas.create_line(x1*self.graph_scale, y1*self.graph_scale, x2*self.graph_scale, y2*self.graph_scale, fill="#cbd5e1", width=1)
        
        # 绘制节点
        current_name = os.path.basename(self.current_file)[:-3] if self.current_file else None
        for name, node in self.graph_nodes.items():
            x = (node["x"] + self.graph_pan_x) * self.graph_scale
            y = (node["y"] + self.graph_pan_y) * self.graph_scale
            r = node["size"] * self.graph_scale
            if name == current_name:
                fill = "#ef4444"
                outline = "#b91c1c"
            else:
                fill = "#93c5fd"
                outline = "#3b82f6"
            self.graph_canvas.create_oval(x-r, y-r, x+r, y+r, fill=fill, outline=outline, width=2, tags=("node", name))
            self.graph_canvas.create_text(x, y + r + 8, text=name, fill="#1e293b", font=("Microsoft YaHei UI", max(8, int(9*self.graph_scale))), tags=("node", name))
    
    def _graph_zoom(self, event):
        if event.delta > 0:
            self.graph_scale = min(self.graph_scale * 1.2, 3.0)
        else:
            self.graph_scale = max(self.graph_scale / 1.2, 0.3)
        self._draw_graph()
    
    def _graph_pan_start(self, event):
        self.graph_panning = True
        self.graph_pan_start_x = event.x
        self.graph_pan_start_y = event.y
        self.graph_pan_start_dx = self.graph_pan_x
        self.graph_pan_start_dy = self.graph_pan_y
    
    def _graph_pan_move(self, event):
        if self.graph_panning:
            dx = (event.x - self.graph_pan_start_x) / self.graph_scale
            dy = (event.y - self.graph_pan_start_y) / self.graph_scale
            self.graph_pan_x = self.graph_pan_start_dx + dx
            self.graph_pan_y = self.graph_pan_start_dy + dy
            self._draw_graph()
    
    def _graph_node_press(self, event):
        self.graph_panning = False
        item = self.graph_canvas.find_closest(event.x, event.y)
        tags = self.graph_canvas.gettags(item)
        for tag in tags:
            if tag in self.graph_nodes:
                self.graph_dragging = tag
                return
        self.graph_dragging = None
    
    def _graph_node_drag(self, event):
        if self.graph_dragging and self.graph_dragging in self.graph_nodes:
            x = event.x / self.graph_scale - self.graph_pan_x
            y = event.y / self.graph_scale - self.graph_pan_y
            self.graph_nodes[self.graph_dragging]["x"] = x
            self.graph_nodes[self.graph_dragging]["y"] = y
            self._draw_graph()
    
    def _graph_node_open(self, event):
        item = self.graph_canvas.find_closest(event.x, event.y)
        tags = self.graph_canvas.gettags(item)
        for tag in tags:
            if tag in self.graph_nodes:
                self._open_note_by_name(tag)
                self.right_notebook.select(0)
                return
    
    # ==================== AI聊天功能 ====================
    def _refresh_ollama_status(self):
        """刷新Ollama连接状态与模型列表"""
        T = self.T
        self._check_ollama_running()
        self._sync_model_combo()
        if self.ollama_running:
            self.chat_status_label.config(text=T["ollama_connected"], foreground="#16a34a")
            detail = T["msg_ollama_ok"].format(len(self.available_models), self.current_chat_model)
            self._add_chat_message("assistant", detail)
        else:
            self.chat_status_label.config(text=T["ollama_not_detected"], foreground="#dc2626")
            reason = self.ollama_error or "未知原因"
            self._add_chat_message(
                "assistant",
                T["msg_ollama_fail"].format(self.ollama_host, reason, AI_LOG_FILE))

    
    def _show_ollama_guide(self):
        """显示Ollama安装指引"""
        T = self.T
        messagebox.showinfo(T["ollama_guide_title"], T["ollama_guide_content"])
        # 尝试打开官网
        try:
            os.startfile("https://ollama.com")
        except:
            pass
    
    def _download_model_mirror(self):
        """用 ModelScope 国内源一键下载模型（绕开 registry.ollama.ai）"""
        cmd = "ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF"
        copied = False
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(cmd)
            copied = True
        except Exception:
            pass
        msg = (
            "将为你下载中文大模型（ModelScope 魔搭国内源，可绕开 registry.ollama.ai 无法访问的问题）：\n\n"
            f"    {cmd}\n\n"
            "· 模型约 2GB，CPU 即可运行，下载完成后自动进入对话。\n"
            "· 需要先安装 Ollama（https://ollama.com）。\n"
            "· 首次下载会显示进度条，请耐心等待。\n"
            + ("· 命令已复制到剪贴板。\n" if copied else "") +
            "\n点「确定」后会自动打开命令提示符窗口执行下载。"
        )
        if not messagebox.askokcancel("下载模型（国内源）", msg):
            return
        try:
            os.system(f'start "CioNote Model Download" cmd /k "{cmd}"')
            self._update_status("已打开命令提示符，正在用国内源下载模型...", 5000)
        except Exception as e:
            messagebox.showerror("Error", f"无法打开命令提示符：{e}\n\n请手动打开 cmd 并粘贴命令：\n{cmd}")
    
    def _open_api_settings(self):
        """配置在线大模型 API（OpenAI 兼容接口）"""
        dlg = tk.Toplevel(self.root)
        dlg.title("Online API Settings / 在线API设置")
        dlg.transient(self.root)
        dlg.grab_set()
        # 高度要装得下说明文字和底部两个按钮；原来 360 会把 Save/Cancel 挤出可视区
        dlg.geometry("560x490")
        frm = ttk.Frame(dlg)
        frm.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        ttk.Label(frm, text="Backend / 后端:").grid(row=0, column=0, sticky="w", pady=5)
        mode_var = tk.StringVar(value=self.api_mode)
        ttk.Combobox(frm, textvariable=mode_var, values=["ollama", "openai"],
                     state="readonly", width=34).grid(row=0, column=1, sticky="w", pady=5)

        ttk.Label(frm, text="Base URL:").grid(row=1, column=0, sticky="w", pady=5)
        base_var = tk.StringVar(value=self.api_base)
        ttk.Entry(frm, textvariable=base_var, width=36).grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(frm, text="API Key:").grid(row=2, column=0, sticky="w", pady=5)
        key_var = tk.StringVar(value=self.api_key)
        ttk.Entry(frm, textvariable=key_var, width=36, show="*").grid(row=2, column=1, sticky="w", pady=5)

        ttk.Label(frm, text="Model:").grid(row=3, column=0, sticky="w", pady=5)
        model_var = tk.StringVar(value=self.api_model)
        ttk.Entry(frm, textvariable=model_var, width=36).grid(row=3, column=1, sticky="w", pady=5)

        tip = (
            "Backend=openai 时才会走在线接口（Base URL 以 /v1 结尾的 OpenAI 兼容地址）。\n"
            "示例 / Examples:\n"
            "· DeepSeek   https://api.deepseek.com/v1          model: deepseek-chat\n"
            "· 智谱GLM    https://open.bigmodel.cn/api/paas/v4  model: glm-4-flash\n"
            "· 硅基流动   https://api.siliconflow.cn/v1        model: Qwen/Qwen2.5-7B-Instruct\n"
            "· OpenAI     https://api.openai.com/v1            model: gpt-4o-mini\n"
            "🔒 API Key 只保存在本机，并经 Windows 系统加密（DPAPI），配置文件里不含明文。\n"
            f"当前密钥：{_mask_secret(self.api_key) or '（未设置）'}"
        )
        ttk.Label(frm, text=tip, justify=tk.LEFT, foreground="#666666").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=10)

        def on_save():
            self.api_mode = mode_var.get().strip() or "ollama"
            self.api_base = base_var.get().strip()
            self.api_key = key_var.get().strip()
            self.api_model = model_var.get().strip()
            self._save_config()
            dlg.destroy()
            self._refresh_route_buttons()
            self._sync_model_combo()
            if self.api_mode != "openai":
                self._check_ollama_running()
                self._sync_model_combo()
            note = f"\n✅ 设置已保存：后端 = {self.api_mode}"
            if self.api_mode == "openai":
                note += f"\n模型 = {self.api_model}\n接口 = {self.api_base}"
                note += f"\n密钥 = {_mask_secret(self.api_key) or '（未设置）'}（本机加密存储）"
            self._add_chat_message("assistant", note)

        btnf = ttk.Frame(frm)
        btnf.grid(row=5, column=0, columnspan=2, pady=8)
        ttk.Button(btnf, text="Save / 保存", command=on_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btnf, text="Cancel / 取消", command=dlg.destroy).pack(side=tk.LEFT, padx=6)
    
    def _safe_after(self, func, *args):
        """跨线程把 UI 更新排回主线程（替代裸 self.root.after(0, ...)）。

        后台请求线程可能比窗口活得久：用户关掉窗口时 Tk 主循环已退出，此时再调
        after() 会抛 RuntimeError('main thread is not in main loop')，控制台喷一大串
        traceback 但其实无害。这里先确认窗口还在，其它异常也一并吞掉，避免噪声。
        """
        try:
            root = getattr(self, "root", None)
            if root is not None and root.winfo_exists():
                root.after(0, func, *args)
        except RuntimeError:
            pass
        except Exception as e:
            _ai_log("_safe_after 失败: %r" % (e,))

    def _stream_openai_response(self):
        """流式请求 OpenAI 兼容在线 API（SSE）"""
        try:
            url = self.api_base.rstrip("/") + "/chat/completions"
            payload = {"model": self.api_model, "messages": self.chat_history, "stream": True}
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {self.api_key}"}
            req = urllib.request.Request(url, data=data, headers=headers)
            self._safe_after(self._add_chat_message, "assistant", self.T["msg_thinking"])

            full_response = ""
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                        delta = obj.get("choices", [{}])[0].get("delta", {})
                        piece = delta.get("content") or ""
                        if piece:
                            full_response += piece
                            self._safe_after(self._update_last_chat_message, full_response)
                    except Exception:
                        pass

            if full_response:
                self.chat_history.append({"role": "assistant", "content": full_response})
            else:
                self._safe_after(self._update_last_chat_message,
                                "❌ 未收到响应，请检查 Base URL / API Key / 模型名是否正确。")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            self._safe_after(self._update_last_chat_message,
                            f"❌ 在线API返回错误（HTTP {e.code}）：{detail}")
        except Exception as e:
            self._safe_after(self._update_last_chat_message, f"❌ 在线API请求失败：{str(e)}")

    
    def _select_model(self):
        """手动选择模型（菜单入口）。优先用AI面板里的下拉框，这里只作手输兜底。"""
        T = self.T
        model = simpledialog.askstring(
            T["ai_select_model"], T["prompt_select_model"], initialvalue=self.current_chat_model)
        if model:
            self.current_chat_model = model.strip()
            self.model_var.set(self.current_chat_model)
            _ai_log("手动指定模型 -> %s" % self.current_chat_model)
            self._add_chat_message("assistant", T["msg_model_selected"].format(self.current_chat_model))
    
    def _clear_chat(self):
        """清空对话"""
        T = self.T
        self.chat_history = []
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.config(state=tk.DISABLED)
        self._add_chat_message("assistant", self.T["ai_welcome"])
    
    def _ask_ai_selected(self):
        """选中内容提问AI"""
        T = self.T
        try:
            sel_start = self.editor.index(tk.SEL_FIRST)
            sel_end = self.editor.index(tk.SEL_LAST)
            selected = self.editor.get(sel_start, sel_end).strip()
            if selected:
                self.right_notebook.select(0)  # 切到AI面板（第一个标签）
                prompt = f"Please explain/summarize the following content clearly:\n\n{selected}" if self.lang == "en" else f"请帮我解释/总结下面这段内容，用清晰易懂的中文回答：\n\n{selected}"
                self._send_chat_message(prompt)
        except:
            messagebox.showinfo("Info", "Please select text in the editor first." if self.lang == "en" else "请先在编辑器里选中要提问的文字")
    
    def _add_chat_message(self, role, content):
        """添加聊天消息"""
        self.chat_display.config(state=tk.NORMAL)
        if role == "user":
            self.chat_display.insert(tk.END, "\n🧑 你\n", "user_label")
            self.chat_display.insert(tk.END, content + "\n", "user")
        else:
            self.chat_display.insert(tk.END, "\n🤖 AI助手\n", "assistant_label")
            # 简单解析markdown
            self._insert_chat_rich_text(content + "\n")
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)
    
    def _insert_chat_rich_text(self, text):
        """插入聊天富文本"""
        pos = 0
        pattern = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")
        for match in pattern.finditer(text):
            start, end = match.span()
            if start > pos:
                self.chat_display.insert(tk.END, text[pos:start], "assistant")
            token = match.group()
            if token.startswith("**"):
                self.chat_display.insert(tk.END, token[2:-2], "bold")
            elif token.startswith("`"):
                self.chat_display.insert(tk.END, token[1:-1], "code")
            pos = end
        if pos < len(text):
            self.chat_display.insert(tk.END, text[pos:], "assistant")
    
    def _send_chat_message(self, preset_prompt=None):
        """发送聊天消息"""
        if preset_prompt:
            user_input = preset_prompt
            self.chat_input.delete("1.0", tk.END)
        else:
            user_input = self.chat_input.get("1.0", tk.END).strip()
            self.chat_input.delete("1.0", tk.END)
        
        if not user_input:
            return
        
        if self.api_mode == "openai" and self.api_base:
            self._add_chat_message("user", user_input)
            self.chat_history.append({"role": "user", "content": user_input})
            threading.Thread(target=self._stream_openai_response, daemon=True).start()
            return
        
        if not self.ollama_running:
            self._check_ollama_running()
            self._sync_model_combo()
            if not self.ollama_running:
                self._add_chat_message("assistant",
                    self.T["msg_conn_error"].format(self.ollama_error or "未知原因 / unknown") + "\n\n"
                    "🌐 不想折腾本地模型？用菜单「AI助手 → 在线API设置」配置一个 OpenAI 兼容接口也能聊。")
                return
        
        self._add_chat_message("user", user_input)
        self.chat_history.append({"role": "user", "content": user_input})
        
        # 后台线程请求Ollama，避免阻塞UI
        threading.Thread(target=self._stream_ollama_response, daemon=True).start()
    
    def _stream_ollama_response(self):
        """流式请求Ollama响应"""
        self._safe_after(self._add_chat_message, "assistant", self.T["msg_thinking"])
        try:
            host = getattr(self, "ollama_host", None) or _resolve_ollama_host()
            url = host + "/api/chat"
            data = json.dumps({
                # 这里必须用下拉框里选中的真实模型名（本地从国内源拉的模型名通常很长）。
                # 用默认常量 qwen2.5:3b 去请求一个没装过的模型，Ollama 会直接 404。
                "model": self.current_chat_model,
                "messages": self.chat_history,
                "stream": True
            }).encode("utf-8")

            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            _ai_log("请求 %s  模型=%s" % (url, self.current_chat_model))

            full_response = ""
            # 首次请求要把 2GB 级别的模型从磁盘加载进内存，CPU 环境下可能要几十秒，
            # 超时给到 300s，避免冷启动被误判成"没有响应"。
            with urllib.request.urlopen(req, timeout=300) as resp:
                for line in resp:
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        if "message" in chunk and "content" in chunk["message"]:
                            content = chunk["message"]["content"]
                            full_response += content
                            # 更新UI
                            self._safe_after(self._update_last_chat_message, full_response)
                        elif chunk.get("error"):
                            full_response = ""
                            self._safe_after(self._update_last_chat_message,
                                            "❌ Ollama 返回错误：" + str(chunk["error"]) + "\n"
                                            "请点 ↻ 刷新后到「模型」下拉框确认选中的模型名是否正确。")
                            return
                    except json.JSONDecodeError:
                        pass

            if full_response:
                self.chat_history.append({"role": "assistant", "content": full_response})
            else:
                self._safe_after(self._update_last_chat_message,
                                self.T["msg_no_response"].format(self.current_chat_model))

        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            _ai_log("Ollama HTTP %s: %s" % (e.code, detail))
            self._safe_after(self._update_last_chat_message,
                self.T["msg_http_error"].format(e.code, self.current_chat_model, detail or "(no body)"))
        except urllib.error.URLError as e:
            reason = str(e.reason) if e.reason else str(e)
            _ai_log("Ollama URLError: %s" % reason)
            self._safe_after(self._update_last_chat_message,
                            self.T["msg_conn_error"].format(reason))
        except Exception as e:
            _ai_log("Ollama 请求异常: %r" % (e,))
            self._safe_after(self._update_last_chat_message,
                            self.T["msg_request_error"].format(type(e).__name__, e))
    
    def _update_last_chat_message(self, content):
        """更新最后一条AI消息（流式输出用）"""
        self.chat_display.config(state=tk.NORMAL)
        # 找到最后一条assistant消息的位置，删除重新插入
        # 简单实现：删除最后两行（标签+内容），重新插入
        end_pos = self.chat_display.index(tk.END)
        line_num = int(end_pos.split(".")[0])
        # 从最后往前找assistant_label
        for i in range(line_num, 0, -1):
            tags = self.chat_display.tag_names(f"{i}.0")
            if "assistant_label" in tags:
                # 从这里开始删除
                self.chat_display.delete(f"{i}.0", tk.END)
                self.chat_display.insert(tk.END, "\n🤖 AI助手\n", "assistant_label")
                self._insert_chat_rich_text(content + "\n")
                break
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)


def main():
    root = tk.Tk()
    app = CioNoteApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()