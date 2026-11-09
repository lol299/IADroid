"""
IADroid - Mascotte Pixel Android & Bannières CLI
"""

import shutil
from rich.console import Console
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from .config import ANDROID_GREEN, CYAN, YELLOW
from .version import __version__

from .config import console  # console partagée : une seule instance pour que rich.Live gère tous les affichages

# Mascotte Android Pixel complète (Style 8-bit / Invaders)
MASCOT_PIXEL_FULL = f"""[{ANDROID_GREEN}]
     ██                  ██     
   ██████████████████████████   
   ████  ██████████████  ████   
   ██████████████████████████   
                                
 █  ██████████████████████  █   
 █  ██████████████████████  █   
 █  ██████████████████████  █   
     ████████████████████       
         ████      ████         
         ████      ████         [/{ANDROID_GREEN}]"""

# Mascotte Android Pixel compacte (pour les écrans étroits de smartphone)
MASCOT_PIXEL_COMPACT = f"""[{ANDROID_GREEN}]
  ▄   ▄  
 ▄█▀███▀█▄
 ██ █ █ ██
 ▀███████▀
 ██ ▀▀▀ ██[/{ANDROID_GREEN}]"""

def display_welcome_banner(preset_data: dict = None, has_update: bool = False):
    """Affiche la mascotte Android et les informations du profil actif."""
    term_width = shutil.get_terminal_size().columns
    is_narrow = term_width < 70

    preset_name = preset_data.get("name", "Aucun") if preset_data else "Aucun"
    hardware = preset_data.get("hardware", {}) if preset_data else {}
    device = hardware.get("device", "gpu").upper()
    ngl = hardware.get("ngl", 99)
    ctx_size = preset_data.get("context", {}).get("ctx_size", 4096) if preset_data else 4096
    model_name = "Non configuré"
    if preset_data and preset_data.get("model_path"):
        import os
        model_name = os.path.basename(preset_data["model_path"])

    device_str = f"Vulkan GPU (ngl: {ngl})" if device == "GPU" else "CPU ARM64 (NEON)"
    device_style = "bold green" if device == "GPU" else "bold yellow"

    info_text = Text()
    info_text.append("IADroid CLI", style=f"bold {ANDROID_GREEN}")
    info_text.append(f" v{__version__}\n", style="bold white")
    info_text.append("─" * (min(38, term_width - 10)) + "\n", style="dim")
    info_text.append("• Profil actif : ", style="dim")
    info_text.append(f"{preset_name}\n", style="bold cyan")
    info_text.append("• Modèle       : ", style="dim")
    info_text.append(f"{model_name[:24]}...\n" if len(model_name) > 27 else f"{model_name}\n", style="white")
    info_text.append("• Matériel     : ", style="dim")
    info_text.append(f"{device_str}\n", style=device_style)
    info_text.append("• Contexte     : ", style="dim")
    info_text.append(f"{ctx_size} tokens\n", style="magenta")

    if has_update:
        info_text.append("• Mise à jour  : ", style="dim")
        info_text.append("Disponible sur GitHub (/update)\n", style="bold yellow")

    info_text.append("─" * (min(38, term_width - 10)) + "\n", style="dim")
    info_text.append("Commandes rapides : ", style="dim")
    info_text.append("/help", style=f"bold {ANDROID_GREEN}")
    info_text.append(" | ", style="dim")
    info_text.append("/update", style="bold yellow")
    info_text.append(" | ", style="dim")
    info_text.append("/create-preset", style=f"bold {ANDROID_GREEN}")

    if is_narrow:
        # Écran étroit : empilement vertical
        console.print(MASCOT_PIXEL_COMPACT)
        console.print(Panel(info_text, border_style=ANDROID_GREEN, expand=False))
    else:
        # Écran large : colonnes côte à côte
        console.print(Columns([
            MASCOT_PIXEL_FULL,
            Panel(info_text, border_style=ANDROID_GREEN, expand=False)
        ]))

