"""
IADroid - Mode agent

Outils que le modèle peut appeler (API OpenAI « tools » de llama-server) pour agir dans
son environnement Termux : lire / écrire / modifier des fichiers, lancer des commandes,
consulter le web et garder des notes sur l'utilisateur.

Permissions :
  - le dossier de travail (~/.iadroid/workspace) est toujours autorisé ;
  - d'autres dossiers s'ajoutent avec /allow (ou /allow all : Termux + /storage/emulated/0) ;
  - un accès hors des dossiers autorisés demande toujours une confirmation ;
  - mode « ask » : écriture, modification et commandes demandent une confirmation ;
    mode « auto » : tout est exécuté sans confirmation dans les dossiers autorisés.

Affichage (style Claude Code) :
  ● Write(~/projets/bonjour.py) · 47 tokens
    ⎿  Fichier créé · 1 ligne
          1 + print("Bonjour")
"""

import os
import re
import json
import time
import codecs
import threading
import base64
import socket
import shutil
import signal
import difflib
import fnmatch
import subprocess
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console
from rich.text import Text

from .config import WORKSPACE_DIR, AGENT_SETTINGS_FILE
from . import memory

# Limites : chaque caractère renvoyé au modèle passe par le traitement du prompt (~30 t/s sur mobile)
MAX_READ_LINES = 200
MAX_TOOL_CHARS = 6000
MAX_LIST_ENTRIES = 150
MAX_SEARCH_RESULTS = 60
DISPLAY_LINES = 6          # lignes de résultat affichées sous un appel d'outil
DIFF_DISPLAY_LINES = 16    # lignes de diff affichées
LIVE_LINES = 5             # lignes de sortie visibles en direct pendant une commande

SHARED_STORAGE = "/storage/emulated/0"
USER_AGENT = "Mozilla/5.0 (Linux; Android 14) IADroid"
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

TOOL_LABELS = {
    "list_dir": "List", "read_file": "Read", "search_files": "Search",
    "write_file": "Write", "edit_file": "Update", "run_command": "Bash", "command_status": "BashOutput",
    "web_search": "WebSearch", "web_fetch": "Fetch",
    "memory_save": "Memory", "memory_delete": "Forget",
}

DEFAULT_SETTINGS = {
    "enabled": False,
    "mode": "ask",          # ask | auto
    "allowed_paths": [],    # dossiers autorisés en plus du dossier de travail
    "web": True,
    "max_steps": 20,
    "cmd_timeout": 120,
}


# --------------------------------------------------------------------------- réglages

