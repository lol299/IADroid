"""
IADroid - Configuration & Chemins
"""

import os
import sys
from pathlib import Path
from rich.console import Console

# Console rich UNIQUE pour tout IADroid. Avec plusieurs instances, un message imprimé par un
# autre module pendant un affichage en direct (rich.Live : chargement, génération) n'est pas
# compté par Live, qui n'efface plus toutes ses lignes : la ligne d'effort et l'indicateur
# de chargement restaient dans la conversation.
console = Console()

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
LLAMA_SRC_DIR = IADROID_HOME / "src" / "llama.cpp"


def get_quantize_path():
    """Binaire llama-quantize (compilé avec llama.cpp dans build/bin), ou None."""
    for p in (BIN_DIR / "llama-quantize", LLAMA_SRC_DIR / "build" / "bin" / "llama-quantize"):
        if p.exists():
            return p
    return None


def recommended_cpu_threads() -> int:
    """
    Nombre de threads CPU conseillé : les cœurs de performance, c'est-à-dire tous sauf
    le groupe de cœurs à la fréquence maximale la plus basse (petits cœurs d'efficacité).
    Mesuré sur Snapdragon 8 Gen 3 : 6 threads (gros cœurs) > 7 ou 8 threads (petits cœurs inclus,
    qui ralentissent tout le monde) > 4 threads.
    """
    n = os.cpu_count() or 8
    freqs = []
    for i in range(n):
        try:
            with open(f"/sys/devices/system/cpu/cpu{i}/cpufreq/cpuinfo_max_freq") as f:
                freqs.append(int(f.read().strip()))
        except (OSError, ValueError):
            pass
    if len(freqs) == n and len(set(freqs)) > 1:
        big = sum(1 for f in freqs if f > min(freqs))
        if big >= 2:
            return big
    return max(2, n // 2)


def cpu_only(hw: dict) -> bool:
    """Profil 100 % CPU (device cpu et aucune couche sur le GPU)."""
    dev = hw.get("device", "gpu")
    return dev == "cpu" and int(hw.get("ngl", 0)) == 0


def mtp_active(preset_data: dict) -> bool:
    """
    Décodage spéculatif MTP utilisé pour ce profil ?
    hardware.speculative : "auto" (si le modèle a des couches MTP et que le profil est 100 % CPU),
    "on" (dès que le modèle en a), "off". En auto, pas de MTP sur GPU : mesuré sur Adreno 750
    en OpenCL, la vérification des tokens proposés y fait chuter la génération (7 → 3 t/s).
    """
    hw = preset_data.get("hardware", {})
    mode = str(hw.get("speculative", "auto")).lower()
    if mode == "off" or not gguf_has_mtp(preset_data.get("model_path") or ""):
        return False
    return mode == "on" or cpu_only(hw)


def runtime_args(preset_data: dict, server: bool = True) -> list:
    """
    Arguments llama.cpp communs aux lanceurs (llama-server et llama-cli) :
    - « -dev none » en 100 % CPU : sinon, avec -ngl 0, llama.cpp envoie quand même les gros
      calculs du prefill au GPU Vulkan (op offload), et initialise Turnip pour rien ;
    - « --spec-type draft-mtp » (serveur) si le MTP est actif : +10 à +35 % en génération sur CPU.
    """
    args = []
    if cpu_only(preset_data.get("hardware", {})):
        args += ["-dev", "none"]
    if server and mtp_active(preset_data):
        args += ["--spec-type", "draft-mtp"]
    return args


_GGUF_CACHE = {}


def gguf_metadata(model_path: str) -> dict:
    """
    Métadonnées scalaires et textes d'un modèle GGUF (les tableaux, comme le vocabulaire, sont
    sautés). Lit seulement l'en-tête (~0,4 s pour 150 000 tokens de vocabulaire), mis en cache
    tant que le fichier ne change pas. Dictionnaire vide si le fichier est illisible.
    """
    import struct
    try:
        key = (model_path, os.path.getmtime(model_path))
    except (OSError, TypeError):
        return {}
    if key in _GGUF_CACHE:
        return _GGUF_CACHE[key]

    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    fmts = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
    meta = {}
    try:
        with open(model_path, "rb") as f:
            def rd(fmt):
                return struct.unpack("<" + fmt, f.read(struct.calcsize("<" + fmt)))[0]

            def rstr():
                return f.read(rd("Q")).decode("utf-8", "replace")

            def skip(t):
                if t == 8:
                    f.seek(rd("Q"), 1)
                elif t == 9:
                    st, cnt = rd("I"), rd("Q")
                    if st in sizes:
                        f.seek(sizes[st] * cnt, 1)
                    else:
                        for _ in range(cnt):
                            skip(st)
                else:
                    f.seek(sizes[t], 1)

            if f.read(4) != b"GGUF":
                raise ValueError("pas un GGUF")
            rd("I"); rd("Q")
            for _ in range(rd("Q")):
                k, t = rstr(), rd("I")
                if t == 8:
                    meta[k] = rstr()
                elif t in fmts:
                    meta[k] = rd(fmts[t])
                else:
                    skip(t)
    except Exception:
        meta = {}
    _GGUF_CACHE[key] = meta
    return meta


def gguf_has_mtp(model_path: str) -> bool:
    """
    Vrai si le modèle GGUF embarque des couches de prédiction multi-tokens (MTP,
    clé « <arch>.nextn_predict_layers » > 0, ex. Qwen3.5). Elles permettent le décodage
    spéculatif sans modèle brouillon (--spec-type draft-mtp).
    """
    meta = gguf_metadata(model_path)
    return any(k.endswith(".nextn_predict_layers") and isinstance(v, int) and v > 0 for k, v in meta.items())


def reasoning_caps(model_path: str) -> dict:
    """
    Ce que le template de chat du modèle sait faire en matière de réflexion :
      thinking : le modèle réfléchit (balises <think>, reasoning_content, enable_thinking…) ;
      toggle   : la réflexion se désactive (variable enable_thinking du template) ;
      effort   : le template lit reasoning_effort (niveaux natifs). Sinon, IADroid règle
                 l'effort par un budget de tokens de réflexion (thinking_budget_tokens),
                 que llama-server applique quel que soit le template ;
      known    : le template a pu être lu (sinon, tout est supposé possible).
    Vérifié le 9 novembre 2026 : Qwen3.5, Qwen3.6, Bonsai et Gemma 4 lisent enable_thinking,
    aucun ne lit reasoning_effort.
    """
    tpl = gguf_metadata(model_path).get("tokenizer.chat_template") or ""
    if not tpl:
        return {"thinking": True, "toggle": True, "effort": False, "known": False}
    toggle = "enable_thinking" in tpl
    thinking = toggle or any(k in tpl for k in ("<think>", "reasoning_content", "thinking"))
    return {"thinking": thinking, "toggle": toggle, "effort": "reasoning_effort" in tpl, "known": True}


# --------------------------------------------------------------------------- capteurs GPU / CPU

_THERMAL_ZONES = None


def _read_int(path: str):
    try:
        with open(path) as f:
            return int(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _thermal_zones():
    """(capteurs CPU, capteurs GPU) parmi /sys/class/thermal, lus une seule fois."""
    global _THERMAL_ZONES
    if _THERMAL_ZONES is None:
        import glob
        cpu, gpu = [], []
        for z in glob.glob("/sys/class/thermal/thermal_zone*"):
            try:
                with open(z + "/type") as f:
                    t = f.read().strip().lower()
            except OSError:
                continue
            if t.startswith(("cpu-", "cpuss", "cpu_")):
                cpu.append(z + "/temp")
            elif t.startswith(("gpu", "mali")):
                gpu.append(z + "/temp")
        _THERMAL_ZONES = (cpu, gpu)
    return _THERMAL_ZONES


def _max_temp(paths) -> "int | None":
    vals = [v for v in (_read_int(p) for p in paths) if v is not None]
    if not vals:
        return None
    t = max(vals)
    return round(t / 1000) if t > 1000 else t


def get_hw_stats(gpu: bool) -> dict:
    """
    Température, fréquence et charge du composant qui calcule, lisibles sans root :
      GPU : {"temp": °C, "freq": "578MHz", "busy": "99%"} (Adreno via /sys/class/kgsl ;
            Mali : température seule) ;
      CPU : {"temp": °C, "freq": "2.7GHz", "busy": None} (/proc/stat, nécessaire pour la
            charge CPU, est interdit aux applications depuis Android 8).
    Les valeurs illisibles valent None.
    """
    cpu_zones, gpu_zones = _thermal_zones()
    if gpu:
        k = "/sys/class/kgsl/kgsl-3d0"
        temp = _read_int(k + "/temp")
        temp = round(temp / 1000) if temp and temp > 1000 else temp
        if temp is None:
            temp = _max_temp(gpu_zones)
        freq = _read_int(k + "/devfreq/cur_freq")
        busy = _read_int(k + "/gpu_busy_percentage")
        return {"temp": temp, "freq": f"{freq // 1_000_000}MHz" if freq else None,
                "busy": f"{busy}%" if busy is not None else None}
    freqs = [v for v in (_read_int(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
                         for i in range(os.cpu_count() or 8)) if v]
    return {"temp": _max_temp(cpu_zones),
            "freq": f"{max(freqs) / 1_000_000:.1f}GHz" if freqs else None, "busy": None}


def temp_color(temp) -> str:
    if temp is None:
        return "#888888"
    return "#3DDC84" if temp < 55 else ("#FFD32A" if temp < 75 else "#FF5555")

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
    threads = recommended_cpu_threads()

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
            "cache_type_v": "f16",   # Type de quantification pour les valeurs KV (f16, q8_0, q4_0)
            "speculative": "auto"    # MTP (--spec-type draft-mtp) : auto = si le modèle l'embarque et en mode CPU
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
