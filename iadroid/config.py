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

# Mode agent : dossier de travail de l'IA, notes mémorisées, réglages (permissions, dossiers autorisés)
WORKSPACE_DIR = IADROID_HOME / "workspace"
MEMORY_DIR = IADROID_HOME / "memory"
AGENT_SETTINGS_FILE = IADROID_HOME / "agent.json"

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

# --- Sélection du pilote Vulkan -------------------------------------------------
# llama.cpp (libggml-vulkan.so) charge "libvulkan.so" depuis $PREFIX/lib.
# Ce lien décide du pilote utilisé :
#   - Termux (libvulkan.so.1, vulkan-loader-generic) -> pilotes Mesa de $PREFIX/share/vulkan/icd.d
#     => Turnip pour Adreno (stable avec llama.cpp)
#   - Android (/system/lib64/libvulkan.so) -> pilote propriétaire du constructeur
#     => seul choix pour Mali ; sur Adreno, le pilote Qualcomm plante avec
#        'createComputePipeline: ErrorUnknown' (ex: mul_mat_vec_q4_k)
# Forçable via la variable d'environnement IADROID_VULKAN=turnip|system.

SYSTEM_VULKAN = Path("/system/lib64/libvulkan.so")

def _termux_prefix() -> Path:
    return Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))

def get_turnip_icd():
    """Retourne le chemin du manifeste ICD Turnip (Mesa freedreno) s'il est installé, sinon None."""
    icd_dir = _termux_prefix() / "share" / "vulkan" / "icd.d"
    try:
        for p in sorted(icd_dir.glob("freedreno_icd*.json")):
            return p
    except Exception:
        pass
    return None

def get_vulkan_driver() -> str:
    """
    Choisit le pilote Vulkan à utiliser :
    - 'turnip' : GPU Adreno + Turnip installé + chargeur Vulkan Termux présent
    - 'system' : pilote Android du constructeur (Mali, ou Adreno sans Turnip)
    - 'none'   : aucun pilote Vulkan trouvé
    """
    termux_loader = _termux_prefix() / "lib" / "libvulkan.so.1"
    forced = os.environ.get("IADROID_VULKAN", "").strip().lower()
    if forced == "turnip" and termux_loader.exists():
        return "turnip"
    if forced == "system" and SYSTEM_VULKAN.exists():
        return "system"

    if get_gpu_type() == "Adreno" and get_turnip_icd() and termux_loader.exists():
        return "turnip"
    if SYSTEM_VULKAN.exists():
        return "system"
    if termux_loader.exists():
        return "turnip"
    return "none"

def ensure_vulkan_link() -> str:
    """
    Fait pointer $PREFIX/lib/libvulkan.so vers le chargeur correspondant au pilote choisi.
    Retourne le pilote actif ('turnip', 'system' ou 'none').
    """
    driver = get_vulkan_driver()
    lib_dir = _termux_prefix() / "lib"
    link = lib_dir / "libvulkan.so"
    if driver == "turnip":
        target = "libvulkan.so.1"  # chargeur Termux, cible relative comme le paquet d'origine
        expected = lib_dir / "libvulkan.so.1"
    elif driver == "system":
        target = str(SYSTEM_VULKAN)
        expected = SYSTEM_VULKAN
    else:
        return driver

    if not lib_dir.exists():
        return driver
    try:
        if not link.is_symlink() or link.resolve() != expected.resolve():
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(target, str(link))
    except Exception:
        pass
    return driver

def get_vulkan_env(base_env: dict = None) -> dict:
    """Environnement de lancement des binaires llama.cpp, adapté au pilote Vulkan actif."""
    env = dict(base_env if base_env is not None else os.environ)
    driver = ensure_vulkan_link()
    if driver == "turnip":
        env["TU_DEBUG"] = "noconform"
        env["MESA_VK_WSI_PRESENT_MODE"] = "mailbox"
    elif driver == "system":
        env["LD_LIBRARY_PATH"] = f"/system/lib64:{_termux_prefix() / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}"
    return env

# Sous Turnip, un micro-batch (-ub) > 128 provoque vk::ErrorDeviceLost dès que le contexte
# dépasse 1024 tokens (charge GPU trop longue, réinitialisée par le noyau). Testé sur Adreno 750.
TURNIP_MAX_UBATCH = 128

def effective_ubatch(hw: dict) -> tuple:
    """
    Retourne (ubatch_effectif, limite_appliquee) pour la section 'hardware' d'un profil.
    Limite le micro-batch sous Turnip quand des couches tournent sur le GPU.
    """
    ub = int(hw.get("ubatch", 128))
    dev = hw.get("device", "gpu")
    ngl = hw.get("ngl", 99 if dev == "gpu" else 0)
    if dev == "gpu" and ngl > 0 and ub > TURNIP_MAX_UBATCH and get_vulkan_driver() == "turnip":
        return TURNIP_MAX_UBATCH, True
    return ub, False

def ensure_directories():
    """Crée l'arborescence des dossiers et règle la liaison du pilote GPU Vulkan sous Termux."""
    for d in (PRESETS_DIR, MODELS_DIR, BIN_DIR, LOGS_DIR, WORKSPACE_DIR, MEMORY_DIR):
        d.mkdir(parents=True, exist_ok=True)
    ensure_vulkan_link()

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
