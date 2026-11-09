"""
IADroid - Menus interactifs au clavier (prompt_toolkit, dans le fil de la conversation)

- slider()    : curseur horizontal (effort de réflexion…) ←/→, Entrée, s (session), Échap ;
- select()    : liste de choix ↑/↓, Entrée, Échap ;
- edit_form() : formulaire à champs (profil) ; Entrée modifie un champ, ←/→ change un choix.

Les menus s'effacent une fois fermés (erase_when_done) : seul le résultat reste à l'écran.
Touches de repli pour le clavier Termux : q = annuler, j/k = bas/haut, h/l = gauche/droite.
"""

import copy
import os
import shutil
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

GREEN = "#3DDC84"
CYAN = "#00D8D6"
YELLOW = "#FFD32A"
RED = "#FF5555"
GREY = "#777777"
DIM = "#555555"


def _cols() -> int:
    return max(30, min(shutil.get_terminal_size().columns - 1, 100))


def _rows() -> int:
    return max(10, shutil.get_terminal_size().lines)


def _clip(text: str, width: int) -> str:
    text = str(text).replace("\n", " ⏎ ")
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def _run(render: Callable[[], list], kb: KeyBindings):
    """Affiche un menu en ligne (pas en plein écran) et retourne la valeur passée à app.exit()."""
    control = FormattedTextControl(lambda: FormattedText(render()), focusable=True, show_cursor=False)

    @kb.add("c-c")
    def _(event):
        event.app.exit(result=None)

    app = Application(layout=Layout(Window(control, wrap_lines=False, dont_extend_height=True)),
                      key_bindings=kb, full_screen=False, erase_when_done=True, mouse_support=False)
    app.ttimeoutlen = 0.05  # Échap réagit tout de suite (sinon ~0,5 s d'attente d'une séquence)
    try:
        return app.run()
    except (KeyboardInterrupt, EOFError):
        return None


def _bind_cancel(kb: KeyBindings):
    @kb.add("escape", eager=True)
    @kb.add("q")
    def _(event):
        event.app.exit(result=None)


def _header(title: str) -> list:
    return [(DIM, "▔" * _cols() + "\n"), (f"bold {CYAN}", f"  {title}\n")]


# --------------------------------------------------------------------------- curseur

def slider(title: str, levels: Sequence[str], current: str, left: str = "", right: str = "",
           notes: Optional[Dict[str, str]] = None, session: bool = True,
           subtitle: str = "") -> Optional[Tuple[str, bool]]:
    """
    Curseur horizontal. Retourne (niveau, session_seulement) ou None si annulé.
    `notes[niveau]` : explication affichée sous le curseur.
    """
    levels = list(levels)
    state = {"i": levels.index(current) if current in levels else 0}
    notes = notes or {}
    gap = "   "
    row = gap.join(levels)
    starts, pos = [], 0
    for lv in levels:
        starts.append(pos)
        pos += len(lv) + len(gap)
    width = len(row)
    indent = "   "

    def render():
        i = state["i"]
        out = _header(title)
        if subtitle:
            out.append((GREY, f"  {subtitle}\n"))
        out.append(("", "\n"))
        if left or right:
            space = max(1, width - len(left) - len(right))
            out.append((GREY, indent + left + " " * space + right + "\n"))
        mark = starts[i] + len(levels[i]) // 2
        out.append((DIM, indent + "─" * mark))
        out.append((f"bold {GREEN}", "▲"))
        out.append((DIM, "─" * max(0, width - mark - 1) + "\n"))
        out.append(("", indent))
        for j, lv in enumerate(levels):
            if j:
                out.append(("", gap))
            out.append((f"bold {GREEN}" if j == i else GREY, lv))
        out.append(("", "\n"))
        note = notes.get(levels[i], "")
        if levels[i] == current:
            note = (note + " · " if note else "") + "réglage actuel"
        out.append((GREY, indent + note + "\n\n"))
        keys = "←/→ régler · Entrée valider" + (" · s cette session" if session else "") + " · Échap annuler"
        out.append((DIM, "  " + keys))
        return out

    kb = KeyBindings()
    _bind_cancel(kb)

    @kb.add("left")
    @kb.add("h")
    def _(event):
        state["i"] = max(0, state["i"] - 1)

    @kb.add("right")
    @kb.add("l")
    def _(event):
        state["i"] = min(len(levels) - 1, state["i"] + 1)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=(levels[state["i"]], False))

    if session:
        @kb.add("s")
        def _(event):
            event.app.exit(result=(levels[state["i"]], True))

    return _run(render, kb)