class AgentSettings:
    def __init__(self):
        self.data = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        try:
            self.data.update(json.loads(AGENT_SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    def save(self):
        try:
            AGENT_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            AGENT_SETTINGS_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()


# --------------------------------------------------------------------------- chemins

def real(path: str) -> str:
    """Chemin absolu réel (~ développé, relatif au dossier de travail, liens résolus)."""
    p = os.path.expanduser(str(path).strip())
    if not os.path.isabs(p):
        p = os.path.join(str(WORKSPACE_DIR), p)
    return os.path.realpath(p)


def short(path: str) -> str:
    """Affichage compact : le dossier personnel devient ~."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _under(path: str, root: str) -> bool:
    root = root.rstrip("/\\")
    return path == root or path.startswith(root + os.sep) or path.startswith(root + "/")


def _clip(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-limit // 3:]
    return f"{head}\n… [{len(text) - limit} caractères coupés] …\n{tail}"


def _line(text: str, width: int = 160) -> str:
    text = text.replace("\t", "    ").rstrip("\r")
    return text if len(text) <= width else text[: width - 1] + "…"


# --------------------------------------------------------------------------- définitions des outils

def _fn(name, description, props, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": required}}}


def tool_schemas(web: bool = True) -> List[dict]:
    """Descriptions courtes : elles sont envoyées à chaque requête."""
    S = {"type": "string"}
    I = {"type": "integer"}
    tools = [
        _fn("list_dir", "Liste le contenu d'un dossier.", {"path": S}, ["path"]),
        _fn("read_file", "Lit un fichier texte (lignes numérotées, 200 max par appel).",
            {"path": S, "offset": {**I, "description": "première ligne (1 par défaut)"}, "limit": I}, ["path"]),
        _fn("search_files", "Cherche des fichiers par nom (glob) et/ou par contenu (regex) dans un dossier.",
            {"path": S, "name": {**S, "description": "ex: *.py"}, "text": {**S, "description": "regex"}}, ["path"]),
        _fn("write_file", "Crée ou remplace entièrement un fichier.", {"path": S, "content": S}, ["path", "content"]),
        _fn("edit_file", "Remplace un passage exact (unique) d'un fichier existant.",
            {"path": S, "old_text": S, "new_text": S}, ["path", "old_text", "new_text"]),
        _fn("run_command", "Exécute une commande bash dans Termux (pkg, python, git, termux-*...). Non interactive.",
            {"command": S, "timeout": {**I, "description": "secondes (120 par défaut), puis elle continue en arrière-plan"},
             "background": {"type": "boolean", "description": "true : rend la main tout de suite (commande longue)"}},
            ["command"]),
        _fn("command_status", "Sortie nouvelle et état d'une commande d'arrière-plan.",
            {"id": S, "wait": {**I, "description": "secondes à attendre sa fin"},
             "kill": {"type": "boolean", "description": "true : l'arrêter"}}, ["id"]),
        _fn("memory_save", "Mémorise une information durable sur l'utilisateur (prénom, préférences…), "
            "conservée entre les conversations. Remplace la note du même nom.",
            {"name": {**S, "description": "nom court, ex: prenom"}, "content": S}, ["name", "content"]),
        _fn("memory_delete", "Supprime une note mémorisée.", {"name": S}, ["name"]),
    ]
    if web:
        tools += [
            _fn("web_search", "Recherche sur le web (DuckDuckGo).", {"query": S}, ["query"]),
            _fn("web_fetch", "Télécharge une page web et renvoie son texte.", {"url": S}, ["url"]),
        ]
    return tools


# --------------------------------------------------------------------------- web

class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "form", "head", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
             "pre", "blockquote", "table", "ul", "ol", "dd", "dt", "main"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skip:
            self.parts.append("## ")
        if tag == "li" and not self.skip:
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(l.split()) for l in raw.split("\n")]
        out, blank = [], 0
        for l in lines:
            # Restes de menus (puces vides, séparateurs) : du bruit qui coûte des tokens
            if not l or re.fullmatch(r"[-|»«·•¶>/\s]*", l) or (len(l) <= 2 and not l.isalnum()):
                l = ""
            if not l:
                blank += 1
                if blank == 1 and out:
                    out.append("")
                continue
            blank = 0
            out.append(l)
        return "\n".join(out).strip()


def _http_get(url: str, data: Optional[bytes] = None, timeout: int = 20,
              user_agent: str = USER_AGENT) -> Tuple[str, str]:
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": user_agent, "Accept-Language": "fr,en;q=0.8", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        raw = resp.read(3 * 1024 * 1024)
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace"), ctype


def _strip_tags(s: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", s)).strip()


SEARCH_TIMEOUT = 10  # par tentative : un handshake TLS bloqué ne doit pas figer l'agent 20 s


def web_search(query: str, max_results: int = 6) -> List[Tuple[str, str, str]]:
    """
    Retourne une liste (titre, url, extrait).
    DuckDuckGo bride par moments les requêtes automatiques (handshake TLS qui n'aboutit pas,
    page « anomaly ») : deux tentatives, puis repli sur Bing.
    """
    errors = []
    for attempt in range(2):
        try:
            res = _search_ddg(query, max_results)
            if res is not None:
                return res
            errors.append("DuckDuckGo : page anti-robot")
            break  # page anti-robot : réessayer tout de suite ne sert à rien
        except Exception as e:
            errors.append(f"DuckDuckGo : {_net_error(e)}")
            if attempt == 0:
                time.sleep(1.5)
    try:
        return _search_bing(query, max_results)
    except Exception as e:
        errors.append(f"Bing : {_net_error(e)}")
    raise RuntimeError("recherche impossible (" + " ; ".join(errors) + ")")


def _net_error(e: Exception) -> str:
    """Message d'erreur réseau court et lisible."""
    reason = getattr(e, "reason", None) or e
    text = str(reason)
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
        return "délai dépassé"
    return text[:120]


def _search_ddg(query: str, max_results: int) -> Optional[List[Tuple[str, str, str]]]:
    """Version HTML de DuckDuckGo. None si DuckDuckGo renvoie sa page anti-robot."""
    html, _ = _http_get("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode(),
                        timeout=SEARCH_TIMEOUT)
    results = []
    blocks = re.split(r'<div[^>]+class="[^"]*\bresult\b', html)[1:]
    for b in blocks:
        m = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        href, title = unescape(m.group(1)), _strip_tags(m.group(2))
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/y.js" in href:  # publicité
            continue
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S)
        results.append((title, href, _strip_tags(sm.group(1)) if sm else ""))
        if len(results) >= max_results:
            break
    if not results and re.search(r"anomaly|challenge", html):
        return None
    return results


def _search_bing(query: str, max_results: int) -> List[Tuple[str, str, str]]:
    """Repli : page de résultats Bing (navigateur de bureau, sinon Bing sert une autre mise en page)."""
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "setlang": "fr", "cc": "FR"})
    html, _ = _http_get(url, timeout=SEARCH_TIMEOUT, user_agent=BROWSER_USER_AGENT)
    results = []
    for b in re.split(r'<li class="b_algo"', html)[1:]:
        m =(re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
             or re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*<h2[^>]*>(.*?)</h2>', b, re.S))
        if not m:
            continue
        href, title = _bing_url(unescape(m.group(1))), _strip_tags(m.group(2))
        sm = re.search(r'<p[^>]*>(.*?)</p>', b, re.S)
        results.append((title, href, _strip_tags(sm.group(1)) if sm else ""))
        if len(results) >= max_results:
            break
    return results


def _bing_url(href: str) -> str:
    """Les liens Bing passent parfois par bing.com/ck/a?…&u=a1<url en base64> : retrouver l'URL réelle."""
    if "bing.com/ck/a" not in href:
        return href
    u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        try:
            b64 = u[2:] + "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(b64).decode("utf-8", errors="replace")
        except Exception:
            pass
    return href


def web_fetch(url: str) -> Tuple[str, str]:
    """Retourne (titre, texte) d'une page web."""
    content, ctype = _http_get(url)
    if "html" in ctype or content.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        p = _HTMLText()
        p.feed(content)
        return " ".join(p.title.split()), p.text()
    return "", content


# --------------------------------------------------------------------------- diff

def render_diff(before: str, after: str) -> Tuple[int, int, List[Text]]:
    """Lignes ajoutées / retirées et rendu numéroté (style Claude Code)."""
    a, b = before.splitlines(), after.splitlines()
    out, added, removed = [], 0, 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for k in range(i1, i2):
            out.append(Text(f"{k + 1:>5} - {_line(a[k], 150)}", style="#FFB3B3 on #4A1E1E"))
            removed += 1
        for k in range(j1, j2):
            out.append(Text(f"{k + 1:>5} + {_line(b[k], 150)}", style="#B3FFB3 on #1E401E"))
            added += 1
    if len(out) > DIFF_DISPLAY_LINES:
        rest = len(out) - DIFF_DISPLAY_LINES
        out = out[:DIFF_DISPLAY_LINES] + [Text(f"      … +{rest} lignes", style="dim")]
    return added, removed, out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


# --------------------------------------------------------------------------- exécution

class Prepared:
    """Appel d'outil analysé, prêt à être confirmé puis exécuté."""
    def __init__(self, title: str, kind: str = "read", paths: List[str] = None,
                 preview: List[Text] = None, question: str = "", action: Callable = None, error: str = ""):
        self.title = title          # ex: Write(~/projets/a.py)
        self.kind = kind            # read | write | exec | web | memory
        self.paths = paths or []    # chemins réels concernés (contrôle des dossiers autorisés)
        self.preview = preview or []
        self.question = question
        self.action = action        # () -> (texte_pour_le_modèle, lignes_affichées)
        self.error = error


class Agent:
    def __init__(self, console: Console):
        self.console = console
        self.settings = AgentSettings()
        self.session_paths: List[str] = []   # autorisés pour la session (réponse « toujours »)
        self.session_always: set = set()    # types d'action confirmés « toujours » pour la session
        # Compteur de tokens (branché par le CLI sur LlamaServer.count_tokens) et
        # nombre de tokens du dernier résultat renvoyé au modèle
        self.count_tokens: Callable[[str], int] = lambda s: max(1, len(s) // 3) if s else 0
        self.last_result_tokens = 0
        self.jobs: Dict[str, "ShellJob"] = {}  # commandes d'arrière-plan (id → ShellJob)

    # ---- état

    @property
    def enabled(self) -> bool:
        return bool(self.settings["enabled"])

    def allowed_roots(self) -> List[str]:
        roots = [os.path.realpath(str(WORKSPACE_DIR))]
        for p in list(self.settings["allowed_paths"]) + self.session_paths:
            roots.append(os.path.realpath(os.path.expanduser(p)))
        return roots

    def in_zone(self, path: str) -> bool:
        return any(_under(path, r) for r in self.allowed_roots())

    def tools(self) -> List[dict]:
        return tool_schemas(web=bool(self.settings["web"]))

    def system_block(self) -> str:
        """Consignes ajoutées au prompt système quand le mode agent est actif."""
        ws = short(os.path.realpath(str(WORKSPACE_DIR)))
        extra = [short(p) for p in self.allowed_roots()[1:]]
        lines = [
            "Mode agent actif : tu agis sur le téléphone Android de l'utilisateur (Termux) grâce aux outils.",
            f"- Dossier de travail : {ws} (les chemins relatifs partent de là).",
        ]
        if extra:
            lines.append(f"- Autres dossiers autorisés : {', '.join(extra)}.")
        lines += [
            "- run_command lance bash dans Termux : pkg install -y, python, git, termux-*… Jamais de commande interactive. "
            "Pour une commande longue (pkg upgrade, compilation, téléchargement), background=true puis continue ; "
            "command_status donne sa sortie.",
            "- Lis un fichier avant de le modifier ; pour un changement ciblé, utilise edit_file.",
            "- Mémoire : les notes déjà mémorisées sont données plus haut (elles ne sont dans aucun fichier). "
            "Quand l'utilisateur donne une NOUVELLE information durable sur lui (prénom, langage, goûts, projets, matériel), "
            "appelle memory_save avant le reste de la tâche : une note par sujet, en phrase complète "
            "(ex : name=langage, content=« Lucas développe surtout en Python »).",
            "- Quand la tâche est terminée, réponds en français par un court résumé.",
        ]
        return "\n".join(lines)

    # ---- point d'entrée

    def execute(self, name: str, raw_args: str, tokens: int, confirm: Callable) -> str:
        """
        Exécute un appel d'outil avec affichage et confirmation.
        confirm(question) -> ("yes" | "always" | "no", raison) ; peut lever KeyboardInterrupt.
        Retourne le texte renvoyé au modèle.
        """
        label = TOOL_LABELS.get(name, name)
        self.last_result_tokens = 0
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
            if not isinstance(args, dict):
                raise ValueError("arguments non JSON objet")
        except Exception as e:
            self._header(f"{label}(?)", tokens)
            self._result([Text(f"Arguments invalides : {e}", style="red")])
            return f"Erreur : arguments JSON invalides ({e}). Renvoie un objet JSON valide."

        try:
            prep = self._prepare(name, args)
        except Exception as e:
            target = next((str(args[k]) for k in ("path", "command", "url", "query", "name", "id") if args.get(k)), "")
            if args.get("path"):
                target = short(real(target))
            prep = Prepared(f"{label}({target[:80]})", error=str(e))

        self._header(prep.title, tokens)
        if prep.error:
            self._result([Text(prep.error, style="red")])
            return f"Erreur : {prep.error}"

        outside = [p for p in prep.paths if not self.in_zone(p)]
        need = bool(outside) or (
            prep.kind in ("write", "exec") and self.settings["mode"] != "auto" and prep.kind not in self.session_always)

        if need:
            for line in prep.preview:
                self.console.print(Text("    ") + line)
            question = prep.question or f"Autoriser {prep.title} ?"
            if outside:
                question += f"  (hors des dossiers autorisés : {', '.join(short(p) for p in outside)})"
            choice, reason = confirm(question)
            if choice == "no":
                self._result([Text("Refusé par l'utilisateur", style="yellow")])
                msg = "L'utilisateur a refusé cette action."
                return msg + (f" Raison : {reason}" if reason else " Demande-lui comment continuer.")
            if choice == "always":
                if outside:
                    for p in outside:
                        d = p if os.path.isdir(p) else os.path.dirname(p)
                        if d not in self.session_paths:
                            self.session_paths.append(d)
                else:
                    self.session_always.add(prep.kind)

        try:
            model_text, lines = prep.action()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self._result([Text(f"Erreur : {e}", style="red")])
            return f"Erreur : {e}"

        # En mode auto (pas de confirmation), l'aperçu est affiché avec le résultat :
        # le diff après le résumé ; la commande complète avant sa sortie, si le titre l'a tronquée
        if not need and prep.preview:
            if prep.kind == "exec":
                if prep.title.endswith("…)") or len(prep.preview) > 1:
                    for line in prep.preview:
                        self.console.print(Text("    ") + line)
            else:
                lines = lines + prep.preview

        # Tokens que le modèle va lire (contenu de fichier, page web, sortie de commande…) :
        # affichés sur la première ligne du résultat, comme les tokens écrits sur l'en-tête
        try:
            self.last_result_tokens = int(self.count_tokens(model_text))
        except Exception:
            self.last_result_tokens = max(1, len(model_text) // 3)
        if prep.kind in ("read", "web", "exec") and self.last_result_tokens:
            lines = list(lines) or [Text("(terminé)", style="dim")]
            lines[0] = lines[0] + Text(f" · {self.last_result_tokens} tokens lus", style="dim")
        self._result(lines)
        return model_text

    # ---- affichage

    def _header(self, title: str, tokens: int):
        t = Text("● ", style="bold #3DDC84")
        t.append(title, style="bold")
        if tokens:
            t.append(f" · {tokens} tokens", style="dim")
        self.console.print(t)

    def _result(self, lines: List[Text]):
        for i, line in enumerate(lines or [Text("(terminé)", style="dim")]):
            prefix = Text("  ⎿  " if i == 0 else "     ", style="dim")
            self.console.print(prefix + line)

    # ---- analyse des appels

    def _prepare(self, name: str, a: Dict[str, Any]) -> Prepared:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return Prepared(f"{name}()", error=f"outil inconnu : {name}")
        return handler(a)

    def _need(self, a: dict, key: str) -> str:
        v = a.get(key)
        if v is None or (isinstance(v, str) and not v.strip() and key != "new_text"):
            raise ValueError(f"paramètre « {key} » manquant")
        return str(v)

    # list_dir
    def _t_list_dir(self, a):
        path = real(a.get("path") or ".")
        title = f"List({short(path)})"

        def run():
            if not os.path.isdir(path):
                raise ValueError(f"dossier introuvable : {short(path)}")
            entries = []
            for e in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
                try:
                    if e.is_dir():
                        entries.append(e.name + "/")
                    else:
                        entries.append(f"{e.name}  ({_size(e.stat().st_size)})")
                except OSError:
                    entries.append(e.name)
            total = len(entries)
            shown = entries[:MAX_LIST_ENTRIES]
            text = "\n".join(shown) + (f"\n… {total - len(shown)} autres" if total > len(shown) else "")
            return (text or "(dossier vide)"), [Text(_plural(total, "élément"), style="dim")]
        return Prepared(title, "read", [path], action=run)

    # read_file
    def _t_read_file(self, a):
        path = real(self._need(a, "path"))
        offset = max(1, int(a.get("offset") or 1))
        limit = max(1, min(MAX_READ_LINES, int(a.get("limit") or MAX_READ_LINES)))
        title = f"Read({short(path)})"

        def run():
            if not os.path.isfile(path):
                raise ValueError(f"fichier introuvable : {short(path)}")
            with open(path, "rb") as f:
                if b"\x00" in f.read(2048):
                    raise ValueError("fichier binaire, lecture impossible")
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            chunk = lines[offset - 1: offset - 1 + limit]
            body = "\n".join(f"{offset + i:>5}│{l}" for i, l in enumerate(chunk))
            more = len(lines) - (offset - 1 + len(chunk))
            if more > 0:
                body += f"\n… {more} lignes restantes (utilise offset={offset + len(chunk)})"
            info = f"{_plural(len(chunk), 'ligne')} lue{'s' if len(chunk) > 1 else ''}"
            if len(lines) != len(chunk):
                info += f" ({offset}-{offset + len(chunk) - 1} / {len(lines)})"
            return _clip(body or "(fichier vide)"), [Text(info, style="dim")]
        return Prepared(title, "read", [path], action=run)

    # search_files
    def _t_search_files(self, a):
        root = real(a.get("path") or ".")
        name_pat = (a.get("name") or "").strip()
        text_pat = (a.get("text") or "").strip()
        if not name_pat and not text_pat:
            raise ValueError("donne au moins « name » ou « text »")
        rx = re.compile(text_pat, re.I) if text_pat else None
        title = f"Search({text_pat or name_pat} dans {short(root)})"

        def run():
            if not os.path.isdir(root):
                raise ValueError(f"dossier introuvable : {short(root)}")
            hits, scanned = [], 0
            for d, dirs, files in os.walk(root):
                dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("node_modules", "__pycache__", "build")]
                for fn in files:
                    if name_pat and not fnmatch.fnmatch(fn.lower(), name_pat.lower()):
                        continue
                    fp = os.path.join(d, fn)
                    if not rx:
                        hits.append(short(fp))
                    else:
                        scanned += 1
                        if scanned > 3000:
                            break
                        try:
                            if os.path.getsize(fp) > 2_000_000:
                                continue
                            with open(fp, encoding="utf-8", errors="ignore") as f:
                                for no, line in enumerate(f, 1):
                                    if rx.search(line):
                                        hits.append(f"{short(fp)}:{no}: {_line(line.strip(), 200)}")
                                        if len(hits) >= MAX_SEARCH_RESULTS:
                                            break
                        except OSError:
                            continue
                    if len(hits) >= MAX_SEARCH_RESULTS:
                        break
                if len(hits) >= MAX_SEARCH_RESULTS:
                    break
            text = "\n".join(hits) if hits else "Aucun résultat."
            return _clip(text), [Text(f"{_plural(len(hits), 'résultat')}", style="dim")]
        return Prepared(title, "read", [root], action=run)

    # write_file
    def _t_write_file(self, a):
        path = real(self._need(a, "path"))
        content = str(a.get("content", ""))
        exists = os.path.isfile(path)
        before = ""
        if exists:
            with open(path, encoding="utf-8", errors="replace") as f:
                before = f.read()
        if exists:
            added, removed, preview = render_diff(before, content)
        else:
            new_lines = content.splitlines()
            preview = [Text(f"{i + 1:>5} + {_line(l, 150)}", style="#B3FFB3 on #1E401E")
                       for i, l in enumerate(new_lines[:DIFF_DISPLAY_LINES])]
            if len(new_lines) > DIFF_DISPLAY_LINES:
                preview.append(Text(f"      … +{len(new_lines) - DIFF_DISPLAY_LINES} lignes", style="dim"))
            added, removed = len(new_lines), 0

        def run():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            if exists:
                info = f"Fichier remplacé · +{added} -{removed} lignes"
            else:
                info = f"Fichier créé · {_plural(added, 'ligne')}"
            return f"OK : {short(path)} écrit ({_plural(len(content.splitlines()), 'ligne')}).", [Text(info)]
        verb = "Remplacer" if exists else "Créer"
        return Prepared(f"Write({short(path)})", "write", [path], preview,
                        f"{verb} le fichier {short(path)} ?", run)

    # edit_file
    def _t_edit_file(self, a):
        path = real(self._need(a, "path"))
        old = self._need(a, "old_text")
        new = str(a.get("new_text", ""))
        if not os.path.isfile(path):
            raise ValueError(f"fichier introuvable : {short(path)}")
        with open(path, encoding="utf-8", errors="replace") as f:
            before = f.read()
        count = before.count(old)
        if count == 0:
            raise ValueError("old_text introuvable dans le fichier (relis-le avec read_file, le texte doit être exact)")
        if count > 1:
            raise ValueError(f"old_text apparaît {count} fois : ajoute du contexte pour qu'il soit unique")
        after = before.replace(old, new, 1)
        added, removed, preview = render_diff(before, after)

        def run():
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(after)
            return f"OK : {short(path)} modifié.", [Text(f"+{added} -{removed} lignes")]
        return Prepared(f"Update({short(path)})", "write", [path], preview,
                        f"Modifier le fichier {short(path)} ?", run)

    # run_command
    def _t_run_command(self, a):
        cmd = self._need(a, "command")
        timeout = max(5, min(600, int(a.get("timeout") or self.settings["cmd_timeout"])))
        if re.search(r"\b(pkill|killall|kill)\b", cmd) and "llama" in cmd:
            raise ValueError("commande refusée : elle arrêterait le serveur du modèle (llama-server) lui-même")
        first = cmd.strip().splitlines()[0] if cmd.strip() else cmd
        shown = first if len(first) <= 80 and first == cmd.strip() else first[:77] + "…"
        preview = [Text("$ ", style="dim") + Text(l, style="bold") for l in cmd.splitlines()[:DIFF_DISPLAY_LINES]]
        background = bool(a.get("background"))

        def run():
            job = ShellJob(cmd)
            if background:
                self.jobs[job.id] = job
                return (f"Commande lancée en arrière-plan (id {job.id}). Continue la tâche ; "
                        f"command_status(id={job.id}) donne sa sortie (wait=N pour attendre la fin).",
                        [Text(f"◐ lancée en arrière-plan · id {job.id}", style="#00D8D6")])
            self.follow(job, timeout)
            out = job.take_new().strip()
            if not job.done:
                # Délai dépassé : la commande continue en arrière-plan au lieu d'être tuée
                self.jobs[job.id] = job
                status = (f"toujours en cours après {timeout}s : passée en arrière-plan, id {job.id} "
                          f"(command_status pour la suivre ou l'arrêter)")
                disp = _tail_lines(out) + [Text(f"◐ {timeout}s écoulées : continue en arrière-plan · id {job.id}",
                                                style="#00D8D6")]
                return _clip(f"[{status}]\n{out}"), disp
            status = f"code de sortie {job.code}"
            disp = _tail_lines(out)
            if job.code != 0:
                disp.append(Text(status, style="red"))
            if not disp:
                disp = [Text("(aucune sortie)", style="dim")]
            return _clip(f"[{status}]\n{out}".rstrip()), disp
        return Prepared(f"Bash({shown})", "exec", [], preview, "Exécuter cette commande ?", run)

    # command_status
    def _t_command_status(self, a):
        jid = self._need(a, "id").strip()
        job = self.jobs.get(jid)
        if job is None:
            known = ", ".join(self.jobs) or "aucune"
            raise ValueError(f"commande d'arrière-plan inconnue : {jid} (connues : {known})")
        wait = max(0, min(300, int(a.get("wait") or 0)))
        kill = bool(a.get("kill"))

        def run():
            if kill and not job.done:
                job.kill()
            elif wait and not job.done:
                self.follow(job, wait)
            out = job.take_new().strip()
            job.notified = job.notified or job.done
            disp = _tail_lines(out)
            style = "#00D8D6" if not job.done else ("red" if job.code not in (0, None) and not job.killed else "dim")
            disp.append(Text(("◐ " if not job.done else "") + job.status(), style=style))
            return _clip(f"[{job.status()}]\n{out or '(pas de nouvelle sortie)'}"), disp
        return Prepared(f"BashOutput({jid}{', kill' if kill else ''})", "read", action=run)

    # ---- commandes en cours

    def follow(self, job: "ShellJob", timeout: float):
        """
        Affiche la sortie de la commande en direct (dernières lignes, durée, tokens) jusqu'à
        sa fin ou `timeout` secondes. Ctrl+C arrête la commande.
        """
        from rich.live import Live
        from rich.spinner import Spinner
        from rich.console import Group

        spinner = Spinner("dots", style="#00D8D6")

        def render():
            out = job.output().rstrip()
            lines = out.splitlines()
            width = max(20, self.console.size.width - 8)
            tail = [Text("     " + _line(l, width), style="dim") for l in lines[-LIVE_LINES:]]
            info = f" en cours · {job.elapsed:.0f}s"
            if lines:
                info += f" · {_plural(len(lines), 'ligne')} · ~{max(1, len(out) // 3)} tokens"
            spinner.update(text=Text(info, style="dim"))
            return Group(spinner, *tail)

        class _View:
            def __rich__(self):
                return render()

        end = time.time() + timeout
        with Live(_View(), console=self.console, refresh_per_second=4, transient=True):
            try:
                while not job.done and time.time() < end:
                    time.sleep(0.2)
            except KeyboardInterrupt:
                job.kill()
                raise

    def jobs_counts(self) -> Tuple[int, int]:
        """(commandes en cours, commandes terminées dont la fin n'a pas encore été signalée au modèle)."""
        running = sum(1 for j in self.jobs.values() if not j.done)
        finished = sum(1 for j in self.jobs.values() if j.done and not j.notified)
        return running, finished

    def finished_notes(self) -> str:
        """Fins de commandes d'arrière-plan à signaler au modèle (une seule fois chacune)."""
        notes = []
        for j in self.jobs.values():
            if j.done and not j.notified:
                j.notified = True
                notes.append(f"La commande d'arrière-plan {j.id} (`{_line(j.cmd, 80)}`) est {j.status()} : "
                             f"command_status(id={j.id}) pour lire sa sortie.")
        return "\n".join(notes)

    def kill_jobs(self) -> int:
        n = 0
        for j in self.jobs.values():
            if not j.done:
                j.kill()
                n += 1
        return n

    # web_search
    def _t_web_search(self, a):
        query = self._need(a, "query")

        def run():
            res = web_search(query)
            if not res:
                return "Aucun résultat.", [Text("Aucun résultat", style="dim")]
            text = "\n\n".join(f"{i + 1}. {t}\n{u}\n{s}" for i, (t, u, s) in enumerate(res))
            disp = [Text(_line(f"{t} — {u}", 120)) for t, u, _ in res[:DISPLAY_LINES]]
            return _clip(text), disp
        return Prepared(f"WebSearch({query})", "web", action=run)

    # web_fetch
    def _t_web_fetch(self, a):
        url = self._need(a, "url").strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        def run():
            title, text = web_fetch(url)
            info = f"{title[:60]} · " if title else ""
            info += f"{len(text)} caractères"
            if len(text) > MAX_TOOL_CHARS:
                info += f" (tronqué à {MAX_TOOL_CHARS})"
            return _clip((f"# {title}\n\n" if title else "") + text), [Text(info, style="dim")]
        return Prepared(f"Fetch({url})", "web", action=run)

    # memory_save / memory_delete
    def _t_memory_save(self, a):
        name, content = self._need(a, "name"), self._need(a, "content")

        def run():
            slug = memory.save_note(name, content)
            return f"Note « {slug} » enregistrée.", [Text(_line(content, 120), style="italic")]
        return Prepared(f"Memory({name})", "memory", action=run)

    def _t_memory_delete(self, a):
        name = self._need(a, "name")

        def run():
            ok = memory.delete_note(name)
            return ("Note supprimée." if ok else "Note introuvable."), [Text("supprimée" if ok else "introuvable", style="dim")]
        return Prepared(f"Forget({name})", "memory", action=run)


# --------------------------------------------------------------------------- utilitaires

def _tail_lines(out: str) -> List[Text]:
    """Fin de la sortie d'une commande (les erreurs y sont), précédée du nombre de lignes masquées."""
    lines = out.rstrip().splitlines()
    disp = [Text(_line(l, 160)) for l in lines[-DISPLAY_LINES:]]
    if len(lines) > DISPLAY_LINES:
        disp.insert(0, Text(f"… {len(lines) - DISPLAY_LINES} lignes au-dessus", style="dim"))
    return disp


def _size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


_ANSI_RX = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
JOB_MAX_CHARS = 200_000   # sortie gardée en mémoire par commande (le début est oublié au-delà)


def clean_output(text: str) -> str:
    """Sortie brute → texte lisible : sans codes couleur, barres de progression (\r) réduites à leur dernier état."""
    text = _ANSI_RX.sub("", text).replace("\r\n", "\n")
    return "\n".join(l.rstrip("\r").rsplit("\r", 1)[-1] for l in text.split("\n"))



class ShellJob:
    """
    Commande bash non interactive lancée dans le dossier de travail. Un thread lit sa sortie
    au fil de l'eau : affichage en direct, puis passage en arrière-plan si elle dure.
    """
    _counter = 0

    def __init__(self, cmd: str):
        ShellJob._counter += 1
        self.id = f"b{ShellJob._counter}"
        self.cmd = cmd
        self.t0 = time.time()
        self.t_end: Optional[float] = None
        self.code: Optional[int] = None
        self.killed = False
        self.notified = False     # fin déjà signalée au modèle
        self._text = ""
        self._dropped = 0         # caractères oubliés en tête (JOB_MAX_CHARS)
        self._read_pos = 0        # position absolue déjà renvoyée au modèle (command_status)
        self._lock = threading.Lock()

        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.setdefault("DEBIAN_FRONTEND", "noninteractive")
        env["TERM"] = "dumb"
        env.setdefault("PYTHONUNBUFFERED", "1")  # sortie des scripts Python visible en direct
        bash = shutil.which("bash")
        argv = [bash, "-c", cmd] if bash else cmd
        kwargs = dict(cwd=str(WORKSPACE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      stdin=subprocess.DEVNULL, env=env, shell=not bash)
        if os.name == "posix":
            kwargs["start_new_session"] = True  # permet de tuer toute l'arborescence de processus
        self.proc = subprocess.Popen(argv, **kwargs)
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        stream = self.proc.stdout
        read = getattr(stream, "read1", stream.read)
        try:
            while True:
                data = read(4096)
                if not data:
                    break
                self._append(dec.decode(data))
        except (OSError, ValueError):
            pass
        self._append(dec.decode(b"", final=True))
        self.code = self.proc.wait()
        self.t_end = time.time()

    def _append(self, s: str):
        if not s:
            return
        with self._lock:
            self._text += s
            extra = len(self._text) - JOB_MAX_CHARS
            if extra > 0:
                self._text = self._text[extra:]
                self._dropped += extra

    @property
    def done(self) -> bool:
        return self.t_end is not None

    @property
    def elapsed(self) -> float:
        return (self.t_end or time.time()) - self.t0

    def output(self) -> str:
        with self._lock:
            return clean_output(self._text)

    def take_new(self) -> str:
        """Sortie produite depuis le dernier appel (ce que le modèle n'a pas encore lu)."""
        with self._lock:
            start = max(0, self._read_pos - self._dropped)
            new = self._text[start:]
            self._read_pos = self._dropped + len(self._text)
        return clean_output(new)

    def status(self) -> str:
        if not self.done:
            return f"en cours depuis {self.elapsed:.0f}s"
        if self.killed:
            return f"arrêtée après {self.elapsed:.0f}s"
        return f"terminée en {self.elapsed:.0f}s, code de sortie {self.code}"

    def kill(self):
        self.killed = True
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, signal.SIGKILL)
            else:
                self.proc.kill()
        except Exception:
            pass
        self._thread.join(timeout=3)
