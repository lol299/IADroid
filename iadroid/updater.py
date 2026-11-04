"""
IADroid - Système de Mise à Jour Automatique via GitHub
Vérifie les nouveaux commits/versions sur https://github.com/lol299/IADroid
et applique les mises à jour proprement en préservant les profils et modèles.
"""

import os
import sys
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm

from .config import IADROID_HOME, ANDROID_GREEN, CYAN, YELLOW
from . import __version__

console = Console()

REPO_OWNER = "lol299"
REPO_NAME = "IADroid"
GITHUB_API_COMMITS = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/main"
GITHUB_API_RELEASES = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
CACHE_FILE = IADROID_HOME / "update_cache.json"

def get_project_root() -> Path:
    """Retourne la racine du projet IADroid contenant .git."""
    curr = Path(__file__).resolve().parent.parent
    if (curr / ".git").exists() or (curr / "requirements.txt").exists():
        return curr
    return Path.cwd()

def ensure_git_repo(root: Path) -> bool:
    """Si .git est manquant (ex: copie manuelle), initialise git et lie le dépôt officiel GitHub."""
    try:
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, timeout=2)
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, timeout=5)
            subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{REPO_OWNER}/{REPO_NAME}.git"], cwd=root, capture_output=True, timeout=5)
            subprocess.run(["git", "fetch", "origin", "main", "--quiet"], cwd=root, capture_output=True, timeout=15)
            subprocess.run(["git", "reset", "--mixed", "origin/main"], cwd=root, capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def get_local_commit_sha() -> Optional[str]:
    """Récupère le SHA court du commit git local."""
    root = get_project_root()
    ensure_git_repo(root)
    try:
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, timeout=2)
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None

