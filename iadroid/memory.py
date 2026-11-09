"""
IADroid - Mémoire persistante de l'agent

Chaque note est un fichier texte ~/.iadroid/memory/<nom>.md.
L'ensemble des notes est injecté dans le prompt système à chaque conversation,
pour que le modèle se souvienne de l'utilisateur d'une session à l'autre.
"""

import re
from pathlib import Path
from typing import List, Tuple

from .config import MEMORY_DIR

# Taille maximale injectée dans le prompt système (chaque token coûte au traitement du prompt)
MAX_NOTE_CHARS = 400
MAX_TOTAL_CHARS = 2000


def _slug(name: str) -> str:
    """Nom de fichier sûr à partir du nom de la note (pas de / ni de ..)."""
    s = re.sub(r"[^\w\-]+", "-", name.strip().lower(), flags=re.UNICODE).strip("-")
    return s[:60] or "note"


def list_notes() -> List[Tuple[str, str]]:
    """Retourne la liste (nom, contenu) des notes, triée par nom."""
    if not MEMORY_DIR.exists():
        return []
    notes = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        try:
            notes.append((f.stem, f.read_text(encoding="utf-8", errors="replace").strip()))
        except Exception:
            continue
    return notes


def save_note(name: str, content: str) -> str:
    """Crée ou remplace une note. Retourne le nom effectif."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(name)
    (MEMORY_DIR / f"{slug}.md").write_text(content.strip()[:MAX_NOTE_CHARS * 3] + "\n", encoding="utf-8")
    return slug


def delete_note(name: str) -> bool:
    path = MEMORY_DIR / f"{_slug(name)}.md"
    if path.exists():
        path.unlink()
        return True
    return False


def clear_notes() -> int:
    n = 0
    for f in MEMORY_DIR.glob("*.md"):
        f.unlink()
        n += 1
    return n


def memory_prompt_block() -> str:
    """Bloc de texte ajouté au prompt système, ou chaîne vide s'il n'y a aucune note."""
    notes = list_notes()
    if not notes:
        return ""
    lines = []
    total = 0
    for name, content in notes:
        text = " ".join(content.split())
        if len(text) > MAX_NOTE_CHARS:
            text = text[:MAX_NOTE_CHARS] + "…"
        line = f"- {name} : {text}"
        if total + len(line) > MAX_TOTAL_CHARS:
            lines.append("- (autres notes non affichées)")
            break
        lines.append(line)
        total += len(line)
    return "Notes mémorisées lors des conversations précédentes :\n" + "\n".join(lines)
