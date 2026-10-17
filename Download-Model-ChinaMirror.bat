@echo off
chcp 936 >nul
cd /d "%~dp0"
echo ============================================
echo   CioNote 模型下载工具（ModelScope 国内源）
echo ============================================
echo.
echo 本工具使用 ModelScope（魔搭）国内源下载中文大模型，
echo 可绕开官方源 registry.ollama.ai 在国内无法解析的问题
echo （如果报错“lookup registry.ollama.ai: no such host”，就用这个）。
echo.
echo 即将执行命令：
echo     ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF
echo.
echo 模型约 2GB，CPU 即可运行，下载时间取决于网速，请耐心等待。
echo 需要已安装 Ollama（https://ollama.com）。
echo.
where ollama >nul 2>nul
if not %errorlevel%==0 (
    echo ------------------------------------------------------------
    echo [错误] 未找到 ollama 命令。
    echo 请先安装 Ollama：https://ollama.com
    echo 安装后如果仍然提示找不到，请重新打开本窗口再试。
    echo ------------------------------------------------------------
    pause
    exit /b
)
echo 按任意键开始下载...
pause >nul
echo.
echo 开始下载（首次会显示进度条）...
echo.
ollama run modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF
echo.
echo ============================================
echo 结束。回到 CioNote 点“检测Ollama连接”即可开始聊天。
echo ============================================
pause