def get_local_commit_full() -> Tuple[Optional[str], Optional[str]]:
    """Retourne (sha_court, message) du dernier commit local."""
    root = get_project_root()
    ensure_git_repo(root)
    try:
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, timeout=2)
        res = subprocess.run(
            ["git", "log", "-1", "--format=%h %s"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split(" ", 1)
            sha = parts[0]
            msg = parts[1] if len(parts) > 1 else ""
            return sha, msg
    except Exception:
        pass
    return None, None

class UpdateChecker:
    @staticmethod
    def check_github_api(timeout: int = 4) -> Optional[Dict[str, Any]]:
        """Interroge l'API GitHub pour obtenir le dernier commit sur la branche main."""
        req = urllib.request.Request(
            GITHUB_API_COMMITS,
            headers={
                "User-Agent": "IADroid-Updater",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    sha_full = data.get("sha", "")
                    commit_obj = data.get("commit", {})
                    msg = commit_obj.get("message", "").splitlines()[0] if commit_obj.get("message") else ""
                    author = commit_obj.get("author", {}).get("name", "Inconnu")
                    date = commit_obj.get("author", {}).get("date", "")[:10]
                    return {
                        "remote_sha": sha_full[:7],
                        "remote_sha_full": sha_full,
                        "message": msg,
                        "author": author,
                        "date": date
                    }
        except Exception:
            pass
        return None

    @staticmethod
    def check_git_remote(timeout: int = 6) -> Optional[Dict[str, Any]]:
        """Effectue un git fetch origin main et compare les branches."""
        root = get_project_root()
        if not (root / ".git").exists():
            return None

        try:
            # Récupération silencieuse des métadonnées distantes
            subprocess.run(
                ["git", "fetch", "origin", "main", "--quiet"],
                cwd=root,
                capture_output=True,
                timeout=timeout
            )

            # Nombre de commits de retard
            res_count = subprocess.run(
                ["git", "rev-list", "HEAD..origin/main", "--count"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=3
            )
            behind_count = int(res_count.stdout.strip()) if res_count.returncode == 0 and res_count.stdout.strip().isdigit() else 0

            # Dernier commit distant
            res_log = subprocess.run(
                ["git", "log", "origin/main", "-1", "--format=%h|%s|%an|%as"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=3
            )
            if res_log.returncode == 0 and res_log.stdout.strip():
                parts = res_log.stdout.strip().split("|")
                return {
                    "remote_sha": parts[0] if len(parts) > 0 else "",
                    "message": parts[1] if len(parts) > 1 else "",
                    "author": parts[2] if len(parts) > 2 else "",
                    "date": parts[3] if len(parts) > 3 else "",
                    "behind_count": behind_count
                }
        except Exception:
            pass
        return None

    @classmethod
    def check_for_updates(cls) -> Dict[str, Any]:
        """Vérifie si une mise à jour est disponible (via git ou GitHub API)."""
        local_sha, local_msg = get_local_commit_full()
        
        # 1. Tenter via git remote d'abord
        info = cls.check_git_remote()
        
        # 2. Fallback via GitHub REST API si git remote n'a pas répondu
        if not info:
            info = cls.check_github_api()

        if not info:
            return {
                "success": False,
                "error": "Impossible de contacter GitHub. Vérifiez votre connexion Internet.",
                "local_sha": local_sha or "inconnu",
                "local_version": __version__
            }

        remote_sha = info.get("remote_sha", "")
        has_update = False

        if "behind_count" in info:
            has_update = info["behind_count"] > 0
        elif local_sha and remote_sha:
            has_update = local_sha.lower() != remote_sha.lower()
        elif not local_sha and remote_sha:
            has_update = True

        # Liste des commits en attente si git est dispo
        incoming_commits = []
        if has_update:
            try:
                res_commits = subprocess.run(
                    ["git", "log", "HEAD..origin/main", "--oneline", "-n", "5"],
                    cwd=get_project_root(),
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if res_commits.returncode == 0 and res_commits.stdout.strip():
                    incoming_commits = res_commits.stdout.strip().splitlines()
            except Exception:
                pass

        return {
            "success": True,
            "has_update": has_update,
            "local_sha": local_sha or "inconnu",
            "local_msg": local_msg or "",
            "local_version": __version__,
            "remote_sha": remote_sha,
            "remote_msg": info.get("message", ""),
            "remote_author": info.get("author", ""),
            "remote_date": info.get("date", ""),
            "behind_count": info.get("behind_count", 1 if has_update else 0),
            "incoming_commits": incoming_commits
        }

    @classmethod
    def cached_check_has_update(cls) -> bool:
        """Vérification rapide non bloquante mise en cache pour le bandeau d'accueil."""
        now = time.time()
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                last_check = data.get("timestamp", 0)
                # Cache valide pendant 2 heures
                if now - last_check < 7200:
                    return bool(data.get("has_update", False))
            except Exception:
                pass

        # Nouveau check silencieux (timeout court)
        try:
            res = cls.check_for_updates()
            has_update = res.get("has_update", False)
            CACHE_FILE.write_text(json.dumps({
                "timestamp": now,
                "has_update": has_update,
                "remote_sha": res.get("remote_sha", "")
            }), encoding="utf-8")
            return has_update
        except Exception:
            return False

def apply_update() -> Tuple[bool, str]:
    """Applique la mise à jour git pull, met à jour les dépendances et restaure les permissions."""
    root = get_project_root()
    ensure_git_repo(root)
    if not (root / ".git").exists():
        return False, "Le dossier n'est pas un dépôt Git cloné valide."

    # 1. git pull
    try:
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, timeout=2)
        pull_res = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30
        )
        if pull_res.returncode != 0:
            # En cas de conflit ou de divergence, tenter pull standard
            pull_res = subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30
            )
            if pull_res.returncode != 0:
                return False, f"Échec de git pull : {pull_res.stderr.strip() or pull_res.stdout.strip()}"
    except Exception as e:
        return False, f"Erreur lors du pull Git : {e}"

    # 2. Mise à jour des dépendances Python (requirements.txt)
    req_file = root / "requirements.txt"
    if req_file.exists():
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--no-warn-script-location"],
                cwd=root,
                capture_output=True,
                timeout=60
            )
        except Exception:
            pass

    # 3. Permissions d'exécution
    for pattern in ("iadroid.py", "main.py", "scripts/*.sh"):
        for p in root.glob(pattern):
            try:
                p.chmod(p.stat().st_mode | 0o111)
            except Exception:
                pass

    # 4. Shebang Termux
    if shutil.which("termux-fix-shebang"):
        try:
            subprocess.run(["termux-fix-shebang", "iadroid.py", "main.py", "scripts/*.sh"], cwd=root, capture_output=True)
        except Exception:
            pass

    # Invalider le cache de mise à jour
    if CACHE_FILE.exists():
        try:
            CACHE_FILE.unlink()
        except Exception:
            pass

    return True, "Mise à jour téléchargée et installée avec succès !"

def run_update_interactive():
    """Interface interactive pour /update."""
    console.print(Panel(
        f"[bold {ANDROID_GREEN}]🔄 Vérification des Mises à Jour IADroid sur GitHub...[/bold {ANDROID_GREEN}]\n"
        f"[dim]Dépôt officiel : https://github.com/{REPO_OWNER}/{REPO_NAME}[/dim]",
        border_style=ANDROID_GREEN,
        expand=False
    ))

    with console.status("[bold cyan]Interrogation du dépôt distant...[/bold cyan]", spinner="dots"):
        res = UpdateChecker.check_for_updates()

    if not res.get("success"):
        console.print(f"\n[red]✗ {res.get('error', 'Erreur inconnue')}[/red]\n")
        return

    local_sha = res.get("local_sha", "")
    remote_sha = res.get("remote_sha", "")
    has_update = res.get("has_update", False)
    behind = res.get("behind_count", 0)

    if not has_update:
        console.print(f"\n[bold {ANDROID_GREEN}]✓ IADroid est déjà parfaitement à jour ![/bold {ANDROID_GREEN}]")
        console.print(f"• Version actuelle : [bold white]v{__version__}[/bold white] (commit [cyan]{local_sha}[/cyan])")
        console.print(f"• Dernier commit   : [dim]{res.get('local_msg')}[/dim]\n")
        return

    # Tableau des détails de la mise à jour
    table = Table(title="🚀 Nouvelle Mise à Jour Disponible !", border_style=ANDROID_GREEN)
    table.add_column("Propriété", style="bold cyan")
    table.add_column("Valeur", style="white")

    table.add_row("Version locale", f"v{__version__} ({local_sha})")
    table.add_row("Version distante", f"commit {remote_sha} ({res.get('remote_date', '')})")
    table.add_row("Auteur distant", res.get("remote_author", ""))
    table.add_row("Dernier message", res.get("remote_msg", ""))
    table.add_row("Commits de retard", f"[bold yellow]{behind}[/bold yellow]")

    console.print(table)

    incoming = res.get("incoming_commits", [])
    if incoming:
        console.print("\n[bold cyan]Nouveaux changements disponibles :[/bold cyan]")
        for c in incoming:
            console.print(f"  [yellow]•[/yellow] {c}")

    console.print(f"\n[dim]Note : Vos profils de modèles (~/.iadroid/presets) et vos modèles GGUF sont conservés.[/dim]\n")

    confirm = Confirm.ask("Voulez-vous installer cette mise à jour maintenant ?", default=True)
    if not confirm:
        console.print("[yellow]Mise à jour reportée.[/yellow]\n")
        return

    with console.status("[bold green]Application de la mise à jour (git pull & dépendances)...[/bold green]", spinner="dots"):
        success, message = apply_update()

    if success:
        console.print(f"\n[bold {ANDROID_GREEN}]✓ {message}[/bold {ANDROID_GREEN}]")
        console.print(f"[bold cyan]👉 Veuillez redémarrer IADroid pour appliquer tous les nouveaux changements.[/bold cyan]\n")
    else:
        console.print(f"\n[red]✗ {message}[/red]\n")