def print_help_table():
    """Affiche le tableau d'aide des commandes slash."""
    from rich.table import Table

    table = Table(title="🤖 Commandes Disponibles dans IADroid", border_style=ANDROID_GREEN)
    table.add_column("Commande", style="bold cyan", no_wrap=True)
    table.add_column("Arguments", style="yellow")
    table.add_column("Description", style="white")

    table.add_row("/load", "[nom]", "Charger un profil en RAM (sans nom : menu de choix)")
    table.add_row("/create-preset", "", "Créer un profil dans un formulaire (nom, modèle, matériel, réflexion…)")
    table.add_row("/profil", "[nom]", "Choisir un profil puis modifier ses réglages dans un formulaire")
    table.add_row("/profil_config_env", "<nom>", "Afficher le fichier brut de configuration du profil")
    table.add_row("/profil_Prompt_Systeme", "<nom>", "Afficher et modifier directement le prompt système du profil")
    table.add_row("/device", "[gpu|cpu] [ngl]", "Basculer entre GPU (Vulkan) et CPU (avec nb de couches ngl optionnel)")
    table.add_row("/fa", "<on|off>", "Activer ou désactiver Flash Attention (-fa)")
    table.add_row("/mtp", "[auto|on|off]", "Décodage spéculatif MTP (modèles qui l'embarquent, ex. Qwen3.5) : +10 à +35 % en CPU")
    table.add_row("/quantize", "[Q4_0|Q8_0|…]", "Convertir le modèle du profil (Q4_0 : le plus rapide en mode CPU)")
    table.add_row("/effort", "[niveau]", "Effort de réflexion : curseur ←/→ (s = cette session seulement)")
    table.add_row("/think", "<on|off>", "Activer ou désactiver rapidement le mode réflexion (Thinking)")
    table.add_row("/show-think", "<on|off>", "Aperçu de la réflexion (ON: 5 mots en direct, OFF: masqué)")
    table.add_row("/presets", "", "Lister tous les profils enregistrés")
    table.add_row("/use", "[nom]", "Activer un profil sans le charger (sans nom : menu de choix)")
    table.add_row("/reset", "", "Réinitialiser le contexte de la conversation en cours")
    table.add_row("/update", "", "Vérifier et installer les mises à jour d'IADroid depuis GitHub")
    table.add_row("/check-gpu", "", "Diagnostic GPU Vulkan et liaison automatique du pilote Android")
    table.add_row("/boost-gpu", "", "Forcer la fréquence maximale du GPU (1000 MHz pour Adreno 750)")
    table.add_row("/cmake-thread-compile", "<2|4|6|8>", "Choisir le nombre de threads de compilation avec grille des risques")
    table.add_row("/cmake-config-auto", "", "Reconfigurer automatiquement CMake avec support Vulkan GPU")
    table.add_row("/cmake-recompiler", "", "Vérifier les màj llama.cpp (git pull) et recompiler")
    table.add_row("/cmake-del-cache", "", "Supprimer le dossier build et le cache CMake")
    table.add_row("/run", "", "Démarrer le modèle actif en arrière-plan et activer la conversation")
    table.add_row("/stop", "", "Arrêter le serveur actif et libérer immédiatement la RAM et le GPU")
    table.add_row("/llama-cli", "", "Lancer une session brute interactive avec llama-cli (expert débug)")
    table.add_row("/agent", "<on|off>", "Mode agent : l'IA lit/écrit des fichiers, lance des commandes, va sur le web (web on|off, steps N)")
    table.add_row("/allow", "[chemin|all|remove]", "Dossiers autorisés pour l'agent (all : Termux + /storage/emulated/0)")
    table.add_row("/permissions", "[ask|auto]", "ask : confirmer chaque écriture/commande · auto : tout autoriser")
    table.add_row("/memory", "[del <nom>|clear]", "Notes que l'IA garde sur vous d'une conversation à l'autre")
    table.add_row("/jobs", "[kill <id>|kill all]", "Commandes lancées en arrière-plan par l'agent (sortie, état, arrêt)")
    table.add_row("!", "<commande>", "Lancer une commande ou un script dans le terminal (sortie jointe au prochain message)")
    table.add_row("/help", "", "Afficher cette aide")
    table.add_row("/exit", "", "Quitter IADroid")

    console.print(table)
