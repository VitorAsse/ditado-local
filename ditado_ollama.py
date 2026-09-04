import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


OLLAMA_PACKAGE_ID = "Ollama.Ollama"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"


class OllamaSetupError(RuntimeError):
    pass


def is_local_ollama_url(url):
    try:
        hostname = urlsplit(url).hostname
    except (TypeError, ValueError):
        return False
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _api_url(chat_url, endpoint):
    parsed = urlsplit(chat_url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/api/{endpoint}", "", "")
    )


def ollama_api_is_ready(chat_url, timeout=1.0):
    try:
        with urllib.request.urlopen(
            _api_url(chat_url, "version"),
            timeout=timeout,
        ) as response:
            return 200 <= int(response.status) < 300
    except (OSError, ValueError, urllib.error.URLError):
        return False


def find_ollama_executable():
    configured = os.environ.get("DITADO_OLLAMA_EXECUTABLE", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        return configured_path if configured_path.is_file() else None

    command = shutil.which("ollama.exe") or shutil.which("ollama")
    if command:
        return Path(command)

    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("PROGRAMFILES")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        )
    if program_files:
        candidates.append(Path(program_files) / "Ollama" / "ollama.exe")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Ollama" / "ollama.exe")
    return next((path for path in candidates if path.is_file()), None)


def find_winget_executable():
    command = shutil.which("winget.exe") or shutil.which("winget")
    if command:
        return Path(command)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Microsoft" / "WindowsApps" / "winget.exe"
        if candidate.is_file():
            return candidate
    return None


def _creation_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_checked(command, timeout, failure_message):
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired as error:
        raise OllamaSetupError(f"{failure_message} O tempo limite foi excedido.") from error
    except OSError as error:
        raise OllamaSetupError(f"{failure_message} {error}") from error
    if completed.returncode != 0:
        raise OllamaSetupError(
            f"{failure_message} Código de saída: {completed.returncode}."
        )
    return completed


def _wait_for_ollama_executable(timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executable = find_ollama_executable()
        if executable is not None:
            return executable
        time.sleep(0.5)
    return None


def install_ollama():
    winget = find_winget_executable()
    if winget is None:
        raise OllamaSetupError(
            "A instalação automática precisa do WinGet (App Installer da Microsoft), "
            f"que não foi encontrado. Instale-o ou baixe o Ollama em {OLLAMA_DOWNLOAD_URL}."
        )
    _run_checked(
        [
            winget,
            "install",
            "--id",
            OLLAMA_PACKAGE_ID,
            "--exact",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        timeout=15 * 60,
        failure_message="Não foi possível instalar o Ollama automaticamente.",
    )
    executable = _wait_for_ollama_executable()
    if executable is None:
        raise OllamaSetupError(
            "O instalador terminou, mas o executável do Ollama não foi encontrado. "
            f"Conclua a instalação em {OLLAMA_DOWNLOAD_URL}."
        )
    return executable


def ensure_ollama_running(executable, chat_url, timeout=45):
    if ollama_api_is_ready(chat_url):
        return
    try:
        subprocess.Popen(
            [str(executable), "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
    except OSError as error:
        raise OllamaSetupError(f"Não foi possível iniciar o Ollama. {error}") from error

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ollama_api_is_ready(chat_url):
            return
        time.sleep(0.5)
    raise OllamaSetupError(
        "O Ollama foi iniciado, mas a API local não respondeu em até 45 segundos."
    )


def pull_ollama_model(executable, model):
    model = str(model).strip()
    if (
        not model
        or len(model) > 200
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?", model)
        is None
    ):
        raise OllamaSetupError("O nome do modelo local é inválido.")
    _run_checked(
        [executable, "pull", model],
        timeout=60 * 60,
        failure_message=f"Não foi possível baixar o modelo {model}.",
    )


def prepare_ollama(model, chat_url, on_status=None):
    if not is_local_ollama_url(chat_url):
        raise OllamaSetupError(
            "A instalação automática só está disponível para o endpoint local do Ollama."
        )
    notify = on_status if callable(on_status) else lambda _title, _detail: None

    executable = find_ollama_executable()
    if executable is None:
        notify(
            "Instalando o Ollama...",
            "O instalador oficial está sendo executado pelo WinGet.",
        )
        executable = install_ollama()

    if not ollama_api_is_ready(chat_url):
        notify(
            "Iniciando o Ollama...",
            "Preparando o serviço local do modo Agente.",
        )
        ensure_ollama_running(executable, chat_url)

    notify(
        "Preparando o modelo...",
        "O primeiro download pode levar alguns minutos.",
    )
    pull_ollama_model(executable, model)
    notify("Validando o Agente...", "Confirmando a resposta do modelo local.")
    return executable
