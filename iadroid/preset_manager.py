"""
IADroid - Gestionnaire de Profils (Presets) et Générateur de Lignes de Commande
"""

import os
import json
import time
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from .config import (
    PRESETS_DIR,
    ACTIVE_PRESET_FILE,
    LLAMA_CLI_PATH,
    ensure_directories,
    get_default_preset,
    ANDROID_GREEN
)

console = Console()

def is_llama_running() -> bool:
    """Vérifie si un processus llama-cli ou llama-server est en cours d'exécution."""
    try:
        res = subprocess.run(["pgrep", "-f", "llama-"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return True
    except Exception:
        pass

    # Méthode alternative via ps
    try:
        res = subprocess.run(["ps", "-ef"], capture_output=True, text=True)
        return any(b in res.stdout for b in ("llama-cli", "llama-server"))
    except Exception:
        return False

def stop_llama_processes() -> bool:
    """Tue proprement les processus llama-cli ou llama-server pour libérer la RAM et le GPU."""
    stopped = False
    try:
        subprocess.run(["pkill", "-15", "-f", "llama-"], capture_output=True)
        time.sleep(0.3)
        subprocess.run(["pkill", "-9", "-f", "llama-"], capture_output=True)
        stopped = True
    except Exception:
        pass
    return stopped

class PresetManager:
    def __init__(self):
        ensure_directories()

    def list_presets(self) -> List[str]:
        """Retourne la liste des noms de profils disponibles."""
        return [f.stem for f in PRESETS_DIR.glob("*.json")]

    def get_preset_path(self, name: str) -> Path:
        """Retourne le chemin absolu du fichier JSON d'un profil."""
        return PRESETS_DIR / f"{name}.json"

    def preset_exists(self, name: str) -> bool:
        """Vérifie si un profil existe."""
        return self.get_preset_path(name).exists()

    def load_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """Charge un profil depuis son fichier JSON."""
        path = self.get_preset_path(name)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            console.print(f"[red]Erreur lors du chargement du profil '{name}' : {e}[/red]")
            return None

    def save_preset(self, name: str, data: Dict[str, Any]) -> bool:
        """Enregistre un profil dans son fichier JSON."""
        path = self.get_preset_path(name)
        data["name"] = name
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            console.print(f"[red]Erreur lors de la sauvegarde du profil '{name}' : {e}[/red]")
            return False

    def get_active_preset_name(self) -> Optional[str]:
        """Retourne le nom du profil actif."""
        if ACTIVE_PRESET_FILE.exists():
            try:
                name = ACTIVE_PRESET_FILE.read_text(encoding="utf-8").strip()
                if self.preset_exists(name):
                    return name
            except Exception:
                pass
        # Fallback : premier profil existant
        presets = self.list_presets()
        if presets:
            self.set_active_preset_name(presets[0])
            return presets[0]
        return None

    def set_active_preset_name(self, name: str) -> bool:
        """Définit le profil actif."""
        if not self.preset_exists(name):
            console.print(f"[red]Le profil '{name}' n'existe pas.[/red]")
            return False
        try:
            ACTIVE_PRESET_FILE.write_text(name, encoding="utf-8")
            return True
        except Exception as e:
            console.print(f"[red]Impossible de définir le profil actif : {e}[/red]")
            return False

    def get_active_preset(self) -> Optional[Dict[str, Any]]:
        """Retourne les données complètes du profil actif."""
        name = self.get_active_preset_name()
        if name:
            return self.load_preset(name)
        return None

    def display_preset_env(self, name: str):
        """Affiche le fichier de configuration JSON brut avec coloration syntaxique (/profil_config_env)."""
        data = self.load_preset(name)
        if not data:
            console.print(f"[red]Profil '{name}' introuvable.[/red]")
            return

        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=True)
        console.print(f"\n[bold {ANDROID_GREEN}]📄 Configuration brute du profil : {name}[/bold {ANDROID_GREEN}] ({self.get_preset_path(name)})")
        console.print(syntax)

    def display_presets_table(self):
        """Affiche la liste de tous les profils sous forme de tableau Rich."""
        presets = self.list_presets()
        if not presets:
            console.print("[yellow]Aucun profil enregistré. Utilisez /create-preset pour en créer un.[/yellow]")
            return

        active_name = self.get_active_preset_name()
        table = Table(title="📋 Profils IADroid Enregistrés", border_style=ANDROID_GREEN)
        table.add_column("Actif", justify="center", style="bold green", width=6)
        table.add_column("Nom", style="bold cyan")
        table.add_column("Modèle", style="white")
        table.add_column("Matériel", style="yellow")
        table.add_column("Contexte", style="magenta")
        table.add_column("Réflexion", style="green")

        for p_name in presets:
            data = self.load_preset(p_name) or {}
            is_active = "★" if p_name == active_name else ""
            m_path = data.get("model_path", "")
            m_name = os.path.basename(m_path) if m_path else "Non défini"
            hw = data.get("hardware", {})
            dev = hw.get("device", "gpu").upper()
            ctx = f"{data.get('context', {}).get('ctx_size', 4096)} t"
            reasoning = "Oui" if data.get("reasoning", {}).get("enabled", False) else "Non"
            table.add_row(is_active, p_name, m_name, dev, ctx, reasoning)

        console.print(table)

    def set_device_mode(self, name: str, device: str) -> bool:
        """Bascule le profil entre CPU et GPU (/device <cpu|gpu>)."""
        data = self.load_preset(name)
        if not data:
            console.print(f"[red]Profil '{name}' introuvable.[/red]")
            return False

        device = device.lower()
        if device not in ("cpu", "gpu"):
            console.print("[red]Le mode doit être 'cpu' ou 'gpu'.[/red]")
            return False

        data.setdefault("hardware", {})
        data["hardware"]["device"] = device
        data["hardware"]["ngl"] = 99 if device == "gpu" else 0

        self.save_preset(name, data)
        status_color = "green" if device == "gpu" else "yellow"
        console.print(f"[{status_color}]✓ Profil '{name}' configuré en mode {device.upper()} (ngl: {data['hardware']['ngl']})[/{status_color}]")
        return True

    def set_reasoning_effort(self, name: str, effort: str) -> bool:
        """Modifie le niveau d'effort de réflexion (/effort <minimal|low|medium|high|xhigh|off>)."""
        data = self.load_preset(name)
        if not data:
            console.print(f"[red]Profil '{name}' introuvable.[/red]")
            return False

        effort = effort.lower().strip()
        valid_efforts = ("minimal", "low", "medium", "high", "xhigh", "off")
        if effort not in valid_efforts:
            console.print(f"[yellow]Niveau invalide. Choix possibles : {', '.join(valid_efforts)}[/yellow]")
            return False

        from .config import REASONING_TOKEN_BUDGETS
        rs = data.setdefault("reasoning", {})
        if effort == "off":
            rs["enabled"] = False
            rs["effort"] = "off"
            console.print(f"[yellow]✓ Mode réflexion désactivé pour le profil '{name}'.[/yellow]")
        else:
            rs["enabled"] = True
            rs["effort"] = effort
            rs["budget_tokens"] = REASONING_TOKEN_BUDGETS.get(effort, 1024)
            console.print(f"[bold {ANDROID_GREEN}]✓ Effort de réflexion configuré sur '{effort}' ({rs['budget_tokens']} tokens) pour '{name}'.[/bold {ANDROID_GREEN}]")

        self.save_preset(name, data)
        return True

    def build_llama_cli_command(self, preset_data: Dict[str, Any], interactive: bool = True) -> List[str]:
        """Génère la liste d'arguments pour invoquer llama-cli."""
        cmd = [str(LLAMA_CLI_PATH)]

        # Modèle
        model_path = preset_data.get("model_path")
        if not model_path:
            raise ValueError("Le chemin du modèle (model_path) est manquant dans le profil.")
        cmd.extend(["-m", model_path])

        # Projecteur de vision (multimodal)
        mmproj = preset_data.get("mmproj_path")
        if mmproj and os.path.exists(mmproj):
            cmd.extend(["--mmproj", mmproj])

        # Hardware
        hw = preset_data.get("hardware", {})
        ngl = hw.get("ngl", 99 if hw.get("device", "gpu") == "gpu" else 0)
        cmd.extend(["-ngl", str(ngl)])

        threads = hw.get("cpu_threads", 4)
        cmd.extend(["-t", str(threads)])

        batch = hw.get("batch", 512)
        ubatch = hw.get("ubatch", 128)
        cmd.extend(["-b", str(batch), "-ub", str(ubatch)])

        # Flash Attention (on|off explicite pour éviter que le flag suivant soit consommé)
        if hw.get("flash_attention", True):
            cmd.extend(["-fa", "on"])
        else:
            cmd.extend(["-fa", "off"])

        # RAM : mmap et mlock (sécurisé pour Android mobile)
        if not hw.get("mmap", True):
            cmd.append("--no-mmap")
        elif hw.get("mlock", False):
            cmd.append("--mlock")

        # Cache KV
        if hw.get("cache_type_k"):
            cmd.extend(["--cache-type-k", str(hw["cache_type_k"])])
        if hw.get("cache_type_v"):
            cmd.extend(["--cache-type-v", str(hw["cache_type_v"])])

        # Contexte
        ctx = preset_data.get("context", {})
        ctx_size = ctx.get("ctx_size", 4096)
        cmd.extend(["-c", str(ctx_size)])

        # Prompt système (en mode conversation -cnv, utiliser --system-prompt)
        sys_prompt = ctx.get("system_prompt", "")
        if sys_prompt:
            if interactive:
                cmd.extend(["--system-prompt", sys_prompt])
            else:
                cmd.extend(["-p", sys_prompt])

        # Sampling
        sampling = preset_data.get("sampling", {})
        if "temperature" in sampling:
            cmd.extend(["--temp", str(sampling["temperature"])])
        if "top_p" in sampling:
            cmd.extend(["--top-p", str(sampling["top_p"])])
        if "top_k" in sampling:
            cmd.extend(["--top-k", str(sampling["top_k"])])
        if "min_p" in sampling:
            cmd.extend(["--min-p", str(sampling["min_p"])])
        if "presence_penalty" in sampling:
            cmd.extend(["--presence-penalty", str(sampling["presence_penalty"])])
        if "repeat_penalty" in sampling:
            cmd.extend(["--repeat-penalty", str(sampling["repeat_penalty"])])

        # Retour de la commande nettoyée pour llama-cli
        return cmd

    def build_llama_stream_command(self, preset_data: Dict[str, Any], prompt: str) -> List[str]:
        """Génère la commande pour un flux de réponse en streaming direct dans le CLI."""
        cmd = [str(LLAMA_CLI_PATH)]

        model_path = preset_data.get("model_path")
        if not model_path:
            raise ValueError("Le chemin du modèle (model_path) est manquant.")
        cmd.extend(["-m", model_path])

        mmproj = preset_data.get("mmproj_path")
        if mmproj and os.path.exists(mmproj):
            cmd.extend(["--mmproj", mmproj])

        hw = preset_data.get("hardware", {})
        ngl = hw.get("ngl", 99 if hw.get("device", "gpu") == "gpu" else 0)
        cmd.extend(["-ngl", str(ngl)])
        cmd.extend(["-t", str(hw.get("cpu_threads", 4))])
        cmd.extend(["-b", str(hw.get("batch", 512)), "-ub", str(hw.get("ubatch", 128))])

        if hw.get("flash_attention", True):
            cmd.extend(["-fa", "on"])
        else:
            cmd.extend(["-fa", "off"])
        if not hw.get("mmap", True):
            cmd.append("--no-mmap")
        elif hw.get("mlock", False):
            cmd.append("--mlock")

        if hw.get("cache_type_k"):
            cmd.extend(["--cache-type-k", str(hw["cache_type_k"])])
        if hw.get("cache_type_v"):
            cmd.extend(["--cache-type-v", str(hw["cache_type_v"])])

        ctx_size = preset_data.get("context", {}).get("ctx_size", 4096)
        cmd.extend(["-c", str(ctx_size)])

        sampling = preset_data.get("sampling", {})
        for param, flag in [
            ("temperature", "--temp"),
            ("top_p", "--top-p"),
            ("top_k", "--top-k"),
            ("min_p", "--min-p"),
            ("presence_penalty", "--presence-penalty"),
            ("repeat_penalty", "--repeat-penalty"),
        ]:
            if param in sampling:
                cmd.extend([flag, str(sampling[param])])

        # Passer le prompt formaté sans réafficher le prompt et sans logs verbeux
        cmd.extend(["-p", prompt, "--no-display-prompt", "--simple-io"])
        return cmd

    def build_llama_server_command(self, preset_data: Dict[str, Any], host: str = "127.0.0.1", port: int = 8080) -> List[str]:
        """Génère la commande pour démarrer llama-server en arrière-plan."""
        from .config import LLAMA_SERVER_PATH
        cmd = [str(LLAMA_SERVER_PATH)]

        model_path = preset_data.get("model_path")
        if not model_path:
            raise ValueError("Le chemin du modèle (model_path) est manquant.")
        cmd.extend(["-m", model_path])

        mmproj = preset_data.get("mmproj_path")
        if mmproj and os.path.exists(mmproj):
            cmd.extend(["--mmproj", mmproj])

        hw = preset_data.get("hardware", {})
        ngl = hw.get("ngl", 99 if hw.get("device", "gpu") == "gpu" else 0)
        cmd.extend(["-ngl", str(ngl)])
        cmd.extend(["-t", str(hw.get("cpu_threads", 4))])
        cmd.extend(["-b", str(hw.get("batch", 512)), "-ub", str(hw.get("ubatch", 128))])

        if hw.get("flash_attention", True):
            cmd.extend(["-fa", "on"])
        else:
            cmd.extend(["-fa", "off"])
        if not hw.get("mmap", True):
            cmd.append("--no-mmap")
        elif hw.get("mlock", False):
            cmd.append("--mlock")

        if hw.get("cache_type_k"):
            cmd.extend(["--cache-type-k", str(hw["cache_type_k"])])
        if hw.get("cache_type_v"):
            cmd.extend(["--cache-type-v", str(hw["cache_type_v"])])

        ctx_size = preset_data.get("context", {}).get("ctx_size", 4096)
        cmd.extend(["-c", str(ctx_size)])

        cmd.extend(["--host", host, "--port", str(port)])
        return cmd