# --------------------------------------------------------------------------- liste de choix

def select(title: str, items: Sequence[Tuple[Any, str, str]], current: Any = None,
           subtitle: str = "", footer: str = "↑/↓ choisir · Entrée valider · Échap annuler"):
    """
    Liste de choix : items = [(valeur, libellé, détail)]. Retourne la valeur choisie ou None.
    Les éléments de valeur None sont des séparateurs non sélectionnables.
    """
    items = list(items)
    selectable = [k for k, it in enumerate(items) if it[0] is not None]
    if not selectable:
        return None
    start = next((k for k in selectable if items[k][0] == current), selectable[0])
    state = {"i": start}

    def render():
        cols = _cols()
        out = _header(title)
        if subtitle:
            out.append((GREY, f"  {subtitle}\n"))
        out.append(("", "\n"))
        visible = max(4, _rows() - 8)
        first = min(max(0, state["i"] - visible // 2), max(0, len(items) - visible))
        label_w = min(28, max((len(it[1]) for it in items), default=10))
        if first > 0:
            out.append((GREY, "    ↑ …\n"))
        for k in range(first, min(len(items), first + visible)):
            value, label, detail = items[k]
            if value is None:
                out.append((DIM, f"    {label}\n"))
                continue
            sel = k == state["i"]
            mark = "★ " if value == current and current is not None else "  "
            out.append((f"bold {GREEN}" if sel else "", ("❯ " if sel else "  ") + mark))
            out.append((f"bold {GREEN}" if sel else "", _clip(label, label_w).ljust(label_w)))
            if detail:
                out.append((GREY, "  " + _clip(detail, max(5, cols - label_w - 9))))
            out.append(("", "\n"))
        if first + visible < len(items):
            out.append((GREY, "    ↓ …\n"))
        out.append(("", "\n"))
        out.append((DIM, "  " + footer))
        return out

    def move(step):
        pos = selectable.index(state["i"]) if state["i"] in selectable else 0
        state["i"] = selectable[max(0, min(len(selectable) - 1, pos + step))]

    kb = KeyBindings()
    _bind_cancel(kb)

    @kb.add("up")
    @kb.add("k")
    def _(event):
        move(-1)

    @kb.add("down")
    @kb.add("j")
    def _(event):
        move(1)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=items[state["i"]][0])

    return _run(render, kb)


def confirm(title: str, yes: str = "Oui", no: str = "Non", default: bool = True, subtitle: str = "") -> bool:
    res = select(title, [(True, yes, ""), (False, no, "")], current=default, subtitle=subtitle)
    return bool(res)


# --------------------------------------------------------------------------- saisie

def ask_text(label: str, default: str = "", multiline: bool = False, hint: str = "") -> Optional[str]:
    """Saisie d'un texte (effacée une fois validée). None si annulée (Ctrl+C)."""
    toolbar = hint or ("Échap puis Entrée pour valider · Ctrl+C pour annuler" if multiline
                       else "Entrée pour valider · Ctrl+C pour annuler")
    try:
        session = PromptSession(erase_when_done=True, multiline=multiline)
        return session.prompt(f"  {label} › ", default=str(default or ""),
                              bottom_toolbar=f" {toolbar}")
    except (KeyboardInterrupt, EOFError):
        return None


# --------------------------------------------------------------------------- formulaire

class Field:
    """
    Champ de formulaire. `key` est un chemin dans le dictionnaire édité, ex. "hardware.ngl".
    kind : text | int | float | bool | choice | path | multiline.
    enabled(data) / note(data) : champ actif et remarque, selon les autres valeurs.
    visible(data) : champ affiché ou non (ex. réglage propre au mode GPU).
    """
    def __init__(self, key: str, label: str, kind: str = "text", section: str = "",
                 choices: Sequence[Any] = (), help: str = "", minimum=None, maximum=None,
                 enabled: Callable[[dict], bool] = None, note: Callable[[dict], str] = None,
                 required: bool = False, path_ext: str = ".gguf", path_dirs: Sequence[str] = (),
                 visible: Callable[[dict], bool] = None):
        self.key, self.label, self.kind, self.section = key, label, kind, section
        self.choices, self.help = list(choices), help
        self.minimum, self.maximum = minimum, maximum
        self.enabled = enabled or (lambda d: True)
        self.note = note or (lambda d: "")
        self.visible = visible or (lambda d: True)
        self.required = required
        self.path_ext, self.path_dirs = path_ext, list(path_dirs)


def get_path(data: dict, key: str, default=None):
    cur = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(data: dict, key: str, value):
    parts = key.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _fmt(field: Field, value) -> str:
    if field.kind == "bool":
        return "oui" if value else "non"
    if field.kind == "path":
        return os.path.basename(str(value)) if value else "(aucun)"
    if value in (None, ""):
        return "(vide)"
    return str(value)


def find_files(dirs: Sequence[str], ext: str, limit: int = 40) -> List[str]:
    """Fichiers d'extension `ext` dans les dossiers donnés (non récursif, puis un niveau)."""
    found, seen = [], set()
    for d in dirs:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        try:
            for root, subdirs, files in os.walk(d):
                depth = root[len(d):].count(os.sep)
                if depth >= 1:
                    subdirs[:] = []
                for fn in sorted(files):
                    if fn.lower().endswith(ext):
                        p = os.path.join(root, fn)
                        real = os.path.realpath(p)
                        if real not in seen:
                            seen.add(real)
                            found.append(p)
                if len(found) >= limit:
                    return found
        except OSError:
            continue
    return found


def _edit_value(field: Field, value):
    """Modifie un champ (sous-menu ou saisie). Retourne la nouvelle valeur, ou None si annulé."""
    if field.kind == "choice":
        items = [(c, str(c), "") for c in field.choices]
        return select(field.label, items, current=value, subtitle=field.help)

    if field.kind == "path":
        files = find_files(field.path_dirs, field.path_ext)
        items = []
        if not field.required:
            items.append(("", "(aucun)", ""))
        for p in files:
            size = ""
            try:
                size = f"{os.path.getsize(p) / 1e9:.1f} Go"
            except OSError:
                pass
            items.append((p, os.path.basename(p), f"{size} · {os.path.dirname(p)}"))
        items.append(("__autre__", "✎ Saisir un autre chemin…", ""))
        choice = select(field.label, items, current=value,
                        subtitle=field.help or "Fichiers trouvés dans les dossiers de modèles")
        if choice != "__autre__":
            return choice
        text = ask_text(field.label, value or "")
        return os.path.expanduser(text.strip()) if text is not None else None

    multiline = field.kind == "multiline"
    while True:
        text = ask_text(field.label, "" if value is None else value, multiline=multiline,
                        hint=field.help or None)
        if text is None:
            return None
        if field.kind in ("text", "multiline"):
            if field.required and not text.strip():
                continue
            return text.strip() if field.kind == "text" else text
        try:
            num = int(text) if field.kind == "int" else float(text.replace(",", "."))
        except ValueError:
            continue
        if field.minimum is not None:
            num = max(field.minimum, num)
        if field.maximum is not None:
            num = min(field.maximum, num)
        return num


def edit_form(title: str, data: dict, fields: List[Field], save_label: str = "✓ Enregistrer",
              extra_actions: Sequence[Tuple[str, str]] = (),
              validate: Callable[[dict], str] = None,
              on_change: Callable[[dict, str, Any], None] = None) -> Tuple[Optional[str], dict]:
    """
    Formulaire au clavier. Retourne (action, données) :
      action = "save" (Enregistrer), une clé de `extra_actions`, ou None (annulé).
    validate(data) -> message d'erreur ("" si tout va bien), vérifié avant d'enregistrer.
    on_change(data, clé, ancienne valeur) : appelé après chaque modification (valeurs liées).
    """
    original = copy.deepcopy(data)
    data = copy.deepcopy(data)
    actions = [("save", save_label)] + list(extra_actions) + [("cancel", "✗ Annuler")]
    state = {"i": 0, "error": ""}

    def rows():
        """Lignes du formulaire : ("section", titre) | ("field", Field) | ("action", clé, libellé)."""
        out, last = [], None
        for f in fields:
            if not f.visible(data):
                continue
            if f.section and f.section != last:
                out.append(("section", f.section))
                last = f.section
            out.append(("field", f))
        out.append(("section", ""))
        for key, label in actions:
            out.append(("action", key, label))
        return out

    def selectable(rs):
        return [k for k, r in enumerate(rs) if r[0] != "section"]

    def render():
        rs = rows()
        sel = selectable(rs)
        cur = sel[min(state["i"], len(sel) - 1)]
        cols = _cols()
        label_w = min(22, max(len(f.label) for f in fields) + 1)
        value_w = max(8, cols - label_w - 8)
        out = _header(title)
        visible = max(6, _rows() - 7)
        first = min(max(0, cur - visible // 2), max(0, len(rs) - visible))
        if first > 0:
            out.append((GREY, "    ↑ …\n"))
        for k in range(first, min(len(rs), first + visible)):
            r = rs[k]
            if r[0] == "section":
                out.append((f"bold {YELLOW}" if r[1] else "", f"  {r[1]}\n" if r[1] else "\n"))
                continue
            here = k == cur
            pointer = (f"bold {GREEN}", "  ❯ ") if here else ("", "    ")
            if r[0] == "action":
                out += [pointer, (f"bold {GREEN}" if here else "", r[2] + "\n")]
                continue
            f = r[1]
            on = f.enabled(data)
            value = get_path(data, f.key)
            changed = value != get_path(original, f.key)
            style = GREY if not on else (f"bold {GREEN}" if here else "")
            vstyle = GREY if not on else (YELLOW if changed else ("" if here else CYAN))
            if f.kind == "path" and value and not os.path.exists(str(value)):
                vstyle = RED
            out += [pointer, (style, f.label.ljust(label_w)),
                    (vstyle, _clip(_fmt(f, value) if on else "—", value_w))]
            note = f.note(data)
            if here and note:
                out.append((GREY, "\n      " + _clip(note, cols - 6)))
            out.append(("", "\n"))
        if first + visible < len(rs):
            out.append((GREY, "    ↓ …\n"))
        if state["error"]:
            out.append((f"bold {RED}", f"\n  {state['error']}\n"))
        out.append((DIM, "\n  ↑/↓ · Entrée modifier · ←/→ choix · Échap quitter"))
        return out

    def current_row():
        rs = rows()
        sel = selectable(rs)
        state["i"] = max(0, min(state["i"], len(sel) - 1))
        return rs[sel[state["i"]]]

    while True:
        kb = KeyBindings()
        _bind_cancel(kb)

        @kb.add("up")
        @kb.add("k")
        def _(event):
            state["i"] = max(0, state["i"] - 1)
            state["error"] = ""

        @kb.add("down")
        @kb.add("j")
        def _(event):
            state["i"] = min(len(selectable(rows())) - 1, state["i"] + 1)
            state["error"] = ""

        def cycle(step):
            r = current_row()
            if r[0] != "field" or not r[1].enabled(data):
                return
            f = r[1]
            v = get_path(data, f.key)
            if f.kind == "bool":
                set_path(data, f.key, not v)
            elif f.kind == "choice" and f.choices:
                k = f.choices.index(v) if v in f.choices else -step
                set_path(data, f.key, f.choices[(k + step) % len(f.choices)])
            else:
                return
            if on_change:
                on_change(data, f.key, v)

        @kb.add("left")
        @kb.add("h")
        def _(event):
            cycle(-1)

        @kb.add("right")
        @kb.add("l")
        @kb.add(" ")
        def _(event):
            cycle(1)

        @kb.add("enter")
        def _(event):
            r = current_row()
            if r[0] == "action":
                event.app.exit(result=("action", r[1]))
            elif r[1].enabled(data):
                if r[1].kind == "bool":
                    cycle(1)
                else:
                    event.app.exit(result=("edit", r[1]))

        res = _run(render, kb)
        if res is None:
            if data != original and not confirm("Quitter sans enregistrer les modifications ?", default=False):
                continue
            return None, original
        kind, target = res
        if kind == "edit":
            old = get_path(data, target.key)
            new = _edit_value(target, old)
            if new is not None:
                set_path(data, target.key, new)
                if on_change and new != old:
                    on_change(data, target.key, old)
            continue
        if target == "cancel":
            if data != original and not confirm("Abandonner les modifications ?", default=True):
                continue
            return None, original
        if target == "save" and validate:
            err = validate(data)
            if err:
                state["error"] = err
                continue
        return target, data
