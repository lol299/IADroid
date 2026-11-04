"""
IADroid - Gestionnaire de Crash et Rapports d'Erreurs
Génère un fichier de log détaillé dans les Téléchargements en cas de plantage
et affiche un message clair avec le fichier et la ligne concernés.
"""

import os
import sys
import datetime
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from rich.console import Console
from rich.panel import Panel

from .config import IADROID_HOME, get_ram_info
from .version import __version__

console = Console()

def get_downloads_dir() -> Path:
    """Recherche le dossier Téléchargements accessible sur Android Termux."""
    candidates = [
        Path.home() / "storage" / "downloads",
        Path("/sdcard/Download"),
        Path("/storage/emulated/0/Download"),
        IADROID_HOME / "logs"
    ]
    for p in candidates:
        try:
            if p.exists() and os.access(str(p), os.W_OK):
                return p
        except Exception:
            continue
    fallback = IADROID_HOME / "logs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

def record_crash(exc: BaseException, extra_context: Optional[Dict[str, Any]] = None) -> Tuple[Path, str, str, str, str]:
    """
    Enregistre le rapport de crash dans un fichier .log dans Téléchargements.
    Retourne (chemin_log, fichier_concerne, ligne_concernee, type_erreur, message_erreur).
    """
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    file_timestamp = now.strftime("%Y%m%d_%H%M%S")

    exc_type = type(exc).__name__
    exc_msg = str(exc)
    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    tb_list = traceback.extract_tb(exc.__traceback__)
    last_frame = tb_list[-1] if tb_list else None
    concerned_file = last_frame.filename if last_frame else "inconnu"
    concerned_line = str(last_frame.lineno) if last_frame else "?"
    concerned_func = last_frame.name if last_frame else "?"

    # Nettoyer le chemin pour afficher un chemin relatif lisible si dans le projet
    try:
        project_root = str(Path(__file__).resolve().parent.parent)
        if concerned_file.startswith(project_root):
            concerned_file = os.path.relpath(concerned_file, project_root)
    except Exception:
        pass

    used_ram, total_ram, ram_pct, free_ram = get_ram_info()
    ctx = extra_context or {}

    log_content = f"""================================================================================
         RAPPORT D'ERREUR ET DE CRASH IADROID (ASSISTANT IA ANDROID)
================================================================================
Date et Heure      : {now_str}
Version d'IADroid  : v{__version__}
Environnement      : Android (Termux)
Dépôt officiel     : https://github.com/lol299/IADroid
================================================================================
NOTE IMPORTANTE :
Une erreur inattendue est survenue dans IADroid.
Pour permettre une correction rapide, veuillez transmettre l'intégralité
de ce fichier de log au développeur (ou ouvrir une Issue sur GitHub :
https://github.com/lol299/IADroid/issues).
================================================================================

[RÉSUMÉ DU CRASH]
Type d'erreur      : {exc_type}
Message            : {exc_msg}
Fichier concerné   : {concerned_file}
Ligne concernée    : {concerned_line}
Fonction / Portée  : {concerned_func}

[TRACEBACK DÉTAILLÉ]
{tb_str}

[ENVIRONNEMENT SYSTÈME & MATÉRIEL]
Python             : {sys.version.splitlines()[0]}
Plateforme         : {sys.platform}
Dossier courant    : {os.getcwd()}
RAM Utilisée       : {used_ram:.2f} Go ({ram_pct:.1f}%)
RAM Totale         : {total_ram:.2f} Go
RAM Restante       : {free_ram:.2f} Go
Profil actif       : {ctx.get('preset', 'Non défini')}
Matériel actif     : {ctx.get('device', 'Non défini')}
Serveur llama      : {ctx.get('server_status', 'Non actif')}
================================================================================
"""

    dest_dir = get_downloads_dir()
    log_file = dest_dir / f"iadroid_crash_{file_timestamp}.log"
    try:
        log_file.write_text(log_content, encoding="utf-8", errors="replace")
    except Exception:
        # Fallback si écriture impossible dans les Téléchargements
        dest_dir = IADROID_HOME / "logs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        log_file = dest_dir / f"iadroid_crash_{file_timestamp}.log"
        log_file.write_text(log_content, encoding="utf-8", errors="replace")

    return log_file, concerned_file, concerned_line, exc_type, exc_msg

def display_crash_ui(log_file: Path, concerned_file: str, concerned_line: str, exc_type: str, exc_msg: str):
    """Affiche une interface propre et lisible pour l'utilisateur en cas de plantage."""
    # Restaurer l'affichage terminal (défilement plein écran et curseur visible)
    try:
        sys.stdout.write("\033[r\033[?25h\n")
        sys.stdout.flush()
    except Exception:
        pass

    details = (
        f"• [dim]Fichier concerné dans Termux :[/dim] [bold cyan]{concerned_file}[/bold cyan] [dim](Ligne {concerned_line})[/dim]\n"
        f"• [dim]Type d'erreur :[/dim] [bold yellow]{exc_type}[/bold yellow]\n"
        f"• [dim]Message :[/dim] [white]{exc_msg}[/white]\n\n"
        f"[bold green]📄 Un fichier de log complet a été créé dans vos Téléchargements :[/bold green]\n"
        f"👉 [bold underline cyan]{log_file}[/bold underline cyan]\n\n"
        f"[dim yellow]💬 Veuillez transmettre ce fichier au développeur pour corriger ce problème ![/dim yellow]"
    )

    console.print()
    console.print(Panel(
        details,
        title="[bold red]❌ IADroid a rencontré une erreur inattendue[/bold red]",
        border_style="red",
        expand=False
    ))
    console.print()

def handle_crash(exc: BaseException, extra_context: Optional[Dict[str, Any]] = None):
    """Point d'entrée unique pour capturer, journaliser et afficher un crash."""
    log_file, concerned_file, concerned_line, exc_type, exc_msg = record_crash(exc, extra_context)
    display_crash_ui(log_file, concerned_file, concerned_line, exc_type, exc_msg)
