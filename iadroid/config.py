"""
IADroid - Configuration & Chemins
"""

import os
import sys
from pathlib import Path

# Détection de l'environnement (Termux vs autre système)
IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "") or os.path.exists("/data/data/com.termux")

# Répertoire racine IADroid (par défaut ~/.iadroid)
IADROID_HOME = Path(os.environ.get("IADROID_HOME", Path.home() / ".iadroid")).resolve()

# Sous-dossiers
PRESETS_DIR = IADROID_HOME / "presets"
MODELS_DIR = IADROID_HOME / "models"
BIN_DIR = IADROID_HOME / "bin"
LOGS_DIR = IADROID_HOME / "logs"
ACTIVE_PRESET_FILE = IADROID_HOME / "active_preset.txt"

# Chemins binaires par défaut
LLAMA_CLI_PATH = BIN_DIR / "llama-cli"
LLAMA_SERVER_PATH = BIN_DIR / "llama-server"

# Couleurs & Thème UI
ANDROID_GREEN = "#3DDC84"
DARK_GRAY = "#2A2A2A"
CYAN = "#00D8D6"
YELLOW = "#FFD32A"

# Niveaux d'effort de réflexion (Reasoning / Jinja)
REASONING_EFFORT_LEVELS = ["minimal", "low", "medium", "high", "xhigh"]

# Budget de tokens indicatif pour chaque niveau d'effort
REASONING_TOKEN_BUDGETS = {
    "minimal": 256,
    "low": 512,
    "medium": 1024,
    "high": 2048,
    "xhigh": 4096
}

def get_ram_info() -> tuple:
    """
    Retourne (used_gb, total_gb, percent, free_gb).
    Lit /proc/meminfo sous Android Termux / Linux avec fallback psutil.
    """
    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])
        total_kb = meminfo.get("MemTotal", 0)
        avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0))
        used_kb = max(0, total_kb - avail_kb)
        if total_kb > 0:
            return (used_kb / 1048576, total_kb / 1048576, (used_kb / total_kb) * 100, avail_kb / 1048576)
    except Exception:
        pass
    try:
        import psutil
        vm = psutil.virtual_memory()
        return (vm.used / (1024**3), vm.total / (1024**3), vm.percent, vm.available / (1024**3))
    except Exception:
        pass
    return (0.0, 0.0, 0.0, 0.0)

def get_gpu_type() -> str:
    """
    Détecte avec précision l'architecture matérielle du GPU sous Android :
    - 'Adreno' (Qualcomm Snapdragon via /dev/kgsl-3d0 ou getprop)
    - 'Mali' (MediaTek Dimensity / Samsung Exynos / ARM Mali via /dev/mali0 ou getprop)
    - 'Vulkan' (Générique / Autre)
    """
    try:
        # 1. Périphériques matériels directs sous /dev
        if Path("/dev/kgsl-3d0").exists() or Path("/dev/kgsl-2d0").exists():
            return "Adreno"
        if Path("/dev/mali0").exists() or Path("/dev/mali").exists():
            return "Mali"

        # 2. Propriétés système Android (getprop)
        import subprocess
        for prop in ["ro.hardware.egl", "ro.hardware.vulkan", "ro.soc.manufacturer", "ro.board.platform", "ro.hardware"]:
            try:
                val = subprocess.check_output(["getprop", prop], text=True, stderr=subprocess.DEVNULL, timeout=0.5).strip().lower()
                if any(k in val for k in ("adreno", "qcom", "qualcomm", "snapdragon")):
                    return "Adreno"
                if any(k in val for k in ("mali", "mediatek", "mtk", "dimensity", "immortalis")):
                    return "Mali"
            except Exception:
                pass

        # 3. /proc/cpuinfo
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").lower()
            if "qualcomm" in cpuinfo or "snapdragon" in cpuinfo:
                return "Adreno"
            if "mediatek" in cpuinfo or "mt6" in cpuinfo or "mt8" in cpuinfo or "dimensity" in cpuinfo:
                return "Mali"
        except Exception:
            pass
    except Exception:
        pass
    return "Vulkan"

def ensure_directories():
    """Crée l'arborescence des dossiers et assure la liaison du pilote GPU Vulkan sous Termux."""
    for d in (PRESETS_DIR, MODELS_DIR, BIN_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # Liaison automatique du pilote Vulkan système d'Android sous Termux
    system_vulkan = Path("/system/lib64/libvulkan.so")
    prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    termux_lib = Path(prefix) / "lib"
    termux_vulkan = termux_lib / "libvulkan.so"

    if system_vulkan.exists() and termux_lib.exists():
        try:
            if not termux_vulkan.is_symlink() or str(termux_vulkan.resolve()) != str(system_vulkan):
                if termux_vulkan.exists() or termux_vulkan.is_symlink():
                    termux_vulkan.unlink()
                os.symlink(str(system_vulkan), str(termux_vulkan))
        except Exception:
            pass

def get_default_preset(name: str = "default") -> dict:
    """Retourne la structure par défaut d'un profil IADroid avec les réglages optimisés pour mobile."""
    # Détection des cœurs CPU : sur mobile, privilégier les gros cœurs (Performance cores)
    cpu_count = os.cpu_count() or 8
    threads = max(2, cpu_count // 2)

    return {
        "name": name,
        "description": "Profil optimisé pour smartphone Android",
        "model_path": str(MODELS_DIR / "model.gguf"),
        "mmproj_path": "",  # Projecteur de vision (optionnel)
        "hardware": {
            "device": "gpu",         # "gpu" ou "cpu"
            "ngl": 99,               # 99 = toutes les couches déchargées sur le GPU Vulkan (0 = CPU)
            "cpu_threads": threads,  # Threads de calcul CPU (-t)
            "batch": 512,            # Taille du batch d'évaluation des prompts (-b)
            "ubatch": 128,           # Taille du micro-batch (-ub)
            "mmap": True,            # Recommandé pour RAM unifiée mobile (évite l'OOM killer)
            "mlock": False,          # Déconseillé sur Android non-rooté (bloqué par RLIMIT_MEMLOCK)
            "flash_attention": True, # -fa : réduit l'empreinte mémoire du contexte par 2-3
            "kv_unified": True,      # Cache KV unifié
            "cache_type_k": "f16",   # Type de quantification pour les clés KV (f16, q8_0, q4_0)
            "cache_type_v": "f16"    # Type de quantification pour les valeurs KV (f16, q8_0, q4_0)
        },
        "context": {
            "ctx_size": 4096,        # Contexte en tokens (-c)
            "system_prompt": "Tu es IADroid, un assistant IA intelligent et concis tournant localement sur smartphone Android."
        },
        "sampling": {
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 40,
            "min_p": 0.05,
            "presence_penalty": 0.0,
            "repeat_penalty": 1.1
        },
        "reasoning": {
            "enabled": True,         # On/Off réflexion
            "effort": "medium",      # minimal, low, medium, high, xhigh
            "budget_tokens": 1024,
            "jinja_template": "{% if reasoning_effort %}<think>\nMode réflexion activé (effort: {{ reasoning_effort }})\n</think>\n{% endif %}{{ prompt }}"
        }
    }
