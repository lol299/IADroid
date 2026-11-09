"""
IADroid - Interface en Ligne de Commande Interactive (REPL)
Style Claude Code & Antigravity CLI (agy) avec barre de commandes et autocomplétion
"""

import os
import json
import re
import sys
import time
import shutil
import threading
import subprocess
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.markup import escape
from rich.text import Text
from rich.live import Live
from rich.console import Group
from rich.spinner import Spinner

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

from .config import (
    IADROID_HOME,
    ANDROID_GREEN,
    LLAMA_CLI_PATH,
    ensure_directories,
    get_ram_info,
    get_gpu_type,
    MODELS_DIR,
    gguf_has_mtp,
    mtp_active,
    get_quantize_path
)
from .banner import display_welcome_banner, print_help_table
from .preset_manager import PresetManager, is_llama_running, stop_llama_processes
from .wizard import PresetWizard
from .builder import LlamaBuilder
from .server import LlamaServer
from .crash_handler import handle_crash
from .agent import Agent, ShellJob, TOOL_LABELS, SHARED_STORAGE, short as short_path
from . import memory

console = Console()

# ------------------------------------------------------------------ commandes « ! » de l'utilisateur

SHELL_CTX_CHARS = 3000      # sortie d'une commande « ! » gardée pour le modèle (début + fin)
SHELL_PENDING_MAX = 4       # commandes « ! » jointes au plus au prochain message
_ANSI_RX = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def _spawn_in_terminal(argv: list) -> tuple:
    """
    Lance argv dans un pseudo-terminal relié au vrai terminal : l'affichage est celui du
    script (couleurs, barres de progression), le clavier lui est transmis (saisies, Ctrl+C
    qui n'interrompt que le script). Retourne (sortie_brute: bytes, code_de_sortie).
    """
    import pty
    import tty
    import termios
    import fcntl
    import struct
    import select

    cols, rows = shutil.get_terminal_size()
    sys.stdout.flush()
    pid, master = pty.fork()
    if pid == 0:  # enfant : stdin/stdout/stderr = esclave du pty
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass
        try:
            os.execvp(argv[0], argv)
        finally:
            os._exit(127)

    stdin_fd, stdout_fd = sys.stdin.fileno(), sys.stdout.fileno()
    saved = None
    if os.isatty(stdin_fd):
        saved = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
    chunks = []
    try:
        fds = [master, stdin_fd]
        while True:
            ready, _, _ = select.select(fds, [], [])
            if master in ready:
                try:
                    data = os.read(master, 4096)
                except OSError:  # EIO : le script est terminé
                    data = b""
                if not data:
                    break
                chunks.append(data)
                os.write(stdout_fd, data)
            if stdin_fd in ready:
                data = os.read(stdin_fd, 1024)
                if data:
                    os.write(master, data)
                else:
                    fds.remove(stdin_fd)
    finally:
        if saved is not None:
            termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, saved)
        os.close(master)
    _, status = os.waitpid(pid, 0)
    return b"".join(chunks), os.waitstatus_to_exitcode(status)


def _spawn_piped(argv: list) -> tuple:
    """Repli hors POSIX (pas de pty) : sortie affichée ligne par ligne, sans saisie clavier."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    chunks = []
    try:
        for line in iter(proc.stdout.readline, b""):
            chunks.append(line)
            sys.stdout.buffer.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        proc.kill()
    return b"".join(chunks), proc.wait()


def _clean_terminal_output(raw: bytes) -> str:
    """Sortie d'un terminal → texte lisible par le modèle (sans codes couleur ni lignes réécrites par \\r)."""
    text = _ANSI_RX.sub("", raw.decode("utf-8", errors="replace"))
    text = text.replace("\r\n", "\n")
    # Barres de progression : seule la dernière version de la ligne compte
    lines = [l.rsplit("\r", 1)[-1] for l in text.split("\n")]
    text = "\n".join(lines).strip()
    if len(text) > SHELL_CTX_CHARS:
        half = SHELL_CTX_CHARS // 2
        text = f"{text[:half]}\n… [{len(text) - SHELL_CTX_CHARS} caractères omis] …\n{text[-half:]}"
    return text


class LiveFooterView:
    """
    Zone dynamique ancrée en bas de l'écran pendant le chargement et la génération :
      [indicateur animé (réflexion / chargement)]
      [ligne de réponse en cours d'écriture]
      [barre de prompt grisée + barre d'état en direct (t/s, contexte, RAM)]
    Les lignes de réponse terminées et les messages (console.print) s'impriment
    au-dessus de la zone, qui reste visible jusqu'à stop().
    """
    def __init__(self, footer_fn):
        self.footer_fn = footer_fn   # () -> renderable rich (barre de prompt + barre d'état)
        self.spinner = Spinner("dots", style="dim")
        self.header = None           # texte (markup rich) de l'indicateur, ou None
        self.partial = Text()        # ligne de réponse pas encore terminée
        self.live = None

    def __rich__(self):
        items = []
        if self.header:
            self.spinner.update(text=Text.from_markup(self.header))
            items.append(self.spinner)
        partial = self.partial
        if partial.plain:
            items.append(partial)
        items.append(self.footer_fn())
        return Group(*items)

    def start(self):
        if self.live is None:
            self.live = Live(self, console=console, refresh_per_second=4, transient=True)
            self.live.start()

    def set_header(self, markup):
        self.header = markup

    def write(self, text: str, style: str = ""):
        """Ajoute du texte de réponse : les lignes complètes passent au-dessus de la zone."""
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                console.print(self.partial)
                self.partial = Text()
            if part:
                new = self.partial.copy()  # nouvel objet : le thread de rafraîchissement lit une ligne cohérente
                new.append(part, style=style)
                self.partial = new

    def stop(self):
        if self.partial.plain:
            console.print(self.partial)
            self.partial = Text()
        self.header = None
        if self.live is not None:
            self.live.stop()
            self.live = None


class MarkdownStreamRenderer:
    """
    Affiche la réponse en streaming en convertissant le gras markdown (**texte**)
    en texte gras coloré, même si les ** sont découpés entre deux tokens.
    sink(texte, style) reçoit les morceaux à afficher.
    """
    BOLD_STYLE = "bold #00D8D6"

    def __init__(self, sink):
        self.sink = sink
        self.bold = False
        self.pending = ""

    def feed(self, token: str):
        s = self.pending + token
        self.pending = ""
        # Un '*' isolé en fin de token peut être la moitié d'un '**' : on attend la suite
        if s.endswith("*") and not s.endswith("**"):
            self.pending = "*"
            s = s[:-1]
        parts = s.split("**")
        for i, part in enumerate(parts):
            if i > 0:
                self.bold = not self.bold
            if part:
                self.sink(part, self.BOLD_STYLE if self.bold else "")

    def finish(self):
        if self.pending:
            self.sink(self.pending, "")
            self.pending = ""
        self.bold = False


class ThinkStepTracker:
    """
    Extrait en streaming les titres d'étape de la réflexion du modèle pour en afficher
    un sommaire (ex: « Recherche de l'erreur » → « Erreur trouvée » → « Correction »).
    Une ligne est considérée comme un titre si elle est :
      - un titre markdown            : ## Titre
      - en gras en début de ligne    : **Titre**  /  1. **Titre :** suite...
      - une étape explicite          : Étape : titre.  /  Step 2 - titre
    Les puces en gras (« * **Langue :** ... ») sont ignorées : ce sont des détails.
    """
    _PATTERNS = [
        re.compile(r"^\s*#{1,4}\s+(.+?)\s*$"),
        re.compile(r"^\s*(?:\d+[.)]\s*)?\*\*(.+?)\*\*"),
        re.compile(r"^\s*(?:Étape|Etape|Step)\s*\d*\s*[:\-–]\s*([^.!?\n]+)", re.IGNORECASE),
    ]

    def __init__(self):
        self.partial = ""
        self.steps = []

    def feed(self, text: str) -> list:
        """Ajoute du texte de réflexion ; retourne les nouveaux titres détectés (lignes complètes)."""
        self.partial += text
        new_steps = []
        while "\n" in self.partial:
            line, self.partial = self.partial.split("\n", 1)
            title = self._title(line)
            if title and (not self.steps or self.steps[-1] != title):
                self.steps.append(title)
                new_steps.append(title)
        return new_steps

    @classmethod
    def _title(cls, line: str):
        for pattern in cls._PATTERNS:
            m = pattern.match(line)
            if m:
                title = m.group(1).strip().strip("*").strip().rstrip(":").strip()
                if 2 <= len(title) <= 80:
                    return title[:60]
        return None


class ThinkStreamParser:
    """
    Analyse les tokens en temps réel pour détecter et séparer les blocs <think> ... </think>,
    même si du texte précède la balise ou si le tag est découpé en plusieurs chunks.
    Gère également l'affichage masqué (show_thinking=False) et le mode désactivé (thinking_enabled=False).
    """
    def __init__(self, on_think_start, on_think_token, on_think_end, on_response_start, on_response_token, show_thinking=True, thinking_enabled=True):
        self.on_think_start = on_think_start
        self.on_think_token = on_think_token
        self.on_think_end = on_think_end
        self.on_response_start = on_response_start
        self.on_response_token = on_response_token
        self.show_thinking = show_thinking
        self.thinking_enabled = thinking_enabled

        self.state = "STREAMING"  # STREAMING, IN_THINK, DISCARD_THINK
        self.buffer = ""
        self.think_tokens_count = 0
        self.response_tokens_count = 0
        self.had_think = False
        self.full_think_text = []
        self.full_response_text = []
        self.response_started = False

    def feed(self, token: str):
        self.buffer += token

        while self.buffer:
            if self.state == "STREAMING":
                # Vérifier si <think> commence dans le buffer
                if "<think>" in self.buffer:
                    idx = self.buffer.find("<think>")
                    before = self.buffer[:idx]
                    if before and before.strip():
                        # Vrai texte avant le <think>
                        if not self.response_started:
                            self.response_started = True
                            self.on_response_start()
                        self.on_response_token(before)
                        self.full_response_text.append(before)
                        self.response_tokens_count += len(before.split())

                    if self.thinking_enabled:
                        self.state = "IN_THINK"
                        self.had_think = True
                        self.on_think_start(self.show_thinking)
                    else:
                        # Le modèle réfléchit malgré /think off : on masque le texte
                        # mais on le signale (sinon l'attente semble inexpliquée)
                        self.state = "DISCARD_THINK"
                        self.had_think = True
                        self.on_think_start(False)

                    self.buffer = self.buffer[idx + 7:]
                    continue
                else:
                    # Si la réponse n'a pas encore commencé et que le buffer ne contient que des blancs/sauts de ligne,
                    # attendre le token suivant pour vérifier s'il s'agit de <think>
                    if not self.response_started and not self.had_think and not self.buffer.strip():
                        break

                    # Garder au plus 6 caractères qui pourraient être le début de "<think>"
                    possible_start = False
                    for i in range(1, min(7, len(self.buffer) + 1)):
                        if "<think>".startswith(self.buffer[-i:]):
                            possible_start = True
                            break

                    if possible_start and len(self.buffer) > 6:
                        safe_len = len(self.buffer) - 6
                        emit = self.buffer[:safe_len]
                        self.buffer = self.buffer[safe_len:]
                    elif not possible_start:
                        emit = self.buffer
                        self.buffer = ""
                    else:
                        break

                    if not self.response_started:
                        emit = emit.lstrip("\r\n")
                        if emit:
                            self.response_started = True
                            self.on_response_start()
                            self.on_response_token(emit)
                            self.full_response_text.append(emit)
                            self.response_tokens_count += len(emit.split())
                    else:
                        if emit:
                            self.on_response_token(emit)
                            self.full_response_text.append(emit)
                            self.response_tokens_count += len(emit.split())
                    break

            elif self.state == "IN_THINK":
                if "</think>" in self.buffer:
                    idx = self.buffer.find("</think>")
                    think_part = self.buffer[:idx]
                    if think_part:
                        self.on_think_token(think_part)
                        self.full_think_text.append(think_part)
                        self.think_tokens_count += len(think_part.split())

                    self.on_think_end(self.think_tokens_count, self.show_thinking)
                    self.state = "STREAMING"
                    self.buffer = self.buffer[idx + 8:].lstrip("\r\n")
                    continue
                else:
                    # Garder au plus 7 caractères qui pourraient être le début de "</think>"
                    possible_end = False
                    for i in range(1, min(8, len(self.buffer) + 1)):
                        if "</think>".startswith(self.buffer[-i:]):
                            possible_end = True
                            break

                    if possible_end and len(self.buffer) > 7:
                        safe_len = len(self.buffer) - 7
                        think_emit = self.buffer[:safe_len]
                        self.buffer = self.buffer[safe_len:]
                        self.on_think_token(think_emit)
                        self.full_think_text.append(think_emit)
                        self.think_tokens_count += len(think_emit.split())
                        break
                    elif not possible_end:
                        think_emit = self.buffer
                        self.buffer = ""
                        self.on_think_token(think_emit)
                        self.full_think_text.append(think_emit)
                        self.think_tokens_count += len(think_emit.split())
                        break
                    else:
                        break

            elif self.state == "DISCARD_THINK":
                if "</think>" in self.buffer:
                    idx = self.buffer.find("</think>")
                    self.on_think_end(0, False)
                    self.state = "STREAMING"
                    self.buffer = self.buffer[idx + 8:].lstrip("\r\n")
                    continue
                else:
                    possible_end = False
                    for i in range(1, min(8, len(self.buffer) + 1)):
                        if "</think>".startswith(self.buffer[-i:]):
                            possible_end = True
                            break
                    if possible_end and len(self.buffer) > 7:
                        self.buffer = self.buffer[-7:]
                        break
                    elif not possible_end:
                        self.buffer = ""
                        break
                    else:
                        break

    def finish(self):
        if self.buffer:
            if self.state == "IN_THINK":
                if self.thinking_enabled:
                    self.on_think_token(self.buffer)
                    self.full_think_text.append(self.buffer)
                    self.think_tokens_count += len(self.buffer.split())
                    self.on_think_end(self.think_tokens_count, self.show_thinking)
            elif self.state == "DISCARD_THINK":
                pass
            else:
                emit = self.buffer
                if not self.response_started:
                    emit = emit.lstrip("\r\n")
                    if emit:
                        self.response_started = True
                        self.on_response_start()
                        self.on_response_token(emit)
                        self.full_response_text.append(emit)
                        self.response_tokens_count += len(emit.split())
                else:
                    if emit:
                        self.on_response_token(emit)
                        self.full_response_text.append(emit)
                        self.response_tokens_count += len(emit.split())
            self.buffer = ""

class IADroidCompleter(Completer):
    """Autocomplétion intelligente pour les commandes slash et les noms de profils."""
    def __init__(self, manager: PresetManager):
        self.manager = manager
        self.slash_commands = [
            "/load",
            "/run",
            "/stop",
            "/create-preset",
            "/profil_config",
            "/profil_config_env",
            "/profil_Prompt_Systeme",
            "/device",
            "/effort",
            "/jobs",
            "/mtp",
            "/quantize",
            "/think",
            "/show-think",
            "/fa",
            "/update",
            "/presets",
            "/use",
            "/reset",
            "/check-gpu",
            "/boost-gpu",
            "/cmake-config-auto",
            "/cmake-recompiler",
            "/cmake-thread-compile",
            "/cmake-del-cache",
            "/llama-cli",
            "/agent",
            "/allow",
            "/permissions",
            "/memory",
            "/help",
            "/clear",
            "/exit"
        ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        if not words:
            for cmd in self.slash_commands:
                yield Completion(cmd, start_position=0)
            return

        # Complétion de la première commande (/...)
        if len(words) == 1 and not text.endswith(" "):
            prefix = words[0]
            for cmd in self.slash_commands:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        # Complétion des arguments de profil
        if words[0] in ("/load", "/use", "/profil_config", "/profil_config_env", "/profil_Prompt_Systeme"):
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for name in self.manager.list_presets():
                if name.startswith(sub_prefix):
                    yield Completion(name, start_position=-len(sub_prefix))
            return

        # Complétion pour /cmake-thread-compile
        if words[0] in ("/cmake-thread-compile", "/cmake-threads"):
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for t in ("2", "4", "6", "8"):
                if t.startswith(sub_prefix):
                    yield Completion(t, start_position=-len(sub_prefix))
            return

        # Complétion pour /device
        if words[0] == "/device":
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for dev in ("gpu", "cpu"):
                if dev.startswith(sub_prefix):
                    yield Completion(dev, start_position=-len(sub_prefix))
            return

        # Complétion pour /effort
        if words[0] in ("/effort", "/reasoning"):
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for eff in ("minimal", "low", "medium", "high", "xhigh", "off"):
                if eff.startswith(sub_prefix):
                    yield Completion(eff, start_position=-len(sub_prefix))
            return

        # Complétion pour /think
        if words[0] == "/think":
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for opt in ("on", "off"):
                if opt.startswith(sub_prefix):
                    yield Completion(opt, start_position=-len(sub_prefix))
            return

        # Complétion pour /show-think
        if words[0] in ("/show-think", "/think-display", "/show_think"):
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for opt in ("on", "off"):
                if opt.startswith(sub_prefix):
                    yield Completion(opt, start_position=-len(sub_prefix))
            return

        # Complétion des commandes du mode agent
        agent_opts = {
            "/agent": ("on", "off", "web", "steps"),
            "/allow": ("all", "remove", "~/", "/storage/emulated/0/"),
            "/permissions": ("ask", "auto"),
            "/memory": ("del", "clear"),
        }
        if words[0] in agent_opts and len(words) <= 2:
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            if len(words) == 2 and text.endswith(" "):
                if words[0] == "/agent" and words[1] == "web":
                    for opt in ("on", "off"):
                        yield Completion(opt, start_position=0)
                return
            for opt in agent_opts[words[0]]:
                if opt.startswith(sub_prefix):
                    yield Completion(opt, start_position=-len(sub_prefix))
            return

        # Complétion pour /fa
        if words[0] in ("/fa", "/flash-attention"):
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for opt in ("on", "off"):
                if opt.startswith(sub_prefix):
                    yield Completion(opt, start_position=-len(sub_prefix))
            return

        if words[0] in ("/mtp", "/quantize"):
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            opts = ("auto", "on", "off") if words[0] == "/mtp" else IADroidCLI.QUANT_TYPES
            for opt in opts:
                if opt.lower().startswith(sub_prefix.lower()):
                    yield Completion(opt, start_position=-len(sub_prefix))
            return


class IADroidCLI:
    def __init__(self):
        ensure_directories()
        self.manager = PresetManager()
        self.wizard = PresetWizard(self.manager)
        self.builder = LlamaBuilder()
        self.server = LlamaServer()
        # Messages de la conversation au format API (user / assistant / tool), sans le prompt système
        self.conversation_history = []
        # Prompt système de la conversation (profil + mémoire + consignes agent), figé jusqu'au
        # prochain /clear : le modifier en cours de route obligerait le serveur à retraiter tout le contexte
        self._session_system = None
        self.agent = Agent(console)
        self.agent.count_tokens = self.server.count_tokens
        # Sorties des commandes « !… » lancées par l'utilisateur, jointes au prochain message
        self._pending_shell: list = []
        self.context_tokens_used = 0
        self.last_speed = "--"
        self._last_status_check = 0.0
        self._cached_server_status = "stopped"
        self._live = None  # métriques en direct pendant chargement/génération (render_live_footer)
        
        # Instantané des métriques affichées dans la barre d'état.
        # Mis à jour par un thread de fond qui ne redessine l'écran QUE si une valeur
        # change de façon notable : un redessin périodique ramène Termux tout en bas
        # et empêche de remonter dans la conversation.
        self._ram_snapshot = get_ram_info()
        self._monitor_stop = threading.Event()
        self._jobs_snapshot = (0, 0)

        # Historique persistant des commandes
        # reserve_space_for_menu=0 : pas de lignes vides réservées pour l'autocomplétion,
        # la zone de saisie ne fait que la hauteur de son contenu et grandit si le texte déborde.
        history_file = str(IADROID_HOME / "history.txt")
        self.session = PromptSession(
            history=FileHistory(history_file),
            auto_suggest=AutoSuggestFromHistory(),
            completer=IADroidCompleter(self.manager),
            reserve_space_for_menu=0,
            # Invite effacée à la validation (voir start() et _echo_input)
            erase_when_done=True
        )

        # Style de l'invite de commande (Style Claude Code épuré, sans fond blanc disgracieux)
        self.style = Style.from_dict({
            'prompt': '#3DDC84 bold',
            'bottom-toolbar': 'noinherit default',
            'bottom-toolbar.text': 'noinherit default',
        })

    def get_prompt_message(self):
        """
        En-tête supérieur et barre de prompt style Claude Code (>):
        Affiche le niveau d'effort à droite, une ligne horizontale séparatrice ─,
        puis l'invite > en vert.
        """
        term_width = shutil.get_terminal_size().columns
        width = max(10, min(term_width - 1, 100))

        preset = self.manager.get_active_preset()
        rs = preset.get("reasoning", {}) if preset else {}
        effort = rs.get("effort", "medium") if rs.get("enabled", True) else "off"

        if effort in ("high", "xhigh"):
            effort_color = "#3DDC84"
        elif effort in ("medium", "low", "minimal"):
            effort_color = "#FFD32A"
        else:
            effort_color = "#777777"

        tag = f"● {effort} · /effort"
        agent_html = ""
        if self.agent.enabled:
            mode = self.agent.settings["mode"]
            agent_tag = f"⚙ agent:{mode} · "
            tag = agent_tag + tag
            agent_color = "#FF79C6" if mode == "auto" else "#00D8D6"
            agent_html = (f'<style fg="{agent_color}"><b>⚙ agent:{mode}</b></style>'
                          f'<style fg="#777777"> · </style>')
        pad = max(0, width - len(tag))

        return HTML(
            f'<style fg="#555555">{" " * pad}</style>'
            f'{agent_html}'
            f'<style fg="{effort_color}"><b>● {effort}</b></style>'
            f'<style fg="#777777"> · /effort</style>\n'
            f'<style fg="#555555">{"─" * width}</style>\n'
            f'<style fg="#3DDC84"><b>&gt; </b></style>'
        )

    def get_bottom_toolbar(self):
        """
        Barre d'état inférieure sous la zone de saisie style Claude Code :
        - Ligne 1 : Ligne horizontale de séparation ─
        - Ligne 2 : ⚡ vitesse | 🧠 tokens contexte (%) | 💾 RAM: used (pct%)/total | Lib: free | ● statut
        - Ligne 3 : ❯❯ Profil actif · Matériel · /help
        Redessinée uniquement sur changement notable (voir _status_monitor_loop).
        """
        term_width = shutil.get_terminal_size().columns
        width = max(10, min(term_width - 1, 100))
        sep_line = "─" * width

        used_ram, total_ram, ram_pct, free_ram = self._ram_snapshot
        if ram_pct >= 85:
            ram_badge = f'<style fg="#FF5555"><b>{used_ram:.1f}G ({ram_pct:.0f}%)</b></style>'
        elif ram_pct >= 70:
            ram_badge = f'<style fg="#FFD32A"><b>{used_ram:.1f}G ({ram_pct:.0f}%)</b></style>'
        else:
            ram_badge = f'<style fg="#3DDC84"><b>{used_ram:.1f}G ({ram_pct:.0f}%)</b></style>'

        ram_display = (
            f'<style fg="#FFA500">💾</style> '
            f'<style fg="#CCCCCC">RAM: </style>{ram_badge}'
            f'<style fg="#888888">/{total_ram:.1f}G</style> '
            f'<style fg="#555555">|</style> '
            f'<style fg="#888888">Rest: </style><style fg="#FFFFFF"><b>{free_ram:.1f}G</b></style>'
        )

        active_preset = self.manager.get_active_preset()
        if not active_preset:
            return HTML(
                f'<style fg="#555555">{sep_line}</style>\n'
                f'<style fg="#FFD32A"> <i>{ram_display} · Tapez /create-preset pour commencer.</i></style>'
            )

        p_name = active_preset.get("name", "Inconnu")
        hw = active_preset.get("hardware", {})
        dev = hw.get("device", "gpu").upper()
        ctx_total = active_preset.get("context", {}).get("ctx_size", 4096)

        # Progression contexte
        used = self.context_tokens_used
        ratio = min(1.0, max(0.0, used / max(1, ctx_total)))
        pct = ratio * 100

        # Statut serveur : rafraîchi si le cache a été invalidé (ex: après /load, /stop),
        # sinon fourni par le thread de surveillance (_status_monitor_loop)
        if self._last_status_check == 0.0:
            self._cached_server_status = self.server.get_status()
            self._last_status_check = time.time()
        server_status = self._cached_server_status

        if server_status == "ready":
            status_badge = '<style fg="#3DDC84"><b>● Prêt (VRAM)</b></style>'
        elif server_status == "loading":
            status_badge = '<style fg="#FFD32A"><b>⏳ Chargement...</b></style>'
        else:
            status_badge = '<style fg="#777777"><b>○ Arrêté (/run)</b></style>'

        speed_str = f"{self.last_speed} t/s" if self.last_speed != "--" else "-- t/s"
        dev_color = "#3DDC84" if dev == "GPU" else "#FFD32A"
        line_border = f'<style fg="#555555">{sep_line}</style>'
        jobs = self._jobs_badge()
        if jobs:
            status_badge += f' <style fg="#555555">|</style> <style fg="#00D8D6"><b>{jobs}</b></style>'

        if width < 75:
            line_metrics = (
                f' <style fg="#FFA500">⚡</style> <style fg="#00D8D6"><b>{speed_str}</b></style> '
                f'<style fg="#555555">|</style> '
                f'<style fg="#FF79C6">🧠</style> <style fg="#FFFFFF"><b>{used}/{ctx_total}</b></style> '
                f'<style fg="#3DDC84">({pct:.0f}%)</style> '
                f'<style fg="#555555">|</style> '
                f'{status_badge}'
            )
            line_ram = f' {ram_display}'
            line_info = (
                f' <style fg="#FFD32A"><b>❯❯</b></style> '
                f'<style fg="#00D8D6"><b>{p_name}</b></style> '
                f'<style fg="#555555">·</style> '
                f'<style fg="{dev_color}"><b>{dev}</b></style> '
                f'<style fg="#555555">·</style> '
                f'<style fg="#888888">Tapez </style><style fg="#3DDC84"><b>/help</b></style>'
            )
            return HTML(f"{line_border}\n{line_metrics}\n{line_ram}\n{line_info}")
        else:
            line_metrics = (
                f' <style fg="#FFA500">⚡</style> <style fg="#00D8D6"><b>{speed_str}</b></style> '
                f'<style fg="#555555">|</style> '
                f'<style fg="#FF79C6">🧠</style> <style fg="#CCCCCC">Ctx: </style>'
                f'<style fg="#FFFFFF"><b>{used}/{ctx_total}</b></style> '
                f'<style fg="#3DDC84"><b>({pct:.1f}%)</b></style> '
                f'<style fg="#555555">|</style> '
                f'{ram_display} '
                f'<style fg="#555555">|</style> '
                f'{status_badge}'
            )
            line_info = (
                f' <style fg="#FFD32A"><b>❯❯</b></style> '
                f'<style fg="#888888">Profil: </style><style fg="#00D8D6"><b>{p_name}</b></style> '
                f'<style fg="#555555">·</style> '
                f'<style fg="{dev_color}"><b>{dev}</b></style> '
                f'<style fg="#555555">·</style> '
                f'<style fg="#888888">Tapez </style><style fg="#3DDC84"><b>/help</b></style>'
                f'<style fg="#888888"> · </style><style fg="#FFD32A"><b>/stop</b></style>'
                f'<style fg="#888888"> (VRAM)</style>'
            )
            return HTML(f"{line_border}\n{line_metrics}\n{line_info}")

    def render_live_footer(self):
        """
        Version rich de la barre de prompt + barre d'état, affichée pendant le chargement
        et la génération (LiveFooterView). Les valeurs viennent de self._live :
          {"status": "loading"|"generating", "t0": début, "tokens": n, "first_tok": t, "ctx_base": n}
        """
        live = self._live or {}
        term_width = shutil.get_terminal_size().columns
        width = max(10, min(term_width - 1, 100))
        sep = f"[#555555]{'─' * width}[/]"
        now = time.time()

        preset = self.manager.get_active_preset() or {}
        rs = preset.get("reasoning", {})
        effort = rs.get("effort", "medium") if rs.get("enabled", True) else "off"
        effort_color = "#3DDC84" if effort in ("high", "xhigh") else ("#FFD32A" if effort in ("medium", "low", "minimal") else "#777777")
        tag = f"● {effort} · /effort"
        agent_markup = ""
        if self.agent.enabled:
            mode = self.agent.settings["mode"]
            tag = f"⚙ agent:{mode} · " + tag
            agent_color = "#FF79C6" if mode == "auto" else "#00D8D6"
            agent_markup = f"[bold {agent_color}]⚙ agent:{mode}[/][#777777] · [/]"
        effort_line = (f"{' ' * max(0, width - len(tag))}{agent_markup}"
                       f"[bold {effort_color}]● {escape(effort)}[/][#777777] · /effort[/]")

        status = live.get("status")
        elapsed = int(now - live.get("t0", now))
        if status == "loading":
            prompt_hint = f"[#777777]chargement du modèle… ({elapsed}s)[/]"
            badge = f"[bold #FFD32A]⏳ Chargement {elapsed}s[/]"
        else:
            prompt_hint = "[#777777]génération en cours… (Ctrl+C pour interrompre)[/]"
            badge = "[bold #00D8D6]● Génération[/]"

        # Vitesse en direct : tokens reçus depuis le premier token
        tokens = live.get("tokens", 0)
        first = live.get("first_tok")
        if status == "generating" and first and tokens > 1 and now - first > 0.5:
            speed_str = f"{tokens / (now - first):.1f} t/s"
        else:
            speed_str = f"{self.last_speed} t/s" if self.last_speed != "--" else "-- t/s"

        ctx_total = preset.get("context", {}).get("ctx_size", 4096)
        used = live.get("ctx_base", self.context_tokens_used) + (tokens if status == "generating" else 0)
        pct = min(100.0, used / max(1, ctx_total) * 100)
        ctx_color = "#FF5555" if pct >= 90 else ("#FFD32A" if pct >= 75 else "#3DDC84")

        used_ram, total_ram, ram_pct, free_ram = get_ram_info()
        ram_color = "#FF5555" if ram_pct >= 85 else ("#FFD32A" if ram_pct >= 70 else "#3DDC84")
        ram = (f"[#FFA500]💾[/] [#CCCCCC]RAM: [/][bold {ram_color}]{used_ram:.1f}G ({ram_pct:.0f}%)[/]"
               f"[#888888]/{total_ram:.1f}G[/] [#555555]|[/] [#888888]Rest: [/][bold #FFFFFF]{free_ram:.1f}G[/]")

        metrics = (f" [#FFA500]⚡[/] [bold #00D8D6]{speed_str}[/] [#555555]|[/] "
                   f"[#FF79C6]🧠[/] [#CCCCCC]Ctx: [/][bold #FFFFFF]{used}/{ctx_total}[/] [bold {ctx_color}]({pct:.1f}%)[/]")
        p_name = escape(preset.get("name", "Inconnu"))
        dev = preset.get("hardware", {}).get("device", "gpu").upper()
        dev_color = "#3DDC84" if dev == "GPU" else "#FFD32A"
        info = (f" [bold #FFD32A]❯❯[/] [#888888]Profil: [/][bold #00D8D6]{p_name}[/] "
                f"[#555555]·[/] [bold {dev_color}]{dev}[/]")

        jobs = self._jobs_badge()
        if jobs:
            badge += f" [#555555]|[/] [bold #00D8D6]{escape(jobs)}[/]"
        if width < 75:
            lines = [f"{metrics} [#555555]|[/] {badge}", f" {ram}", info]
        else:
            lines = [f"{metrics} [#555555]|[/] {ram} [#555555]|[/] {badge}", info]

        return Text.from_markup("\n".join([effort_line, sep, f"[bold #3DDC84]>[/] {prompt_hint}", sep] + lines))

    def _jobs_badge(self) -> str:
        """Pastille des commandes d'arrière-plan de l'agent : « ◐ 1 commande · ✓ 1 finie » (vide s'il n'y en a pas)."""
        running, finished = self.agent.jobs_counts()
        parts = []
        if running:
            parts.append(f"◐ {running} commande{'s' if running > 1 else ''} (/jobs)")
        if finished:
            parts.append(f"✓ {finished} finie{'s' if finished > 1 else ''}")
        return " · ".join(parts)

    def _start_server_live(self, preset) -> bool:
        """Démarre llama-server en gardant la barre d'état visible (temps de chargement, RAM en direct)."""
        self._live = {"status": "loading", "t0": time.time()}
        view = LiveFooterView(self.render_live_footer)
        view.set_header("[bold cyan]Chargement du modèle en VRAM...[/bold cyan]")
        view.start()
        try:
            return self.server.start(preset, progress=view.set_header)
        finally:
            view.stop()
            self._live = None
            self._last_status_check = 0.0

    def _status_monitor_loop(self):
        """
        Surveille RAM et statut serveur en arrière-plan pendant la saisie.
        Ne déclenche un redessin que lors d'un changement notable (statut serveur,
        ou variation de RAM ≥ 3 %), afin de ne pas ramener le terminal en bas
        quand l'utilisateur remonte lire la conversation.
        """
        while not self._monitor_stop.wait(2.0):
            try:
                # Ne surveiller que pendant la saisie : pendant /load, /run ou une génération,
                # le thread principal gère lui-même le serveur (évite les accès concurrents)
                app = self.session.app
                if not app.is_running:
                    continue

                changed = False

                ram = get_ram_info()
                if abs(ram[2] - self._ram_snapshot[2]) >= 3.0:
                    self._ram_snapshot = ram
                    changed = True

                jobs = self.agent.jobs_counts()
                if jobs != self._jobs_snapshot:
                    self._jobs_snapshot = jobs
                    changed = True

                status = self.server.get_status()
                self._last_status_check = time.time()
                if status != self._cached_server_status:
                    self._cached_server_status = status
                    changed = True

                if changed and app.is_running:
                    app.invalidate()
            except Exception:
                pass

    def reset_conversation(self):
        """Oublie l'historique de la conversation (contexte envoyé au modèle) et remet les compteurs à zéro."""
        self.conversation_history.clear()
        self._pending_shell.clear()
        self._session_system = None
        self.agent.session_paths.clear()
        self.agent.session_always.clear()
        active_preset = self.manager.get_active_preset()
        sys_p = active_preset.get("context", {}).get("system_prompt", "") if active_preset else ""
        self.context_tokens_used = int(len(sys_p.split()) * 1.3)
        self.last_speed = "--"

    def handle_command(self, user_input: str) -> bool:
        """Exécute une commande utilisateur. Retourne False pour quitter."""
        raw = user_input.strip()
        if not raw:
            return True

        # !commande : exécutée directement dans le terminal, sans passer par le modèle
        if raw.startswith("!"):
            self._run_user_shell(raw[1:])
            return True

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # /exit ou /quit
        if cmd in ("/exit", "/quit", "exit", "quit"):
            self.server.stop()
            console.print("[yellow]Fermeture d'IADroid. À bientôt ![/yellow]")
            return False

        # /help
        elif cmd in ("/help", "/aide", "help"):
            print_help_table()
            return True

        # /clear
        elif cmd in ("/clear", "clear"):
            # Nouvelle conversation : écran + historique de défilement + contexte du modèle
            self.reset_conversation()
            os.system("clear" if os.name != "nt" else "cls")
            sys.stdout.write("\033[3J")  # efface aussi l'historique de défilement du terminal
            sys.stdout.flush()
            display_welcome_banner(self.manager.get_active_preset())
            console.print(f"[dim]✓ Nouvelle conversation (contexte réinitialisé).[/dim]")
            return True

        # /create-preset
        elif cmd == "/create-preset":
            self.wizard.run_wizard()
            return True

        # /profil_config <nom>
        elif cmd == "/profil_config":
            target = arg or self.manager.get_active_preset_name()
            if not target:
                console.print("[yellow]Veuillez spécifier le profil à éditer : /profil_config <nom>[/yellow]")
            else:
                self.wizard.run_wizard(target)
            return True

        # /profil_config_env <nom>
        elif cmd == "/profil_config_env":
            target = arg or self.manager.get_active_preset_name()
            if not target:
                console.print("[yellow]Veuillez spécifier un profil : /profil_config_env <nom>[/yellow]")
            else:
                self.manager.display_preset_env(target)
            return True

        # /profil_Prompt_Systeme <nom>
        elif cmd in ("/profil_prompt_systeme", "/prompt"):
            target = arg or self.manager.get_active_preset_name()
            if not target:
                console.print("[yellow]Veuillez spécifier un profil : /profil_Prompt_Systeme <nom>[/yellow]")
            else:
                self.wizard.edit_system_prompt_only(target)
            return True

        # /device <gpu|cpu> [ngl]
        elif cmd == "/device":
            active_name = self.manager.get_active_preset_name()
            if not active_name:
                console.print("[yellow]Aucun profil actif.[/yellow]")
            else:
                parts = arg.split()
                mode = parts[0].lower() if parts else ""
                if mode not in ("gpu", "cpu"):
                    console.print("[yellow]Usage : /device <gpu|cpu> [ngl][/yellow]")
                else:
                    ngl_val = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                    self.manager.set_device_mode(active_name, mode, ngl=ngl_val)
                    self._cpu_hints(self.manager.get_active_preset())
                    if self.server.is_ready():
                        console.print(f"[cyan]Redémarrage du modèle en mode {mode.upper()}...[/cyan]")
                        self._start_server_live(self.manager.get_active_preset())
                        self._last_status_check = 0.0
            return True

        # /fa <on|off> : Activer ou désactiver Flash Attention (-fa)
        elif cmd in ("/fa", "/flash-attention"):
            active_name = self.manager.get_active_preset_name()
            if not active_name:
                console.print("[yellow]Aucun profil actif.[/yellow]")
            elif not arg:
                preset = self.manager.get_active_preset()
                hw = preset.get("hardware", {}) if preset else {}
                dev = hw.get("device", "gpu")
                is_on = hw.get("gpu_flash_attention", False) if dev == "gpu" else hw.get("flash_attention", True)
                state = "[bold green]activé (ON)[/bold green]" if is_on else "[bold yellow]désactivé (OFF)[/bold yellow]"
                console.print(f"Flash Attention (-fa) : {state} (mode actuel: {dev.upper()})")
                console.print("[dim]Usage : /fa <on|off>[/dim]")
                if dev == "gpu":
                    gpu_name = get_gpu_type()
                    gpu_str = f" ({gpu_name})" if gpu_name != "Vulkan" else ""
                    console.print(f"[dim yellow]Note : Sur GPU Vulkan mobile{gpu_str}, Flash Attention fait souvent crasher le pilote.[/dim yellow]")
            else:
                opt = arg.lower()
                if opt in ("on", "true", "1", "oui"):
                    self.manager.set_flash_attention(active_name, True)
                elif opt in ("off", "false", "0", "non"):
                    self.manager.set_flash_attention(active_name, False)
                else:
                    console.print("[yellow]Usage : /fa <on|off>[/yellow]")
            return True

        # /mtp <auto|on|off> : décodage spéculatif par les couches MTP du modèle
        elif cmd == "/mtp":
            self._cmd_mtp(arg)
            return True

        # /quantize [type] : convertir le modèle du profil (Q4_0 par défaut, le plus rapide en CPU)
        elif cmd == "/quantize":
            self._cmd_quantize(arg)
            return True

        # /effort <minimal|low|medium|high|xhigh|off>
        elif cmd in ("/effort", "/reasoning"):
            active_name = self.manager.get_active_preset_name()
            if not active_name:
                console.print("[yellow]Aucun profil actif.[/yellow]")
            elif not arg:
                preset = self.manager.get_active_preset()
                rs = preset.get("reasoning", {}) if preset else {}
                curr = rs.get("effort", "medium") if rs.get("enabled", True) else "off"
                console.print(f"[cyan]Niveau d'effort de réflexion actuel : [bold]{curr}[/bold][/cyan]")
                console.print("[dim]Usage : /effort <minimal|low|medium|high|xhigh|off>[/dim]")
            else:
                self.manager.set_reasoning_effort(active_name, arg.lower())
            return True

        # /think <on|off>
        elif cmd == "/think":
            active_name = self.manager.get_active_preset_name()
            if not active_name:
                console.print("[yellow]Aucun profil actif.[/yellow]")
            elif not arg:
                preset = self.manager.get_active_preset()
                rs = preset.get("reasoning", {}) if preset else {}
                is_on = rs.get("enabled", True) and rs.get("effort", "medium") != "off"
                state = "[bold green]activé (ON)[/bold green]" if is_on else "[bold yellow]désactivé (OFF)[/bold yellow]"
                console.print(f"Mode réflexion (Thinking) : {state}")
                console.print("[dim]Usage : /think <on|off>[/dim]")
            else:
                opt = arg.lower()
                if opt in ("on", "true", "1", "oui"):
                    self.manager.set_reasoning_effort(active_name, "medium")
                elif opt in ("off", "false", "0", "non"):
                    self.manager.set_reasoning_effort(active_name, "off")
                else:
                    console.print("[yellow]Usage : /think <on|off>[/yellow]")
            return True

        # /show-think <on|off> : Activer ou masquer les étapes de réflexion
        elif cmd in ("/show-think", "/think-display", "/show_think", "/showthink"):
            active_name = self.manager.get_active_preset_name()
            if not active_name:
                console.print("[yellow]Aucun profil actif.[/yellow]")
            elif not arg:
                preset = self.manager.get_active_preset()
                rs = preset.get("reasoning", {}) if preset else {}
                curr = rs.get("show_thinking", True)
                state = "[bold green]activé (ON - aperçu 5 mots)[/bold green]" if curr else "[bold yellow]masqué (OFF - indicateur seul)[/bold yellow]"
                console.print(f"Affichage de la réflexion : {state}")
                console.print("[dim]Usage : /show-think <on|off>[/dim]")
            else:
                opt = arg.lower()
                if opt in ("on", "true", "1", "oui", "visible"):
                    self.manager.set_show_thinking(active_name, True)
                elif opt in ("off", "false", "0", "non", "masquer", "cache"):
                    self.manager.set_show_thinking(active_name, False)
                else:
                    console.print("[yellow]Usage : /show-think <on|off>[/yellow]")
            return True

        # /update : Vérifier et installer les mises à jour GitHub
        elif cmd in ("/update", "/upgrade", "/maj"):
            from .updater import run_update_interactive
            run_update_interactive()
            return True

        # /presets ou /list
        elif cmd in ("/presets", "/list"):
            self.manager.display_presets_table()
            return True

        # /load ou /select : Charger un profil en mémoire VRAM/RAM avec llama-server
        elif cmd in ("/load", "/select", "/start"):
            target = arg or self.manager.get_active_preset_name()
            if not target:
                console.print("[yellow]Veuillez spécifier le profil à charger : /load <nom>[/yellow]")
                return True
            
            if not self.manager.preset_exists(target):
                console.print(f"[red]Le profil '{target}' n'existe pas.[/red]")
                return True

            self.manager.set_active_preset_name(target)
            self.reset_conversation()
            preset = self.manager.get_active_preset()

            console.print(f"\n[bold {ANDROID_GREEN}]⚡ Chargement du profil '{target}' avec llama-server...[/bold {ANDROID_GREEN}]")
            success = self._start_server_live(preset)
            self._last_status_check = 0.0
            if success:
                console.print(f"[bold {ANDROID_GREEN}]✓ Modèle '{target}' prêt ! Posez votre question directement ci-dessous au prompt >[/bold {ANDROID_GREEN}]\n")
            return True

        # /run : Démarre le serveur et garde l'interface IADroid
        elif cmd == "/run":
            preset = self.manager.get_active_preset()
            if not preset:
                console.print("[yellow]Aucun profil actif. Utilisez /create-preset pour en créer un.[/yellow]")
                return True
            p_name = preset.get("name", "inconnu")
            console.print(f"\n[bold {ANDROID_GREEN}]⚡ Démarrage du profil '{p_name}' avec llama-server...[/bold {ANDROID_GREEN}]")
            success = self._start_server_live(preset)
            self._last_status_check = 0.0
            if success:
                console.print(f"[bold {ANDROID_GREEN}]✓ Modèle '{p_name}' chargé et prêt en mémoire ![/bold {ANDROID_GREEN}]")
                console.print("[dim]L'interface IADroid reste active. Tapez directement votre message au prompt > ci-dessous.[/dim]\n")
            return True

        # /stop : Arrêter le serveur actif et libérer la mémoire RAM et VRAM GPU
        elif cmd in ("/stop", "/kill"):
            self.server.stop()
            self._last_status_check = 0.0
            console.print(f"[bold {ANDROID_GREEN}]✓ Serveur arrêté. Mémoire RAM et VRAM GPU libérées avec succès ![/bold {ANDROID_GREEN}]")
            return True

        # /reset : Réinitialiser la conversation
        elif cmd == "/reset":
            self.reset_conversation()
            console.print(f"[bold {ANDROID_GREEN}]✓ Contexte de la conversation réinitialisé.[/bold {ANDROID_GREEN}]")
            return True

        # /use <nom>
        elif cmd == "/use":
            if not arg:
                console.print("[yellow]Usage : /use <nom_du_profil>[/yellow]")
            else:
                if self.manager.set_active_preset_name(arg):
                    self.reset_conversation()
                    console.print(f"[bold {ANDROID_GREEN}]✓ Profil actif défini sur '{arg}' (contexte réinitialisé).[/bold {ANDROID_GREEN}]")
            return True

        # /check-gpu : Diagnostic et liaison Vulkan GPU
        elif cmd in ("/check-gpu", "/gpu-check"):
            self.builder.check_gpu()
            return True

        # /boost-gpu : Forcer la fréquence GPU max (1000 MHz) et optimiser Turnip
        elif cmd in ("/boost-gpu", "/gpu-boost"):
            self.builder.boost_gpu()
            return True

        # /cmake-thread-compile : Choisir le nombre de threads avec affichage des risques
        elif cmd in ("/cmake-thread-compile", "/cmake-threads"):
            self.builder.interactive_threads_menu(arg)
            return True

        # /cmake-del-cache : Supprimer le cache CMake
        elif cmd == "/cmake-del-cache":
            self.builder.delete_cache()
            return True

        # /cmake-config-auto : Reconfigurer CMake automatiquement avec Vulkan
        elif cmd == "/cmake-config-auto":
            self.builder.config_auto(force_vulkan=True)
            return True

        # /cmake-recompiler : Vérifier les mises à jour et recompiler proprement
        elif cmd == "/cmake-recompiler":
            self.builder.recompile(check_updates=True)
            return True

        # /llama-cli : Mode expert brut en ligne de commande
        elif cmd in ("/llama-cli", "/cli-raw"):
            self.run_raw_llama_cli()
            return True

        # Mode agent : /agent, /allow, /permissions, /memory
        elif cmd == "/agent":
            self._cmd_agent(arg)
            return True

        elif cmd == "/allow":
            self._cmd_allow(arg)
            return True

        elif cmd in ("/permissions", "/perm"):
            self._cmd_permissions(arg)
            return True

        elif cmd in ("/memory", "/memoire", "/mémoire"):
            self._cmd_memory(arg)
            return True

        elif cmd == "/jobs":
            self._cmd_jobs(arg)
            return True

        # Commande non reconnue
        elif cmd.startswith("/"):
            console.print(f"[red]Commande inconnue : {cmd}. Tapez /help pour la liste.[/red]")
            return True

        else:
            # Traitement direct du prompt utilisateur avec streaming en direct dans l'interface IADroid
            self.stream_response(user_input)
            return True

    # ------------------------------------------------------------------ mode agent : commandes

    def _cmd_agent(self, arg: str):
        """/agent [on|off|web on|off|steps N] : état et réglages du mode agent."""
        s = self.agent.settings
        words = arg.lower().split()
        if not words:
            state = "[bold green]activé[/bold green]" if s["enabled"] else "[bold yellow]désactivé[/bold yellow]"
            console.print(f"Mode agent : {state}")
            console.print(f"  Permissions : [bold]{s['mode']}[/bold]  [dim](/permissions ask|auto)[/dim]")
            console.print(f"  Dossiers    : {escape(', '.join(short_path(p) for p in self.agent.allowed_roots()))}  [dim](/allow)[/dim]")
            console.print(f"  Web         : {'oui' if s['web'] else 'non'}  [dim](/agent web on|off)[/dim]")
            console.print(f"  Étapes max  : {s['max_steps']}  [dim](/agent steps N)[/dim]")
            console.print(f"  Notes       : {len(memory.list_notes())}  [dim](/memory)[/dim]")
            console.print("[dim]Usage : /agent <on|off>[/dim]")
            return
        if words[0] in ("on", "off"):
            s["enabled"] = words[0] == "on"
            self._session_system = None  # consignes de l'agent ajoutées/retirées du prompt système
            if s["enabled"]:
                console.print(f"[bold {ANDROID_GREEN}]✓ Mode agent activé[/bold {ANDROID_GREEN}] "
                              f"[dim](permissions : {s['mode']} · dossier de travail : "
                              f"{escape(short_path(self.agent.allowed_roots()[0]))})[/dim]")
            else:
                console.print("[yellow]Mode agent désactivé : le modèle répond sans outils.[/yellow]")
        elif words[0] == "web" and len(words) > 1 and words[1] in ("on", "off"):
            s["web"] = words[1] == "on"
            console.print(f"[cyan]Accès web de l'agent : {'activé' if s['web'] else 'désactivé'}.[/cyan]")
        elif words[0] == "steps" and len(words) > 1 and words[1].isdigit():
            s["max_steps"] = max(1, min(100, int(words[1])))
            console.print(f"[cyan]Étapes maximum par demande : {s['max_steps']}.[/cyan]")
        else:
            console.print("[yellow]Usage : /agent <on|off> · /agent web <on|off> · /agent steps <N>[/yellow]")

    def _cmd_allow(self, arg: str):
        """/allow [chemin|all|remove chemin] : dossiers que l'agent peut utiliser sans confirmation d'accès."""
        s = self.agent.settings
        paths = list(s["allowed_paths"])
        words = arg.split(maxsplit=1)
        if not words:
            console.print("[bold]Dossiers autorisés pour l'agent :[/bold]")
            console.print(f"  • {escape(short_path(self.agent.allowed_roots()[0]))} [dim](dossier de travail, toujours)[/dim]")
            for p in paths:
                console.print(f"  • {escape(short_path(p))}")
            for p in self.agent.session_paths:
                console.print(f"  • {escape(short_path(p))} [dim](cette conversation)[/dim]")
            console.print("[dim]Usage : /allow <chemin> · /allow all (Termux + /storage/emulated/0) · /allow remove <chemin>[/dim]")
            return
        if words[0].lower() == "all":
            added = []
            for p in (os.path.expanduser("~"), SHARED_STORAGE):
                rp = os.path.realpath(p)
                if os.path.isdir(rp) and rp not in paths:
                    paths.append(rp)
                    added.append(rp)
            s["allowed_paths"] = paths
            console.print(f"[bold {ANDROID_GREEN}]✓ Accès étendu à Termux et au stockage partagé.[/bold {ANDROID_GREEN}]")
            for p in added:
                console.print(f"  + {escape(short_path(p))}")
            return
        if words[0].lower() in ("remove", "rm", "del") and len(words) > 1:
            target = os.path.realpath(os.path.expanduser(words[1].strip()))
            if target in paths:
                paths.remove(target)
                s["allowed_paths"] = paths
                console.print(f"[cyan]✓ Retiré : {escape(short_path(target))}[/cyan]")
            elif target in self.agent.session_paths:
                self.agent.session_paths.remove(target)
                console.print(f"[cyan]✓ Retiré : {escape(short_path(target))}[/cyan]")
            else:
                console.print(f"[yellow]Ce dossier n'est pas dans la liste : {escape(short_path(target))}[/yellow]")
            return
        target = os.path.realpath(os.path.expanduser(arg.strip()))
        if not os.path.isdir(target):
            console.print(f"[red]Dossier introuvable : {escape(target)}[/red]")
            return
        if target not in paths:
            paths.append(target)
            s["allowed_paths"] = paths
        console.print(f"[bold {ANDROID_GREEN}]✓ Dossier autorisé : {escape(short_path(target))}[/bold {ANDROID_GREEN}]")
        if s["mode"] == "ask":
            console.print("[dim]Les écritures et commandes restent confirmées (mode ask). /permissions auto pour tout autoriser.[/dim]")

    def _cmd_permissions(self, arg: str):
        s = self.agent.settings
        opt = arg.lower().strip()
        if opt in ("ask", "auto"):
            s["mode"] = opt
            self.agent.session_always.clear()
        elif opt:
            console.print("[yellow]Usage : /permissions <ask|auto>[/yellow]")
            return
        if s["mode"] == "auto":
            console.print("Permissions : [bold #FF79C6]auto[/bold #FF79C6] — l'agent écrit, modifie et lance des commandes "
                          "sans confirmation dans les dossiers autorisés. [dim](hors de ces dossiers : confirmation)[/dim]")
        else:
            console.print("Permissions : [bold cyan]ask[/bold cyan] — confirmation avant chaque écriture, modification ou commande.")

    def _cpu_hints(self, preset):
        """Après /device cpu : conseils mesurés (Q4_0, MTP)."""
        if not preset or preset.get("hardware", {}).get("device") != "cpu":
            return
        model = os.path.basename(preset.get("model_path") or "")
        if not re.search(r"q4_0", model, re.I):
            console.print("[dim]💡 En CPU, un modèle Q4_0 traite le prompt bien plus vite (48 t/s contre 31 t/s "
                          "pour Qwen3.5-9B Q4_K_M, mesuré sur Snapdragon 8 Gen 3) : /quantize.[/dim]")
        if mtp_active(preset):
            console.print("[dim]⚡ Décodage spéculatif MTP activé pour ce modèle (/mtp).[/dim]")

    def _cmd_mtp(self, arg: str):
        """/mtp [auto|on|off]"""
        name = self.manager.get_active_preset_name()
        preset = self.manager.get_active_preset()
        if not name or not preset:
            console.print("[yellow]Aucun profil actif.[/yellow]")
            return
        has = gguf_has_mtp(preset.get("model_path") or "")
        opt = arg.lower().strip()
        if not opt:
            mode = preset.get("hardware", {}).get("speculative", "auto")
            console.print(f"Décodage spéculatif MTP : réglage [bold]{mode}[/bold] · "
                          + ("[bold green]actif[/bold green]" if mtp_active(preset) else "[yellow]inactif[/yellow]"))
            console.print("  Le modèle " + ("[green]contient[/green]" if has else "[yellow]ne contient pas[/yellow]")
                          + " de couches MTP (prédiction de plusieurs tokens à la fois).")
            console.print("[dim]Usage : /mtp <auto|on|off> · auto = actif en mode CPU uniquement "
                          "(sur GPU, la vérification des tokens proposés ralentit la génération).[/dim]")
            return
        if opt not in ("auto", "on", "off"):
            console.print("[yellow]Usage : /mtp <auto|on|off>[/yellow]")
            return
        self.manager.set_speculative(name, opt)
        preset = self.manager.get_active_preset()
        state = "[bold green]actif[/bold green]" if mtp_active(preset) else "[yellow]inactif[/yellow]"
        console.print(f"✓ MTP réglé sur [bold]{opt}[/bold] : {state}.")
        if not has and opt != "off":
            console.print("[yellow]Ce modèle n'a pas de couches MTP : le réglage n'aura aucun effet.[/yellow]")
        if self.server.is_ready():
            console.print("[cyan]Redémarrage du modèle pour appliquer le réglage...[/cyan]")
            self._start_server_live(preset)
            self._last_status_check = 0.0

    QUANT_TYPES = ("Q4_0", "Q4_1", "IQ4_NL", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0")

    def _cmd_quantize(self, arg: str):
        """/quantize [type] : convertit le modèle du profil actif (Q4_0 par défaut) dans ~/.iadroid/models."""
        from rich.prompt import Confirm
        import shlex
        name = self.manager.get_active_preset_name()
        preset = self.manager.get_active_preset()
        if not name or not preset:
            console.print("[yellow]Aucun profil actif.[/yellow]")
            return
        qtype = (arg.strip() or "Q4_0").upper()
        if qtype not in self.QUANT_TYPES:
            console.print(f"[yellow]Usage : /quantize [{'|'.join(self.QUANT_TYPES)}] (Q4_0 par défaut)[/yellow]")
            return
        src = preset.get("model_path") or ""
        if not os.path.isfile(src):
            console.print(f"[red]Fichier modèle introuvable : {escape(src)}[/red]")
            return
        tool = get_quantize_path()
        if not tool:
            console.print("[red]llama-quantize introuvable : recompilez llama.cpp avec /cmake-recompiler.[/red]")
            return

        stem = os.path.splitext(os.path.basename(src))[0]
        current = re.search(r"(?:^|[.\-_])(I?Q\d\w*|BF16|F16|F32)$", stem, re.I)
        if current and current.group(1).upper() == qtype:
            console.print(f"[green]Le modèle est déjà en {qtype}.[/green]")
            return
        base = stem[:current.start()] if current else stem
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        out = MODELS_DIR / f"{base}-{qtype}.gguf"

        size = os.path.getsize(src)
        console.print(f"[bold]Conversion en {qtype}[/bold] : {escape(os.path.basename(src))} → {escape(short_path(str(out)))}")
        requant = not (current and current.group(1).upper() in ("BF16", "F16", "F32"))
        if requant:
            console.print("[dim]Le modèle source est déjà quantifié : la conversion fait perdre un peu de qualité. "
                          "L'idéal est de télécharger directement un GGUF dans ce format.[/dim]")
        if out.exists():
            console.print(f"[yellow]{escape(short_path(str(out)))} existe déjà.[/yellow]")
            if not Confirm.ask("  L'utiliser sans reconvertir ?", default=True):
                return
        else:
            free = shutil.disk_usage(str(MODELS_DIR)).free
            if free < size * 1.05:
                console.print(f"[red]Espace insuffisant : {free / 1e9:.1f} Go libres, ~{size / 1e9:.1f} Go nécessaires.[/red]")
                return
            if not Confirm.ask(f"  Lancer la conversion (~{size / 1e9:.1f} Go à écrire, environ une minute) ?", default=True):
                return
            threads = preset.get("hardware", {}).get("cpu_threads", 4)
            cmd = (f"{shlex.quote(str(tool))} {'--allow-requantize ' if requant else ''}"
                   f"{shlex.quote(src)} {shlex.quote(str(out))} {qtype} {threads}")
            job = ShellJob(cmd)
            try:
                self.agent.follow(job, 7200)
            except KeyboardInterrupt:
                out.unlink(missing_ok=True)
                console.print("[yellow]Conversion annulée.[/yellow]")
                return
            if job.code != 0 or not out.exists():
                out.unlink(missing_ok=True)
                console.print(f"[red]✗ Échec de la conversion (code {job.code}).[/red]")
                for line in job.output().strip().splitlines()[-3:]:
                    console.print(Text("    " + line, style="dim"))
                return
            console.print(f"[bold {ANDROID_GREEN}]✓ Modèle converti en {job.elapsed:.0f}s : "
                          f"{os.path.getsize(out) / 1e9:.2f} Go[/bold {ANDROID_GREEN}]")

        if Confirm.ask(f"  Utiliser ce modèle dans le profil '{name}' ?", default=True):
            self.manager.set_model_path(name, str(out))
            console.print(f"[green]✓ Profil '{escape(name)}' mis à jour.[/green]")
            preset = self.manager.get_active_preset()
            if preset.get("hardware", {}).get("device") != "cpu":
                console.print("[dim]💡 Le Q4_0 donne le meilleur résultat en CPU : /device cpu.[/dim]")
            if self.server.is_ready():
                console.print("[cyan]Rechargement du modèle...[/cyan]")
                self._start_server_live(preset)
                self._last_status_check = 0.0

    def _cmd_jobs(self, arg: str):
        """/jobs [kill <id>|kill all] : commandes lancées en arrière-plan par l'agent."""
        jobs = self.agent.jobs
        words = arg.split()
        if words and words[0].lower() == "kill":
            target = words[1] if len(words) > 1 else ""
            if target.lower() == "all":
                n = self.agent.kill_jobs()
                console.print(f"[cyan]✓ {n} commande(s) arrêtée(s).[/cyan]")
            elif target in jobs:
                if jobs[target].done:
                    console.print(f"[dim]{target} est déjà terminée.[/dim]")
                else:
                    jobs[target].kill()
                    console.print(f"[cyan]✓ {target} arrêtée.[/cyan]")
            else:
                console.print("[yellow]Usage : /jobs kill <id> · /jobs kill all[/yellow]")
            return
        if not jobs:
            console.print("[dim]Aucune commande en arrière-plan. L'agent en lance avec background=true, "
                          "ou quand une commande dépasse son délai.[/dim]")
            return
        for j in jobs.values():
            color = "#00D8D6" if not j.done else ("#3DDC84" if j.code == 0 else "#FF5555")
            console.print(Text(f"  {'◐' if not j.done else '●'} {j.id} ", style=f"bold {color}") +
                          Text(j.cmd.splitlines()[0][:70], style="bold") + Text(f"  {j.status()}", style="dim"))
            for line in j.output().rstrip().splitlines()[-3:]:
                console.print(Text("      " + line[:150], style="dim"))
        console.print("[dim]Usage : /jobs kill <id> · /jobs kill all[/dim]")

    def _cmd_memory(self, arg: str):
        words = arg.split(maxsplit=1)
        if not words:
            notes = memory.list_notes()
            if not notes:
                console.print("[dim]Aucune note mémorisée. En mode agent, le modèle en crée avec memory_save.[/dim]")
            for name, content in notes:
                console.print(Text("• ", style="bold #3DDC84") + Text(name, style="bold") +
                              Text(" : " + " ".join(content.split())[:200]))
            console.print("[dim]Usage : /memory del <nom> · /memory clear[/dim]")
        elif words[0].lower() in ("del", "rm", "remove") and len(words) > 1:
            ok = memory.delete_note(words[1])
            console.print("[cyan]✓ Note supprimée.[/cyan]" if ok else "[yellow]Note introuvable.[/yellow]")
        elif words[0].lower() == "clear":
            n = memory.clear_notes()
            console.print(f"[cyan]✓ {n} note(s) supprimée(s).[/cyan]")
        else:
            console.print("[yellow]Usage : /memory · /memory del <nom> · /memory clear[/yellow]")

    def _echo_input(self, user_input: str):
        """Réaffiche le message validé dans la conversation (l'invite elle-même est effacée)."""
        text = user_input.strip()
        if not text:
            return
        if text.startswith("!"):
            console.print(Text("! ", style="bold #FF79C6") + Text(text[1:].strip(), style="bold"))
        else:
            console.print(Text("> ", style=f"bold {ANDROID_GREEN}") + Text(text))

    def _run_user_shell(self, cmd: str):
        """
        « !commande » : lance une commande ou un script dans le terminal, avec son affichage
        en direct et le clavier (saisies, Ctrl+C). La sortie est jointe au prochain message
        envoyé au modèle, pour qu'il puisse s'en servir (« corrige l'erreur ci-dessus »).
        """
        cmd = cmd.strip()
        if not cmd:
            console.print("[dim]Usage : !<commande>  (ex. !ls, !python script.py, !bash install.sh, !cd dossier)[/dim]")
            return

        # « cd » doit changer le dossier d'IADroid lui-même, pas celui d'un shell éphémère
        m = re.fullmatch(r"cd(?:\s+(.+))?", cmd)
        if m and not re.search(r"[;&|<>`$]", cmd):
            target = os.path.expanduser((m.group(1) or "~").strip().strip("\"'"))
            try:
                os.chdir(target)
                console.print(f"[dim]  ⎿  {escape(short_path(os.getcwd()))}[/dim]")
            except OSError as e:
                console.print(f"[red]  ⎿  cd : {escape(e.strerror or str(e))} : {escape(target)}[/red]")
            return

        shell = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        argv = [shell, "-c", cmd]
        t0 = time.time()
        try:
            if os.name == "posix":
                raw, code = _spawn_in_terminal(argv)
            else:
                raw, code = _spawn_piped([os.environ.get("COMSPEC", "cmd.exe"), "/c", cmd])
        except Exception as e:
            console.print(f"[red]  ⎿  Impossible de lancer la commande : {escape(str(e))}[/red]")
            return
        elapsed = time.time() - t0

        output = _clean_terminal_output(raw)
        # Retour à la ligne si le script n'a pas terminé sa dernière ligne
        if raw and not raw.endswith((b"\n", b"\r")):
            console.print()

        entry = f"$ {cmd}\n{output or '(aucune sortie)'}\n[code de sortie {code}]"
        self._pending_shell.append(entry)
        del self._pending_shell[:-SHELL_PENDING_MAX]
        n_tok = self.server.count_tokens(entry) if self._cached_server_status == "ready" else max(1, len(entry) // 3)

        code_str = f"[green]code 0[/green]" if code == 0 else f"[red]code {code}[/red]"
        if code < 0:
            code_str = f"[yellow]interrompue (signal {-code})[/yellow]"
        console.print(f"[dim]  ⎿  [/dim]{code_str}[dim] · {elapsed:.1f}s · {n_tok} tokens joints "
                      f"au prochain message[/dim]")

    def _confirm_tool(self, question: str):
        """Demande de confirmation d'une action de l'agent. Retourne ("yes"|"always"|"no", raison)."""
        from prompt_toolkit import prompt as pt_prompt
        console.print(Text("  ? ", style="bold #FFD32A") + Text(question, style="bold"))
        console.print("    [bold #3DDC84]o[/] oui   [bold #00D8D6]t[/] toujours (cette conversation)   "
                      "[bold #FF5555]n[/] non", highlight=False)
        while True:
            try:
                ans = pt_prompt("    › ").strip().lower()
            except EOFError:
                return "no", ""
            if ans in ("o", "oui", "y", "yes"):
                return "yes", ""
            if ans in ("t", "toujours", "a", "always"):
                return "always", ""
            if ans in ("n", "non", "no"):
                try:
                    reason = pt_prompt("    Raison ou consigne (facultatif) › ").strip()
                except EOFError:
                    reason = ""
                return "no", reason

    # ------------------------------------------------------------------ génération

    def _system_prompt(self, preset) -> str:
        """Prompt système de la conversation : profil + notes mémorisées + consignes de l'agent."""
        if self._session_system is None:
            parts = [preset.get("context", {}).get("system_prompt", "")]
            mem = memory.memory_prompt_block()
            if mem:
                parts.append(mem)
            if self.agent.enabled:
                parts.append(self.agent.system_block())
            self._session_system = "\n\n".join(p for p in parts if p)
        return self._session_system

    def stream_response(self, user_prompt: str):
        """
        Envoie le message au modèle et diffuse la réponse.
        En mode agent, boucle tant que le modèle appelle des outils :
        génération → exécution des outils (avec confirmation) → résultat renvoyé → génération…
        """
        preset = self.manager.get_active_preset()
        if not preset:
            console.print("[yellow]Aucun profil actif. Créez-en un avec /create-preset ou chargez-en un avec /load <nom>.[/yellow]")
            return

        model_path = preset.get("model_path")
        if not model_path or not os.path.exists(model_path):
            console.print(f"[red]Fichier modèle introuvable : {model_path}[/red]")
            console.print("[yellow]Vérifiez le chemin avec /profil_config.[/yellow]")
            return

        # Si le serveur n'est pas encore prêt, le démarrer automatiquement
        if not self.server.is_ready():
            console.print("[cyan]⚡ Démarrage automatique de llama-server en mémoire...[/cyan]")
            success = self._start_server_live(preset)
            self._last_status_check = 0.0
            if not success:
                return

        # La réflexion est pilotée uniquement par chat_template_kwargs.enable_thinking
        # (server.stream_chat). Ne rien ajouter au prompt système à ce sujet :
        # - citer <think>/</think> : le modèle recopie la balise et llama-server coupe la réflexion dessus ;
        # - demander des titres d'étape : le modèle les écrit dans la RÉPONSE et alterne
        #   réflexion/réponse (testé sur Qwen3.5-9B). Le sommaire (ThinkStepTracker) s'appuie
        #   sur les titres que le modèle produit naturellement (« 1. **Analyze the Request:** »).
        messages = []
        sys_prompt = self._system_prompt(preset)
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.extend(self.conversation_history)

        agent_on = self.agent.enabled
        tools = self.agent.tools() if agent_on else None
        max_steps = int(self.agent.settings["max_steps"]) if agent_on else 1

        # Sorties des commandes « ! » lancées depuis le dernier message : jointes à celui-ci
        # (un message utilisateur séparé casserait l'alternance user/assistant de certains templates)
        if self._pending_shell:
            block = "\n\n".join(self._pending_shell)
            self._pending_shell = []
            user_prompt = (f"Commandes que j'ai exécutées dans mon terminal (dossier "
                           f"{os.getcwd()}) :\n```\n{block}\n```\n\n{user_prompt}")

        notes = self.agent.finished_notes()
        if notes:
            user_prompt = f"{notes}\n\n{user_prompt}"

        turn = [{"role": "user", "content": user_prompt}]
        pending_ctx = int(len(user_prompt.split()) * 1.3)  # tokens ajoutés depuis la dernière mesure

        ctx_total = int(preset.get("context", {}).get("ctx_size", 4096))

        try:
            for step in range(max_steps):
                # Contexte presque plein : raccourcir les anciens résultats d'outils, puis
                # oublier les plus anciens échanges (sinon le serveur coupe la génération)
                if self.context_tokens_used + pending_ctx > ctx_total * 0.75:
                    self._compact_context(turn, ctx_total, pending_ctx)
                    messages = messages[:1] if sys_prompt else []
                    messages.extend(self.conversation_history)
                reply, calls = self._generate_step(messages + turn, preset, tools, pending_ctx)
                if not calls:
                    turn.append({"role": "assistant", "content": reply})
                    break

                tool_calls = [{"id": c["id"] or f"call_{step}_{i}", "type": "function",
                               "function": {"name": c["name"], "arguments": c["arguments"]}}
                              for i, c in enumerate(calls)]
                turn.append({"role": "assistant", "content": reply, "tool_calls": tool_calls})
                pending_ctx = 0
                for c, tc in zip(calls, tool_calls):
                    out = self.agent.execute(c["name"], c["arguments"], c["tokens"], self._confirm_tool)
                    notes = self.agent.finished_notes()
                    if notes:
                        out += "\n\n" + notes
                    turn.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
                    pending_ctx += self.agent.last_result_tokens or len(out) // 3
            else:
                console.print(f"[yellow]⚠ Limite de {max_steps} étapes atteinte : l'agent s'arrête "
                              f"(/agent steps N pour l'augmenter).[/yellow]")
                turn.append({"role": "assistant", "content": "(Limite d'étapes atteinte, tâche interrompue.)"})
            self.conversation_history.extend(turn)
            console.print()

        except KeyboardInterrupt:
            console.print("\n[yellow]Génération interrompue par l'utilisateur (Ctrl+C).[/yellow]\n")
            self._keep_partial_turn(turn)
        except RuntimeError as e:
            console.print(f"\n[red]✗ {escape(str(e))}[/red]")
            console.print("[yellow]Conseil : tapez /stop puis /load pour redémarrer le serveur.[/yellow]\n")
            self._keep_partial_turn(turn)
        except Exception as e:
            console.print(f"\n[red]Erreur lors de la génération : {escape(str(e))}[/red]\n")
            self._keep_partial_turn(turn)
        finally:
            self._live = None
            self._last_status_check = 0.0

    def _compact_context(self, turn: list, ctx_total: int, pending_ctx: int):
        """
        Libère du contexte quand il dépasse 75 % :
          1. raccourcit les résultats d'outils et les contenus de fichiers écrits, sauf ceux de la dernière étape ;
          2. au-delà de 90 %, retire les plus anciens échanges de l'historique (par tour complet).
        Le serveur doit ensuite retraiter le prompt modifié : c'est plus lent une fois, mais évite la coupure.
        """
        # Messages de la dernière étape (après le dernier appel d'outils) : gardés intacts
        last_call = max((i for i, m in enumerate(turn) if m.get("tool_calls")), default=-1)
        older = self.conversation_history + (turn[:last_call] if last_call > 0 else [])
        saved = 0
        for m in older:
            if m.get("role") == "tool" and len(m.get("content") or "") > 400:
                old = m["content"]
                m["content"] = old[:200] + "\n… [résultat raccourci pour libérer le contexte]"
                saved += (len(old) - len(m["content"])) // 3
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments") or ""
                if len(args) > 400:
                    try:
                        data = json.loads(args)
                        for k in ("content", "new_text", "old_text"):
                            if isinstance(data.get(k), str) and len(data[k]) > 150:
                                data[k] = data[k][:100] + "… [omis]"
                        new_args = json.dumps(data, ensure_ascii=False)
                    except Exception:
                        new_args = args[:200]
                    saved += (len(args) - len(new_args)) // 3
                    fn["arguments"] = new_args
        self.context_tokens_used = max(0, self.context_tokens_used - saved)

        dropped = 0
        while self.conversation_history and self.context_tokens_used + pending_ctx > ctx_total * 0.9:
            # Retirer un tour complet : du premier message jusqu'au message utilisateur suivant
            m = self.conversation_history.pop(0)
            self.context_tokens_used -= len(json.dumps(m, ensure_ascii=False)) // 3
            dropped += 1
            while self.conversation_history and self.conversation_history[0].get("role") != "user":
                m = self.conversation_history.pop(0)
                self.context_tokens_used -= len(json.dumps(m, ensure_ascii=False)) // 3
                dropped += 1
        self.context_tokens_used = max(0, self.context_tokens_used)

        if saved or dropped:
            msg = f"♻ Contexte presque plein : {saved} tokens libérés"
            if dropped:
                msg += f", {dropped} anciens messages oubliés"
            console.print(f"[dim]{msg} (/clear pour repartir de zéro).[/dim]")

    def _keep_partial_turn(self, turn: list):
        """
        Après une interruption : si l'agent a déjà agi (outils exécutés), garder ces échanges
        dans l'historique pour que le modèle sache ce qui a été fait. Sinon, oublier le tour.
        """
        if not any(m.get("role") == "tool" for m in turn):
            return
        # Chaque appel d'outil doit avoir sa réponse, sinon le template de chat est incohérent
        answered = {m.get("tool_call_id") for m in turn if m.get("role") == "tool"}
        for m in list(turn):
            for tc in m.get("tool_calls") or []:
                if tc["id"] not in answered:
                    turn.append({"role": "tool", "tool_call_id": tc["id"], "content": "Interrompu par l'utilisateur."})
        turn.append({"role": "assistant", "content": "(Tâche interrompue par l'utilisateur.)"})
        self.conversation_history.extend(turn)

    def _generate_step(self, messages: list, preset: dict, tools, pending_ctx: int):
        """
        Une génération du modèle avec affichage en direct (réflexion, réponse, appels d'outils).
        Retourne (texte_de_réponse, appels_d_outils). Les exceptions remontent à l'appelant.
        """
        rs = preset.get("reasoning", {})
        thinking_enabled = rs.get("enabled", True) and rs.get("effort", "medium") != "off"
        effort = rs.get("effort", "medium") if thinking_enabled else "off"
        show_thinking = rs.get("show_thinking", True)

        # Zone dynamique en bas d'écran : indicateur + réponse en cours + barre d'état en direct
        self._live = {"status": "generating", "t0": time.time(), "tokens": 0, "first_tok": None,
                      "ctx_base": self.context_tokens_used + pending_ctx}
        view = LiveFooterView(self.render_live_footer)
        view.set_header("[dim]💭 Réflexion en cours...[/dim]" if thinking_enabled else "[dim]⚡ Génération en cours...[/dim]")
        view.start()
        start_time = time.time()
        think_start_time = 0
        accumulated_think_text = ""

        PREVIEW_WORDS = 15  # mots de réflexion visibles en direct (/show-think on)
        step_tracker = ThinkStepTracker()
        current_step = None

        def on_think_start(show: bool = True):
            nonlocal think_start_time, accumulated_think_text, current_step, step_tracker
            think_start_time = time.time()
            accumulated_think_text = ""
            step_tracker = ThinkStepTracker()
            current_step = None
            if thinking_enabled:
                view.set_header("[dim]💭 Réflexion en cours...[/dim]")
            else:
                view.set_header("[dim]💭 Le modèle réfléchit malgré /think off (masqué)...[/dim]")

        def on_think_token(tok: str):
            nonlocal accumulated_think_text, current_step
            accumulated_think_text += tok

            # Sommaire : chaque nouvelle étape fait passer la précédente en « terminée »
            for step in step_tracker.feed(tok):
                if current_step:
                    console.print(f"  [dim]✓ {escape(current_step)}[/dim]")
                current_step = step

            header = f"💭 {escape(current_step)}..." if current_step else "💭 Réflexion en cours..."
            if show_thinking:
                words = accumulated_think_text.replace("**", "").replace("#", "").split()
                if words:
                    shown = words[-PREVIEW_WORDS:]
                    prefix = "... " if len(words) > PREVIEW_WORDS else ""
                    preview_str = prefix + " ".join(shown)
                    term_w = shutil.get_terminal_size().columns
                    max_w = max(40, (term_w - 6) * 2)  # au plus ~2 lignes
                    if len(preview_str) > max_w:
                        preview_str = "..." + preview_str[-max_w:]
                    view.set_header(f"[dim]{header}\n  [italic]{escape(preview_str)}[/italic][/dim]")
                    return
            view.set_header(f"[dim]{header}[/dim]")

        def on_think_end(tok_count: int, show: bool = True):
            view.set_header(None)
            if current_step:
                console.print(f"  [dim]✓ {escape(current_step)}[/dim]")
            t_elapsed = max(0.1, time.time() - (think_start_time or start_time))
            n_steps = len(step_tracker.steps)
            steps_str = f" · {n_steps} étape{'s' if n_steps > 1 else ''}" if n_steps else ""
            if thinking_enabled:
                console.print(f"\n[dim]💭 Réfléchi en {t_elapsed:.1f}s{steps_str}[/dim]")
            else:
                console.print(f"\n[dim]💭 Réfléchi en {t_elapsed:.1f}s [yellow](ce modèle ignore /think off)[/yellow][/dim]")
            sys.stdout.flush()

        def on_response_start():
            view.set_header(None)
            view.write("\n")
            view.write("> ", style=f"bold {ANDROID_GREEN}")

        # Texte du modèle affiché via Text() : jamais interprété comme du balisage rich ([...])
        md_renderer = MarkdownStreamRenderer(view.write)

        def on_response_token(tok: str):
            md_renderer.feed(tok)

        parser = ThinkStreamParser(
            on_think_start=on_think_start,
            on_think_token=on_think_token,
            on_think_end=on_think_end,
            on_response_start=on_response_start,
            on_response_token=on_response_token,
            show_thinking=show_thinking,
            thinking_enabled=thinking_enabled
        )

        def count_token():
            live = self._live
            if live is not None:
                if live["first_tok"] is None:
                    live["first_tok"] = time.time()
                live["tokens"] += 1

        def on_raw_token(token: str):
            if token not in ("<think>", "</think>"):
                count_token()
            parser.feed(token)

        arg_rx = re.compile(r'"(?:path|command|url|query|name|id)"\s*:\s*"((?:[^"\\]|\\.)*)')

        def on_tool_progress(idx: int, name: str, args: str, tokens: int):
            # Appel d'outil en cours d'écriture par le modèle : ● Write(chemin…) · 23 tokens
            count_token()
            label = TOOL_LABELS.get(name, name or "…")
            m = arg_rx.search(args)
            target = m.group(1).replace("\\n", " ")[:50] if m else "…"
            view.set_header(f"[bold #3DDC84]●[/] [bold]{escape(label)}({escape(target)})[/bold] [dim]· {tokens} tokens[/dim]")

        try:
            full_reply, elapsed, gen_tokens = self.server.stream_chat(
                messages=messages,
                sampling=preset.get("sampling", {}),
                on_token=on_raw_token,
                reasoning_effort=effort,
                thinking_enabled=thinking_enabled,
                tools=tools,
                on_tool_progress=on_tool_progress if tools else None
            )
            parser.finish()
            md_renderer.finish()

            clean_reply = "".join(parser.full_response_text).strip()
            if not clean_reply and full_reply:
                clean_reply = full_reply.strip()
            calls = list(getattr(self.server, "last_tool_calls", []) or [])

            # Contexte : valeur exacte renvoyée par le serveur si disponible, sinon estimation
            usage = getattr(self.server, "last_usage", None) or {}
            tokens_count = max(1, gen_tokens if gen_tokens > 0 else int(len(clean_reply.split()) * 1.3))
            if usage.get("prompt_tokens"):
                self.context_tokens_used = int(usage["prompt_tokens"]) + int(usage.get("completion_tokens") or tokens_count)
            else:
                self.context_tokens_used += pending_ctx + tokens_count

            # Vitesse de génération mesurée depuis le premier token (hors traitement du prompt)
            first_tok = self._live.get("first_tok") if self._live else None
            gen_time = (time.time() - first_tok) if first_tok else elapsed
            if gen_time > 0.05:
                self.last_speed = str(round(tokens_count / gen_time, 1))

            view.stop()
            if getattr(self.server, "last_finish_reason", None) == "length":
                console.print("[yellow]⚠ Réponse coupée : contexte plein ou limite de tokens atteinte (/clear conseillé).[/yellow]")
                calls = []  # un appel d'outil coupé a des arguments incomplets : ne pas l'exécuter
            if clean_reply and calls:
                console.print()
            return clean_reply, calls
        finally:
            view.stop()

    def run_raw_llama_cli(self):
        """Lance llama-cli en mode brut interactif plein écran (expert/debug)."""
        preset = self.manager.get_active_preset()
        if not preset:
            console.print("[yellow]Aucun profil actif. Utilisez /create-preset pour en créer un.[/yellow]")
            return

        if self.server.is_ready():
            console.print("[yellow]Arrêt du serveur local pour lancer la session brute...[/yellow]")
            self.server.stop()

        try:
            cmd = self.manager.build_llama_cli_command(preset, interactive=True)
            console.print(f"\n[bold {ANDROID_GREEN}]Lancement brut de llama-cli...[/bold {ANDROID_GREEN}]\n")
            subprocess.run(cmd)
        except Exception as e:
            console.print(f"[red]Erreur lors de l'exécution brute : {e}[/red]")

    def start(self):
        """Boucle principale du CLI."""
        os.system("clear" if os.name != "nt" else "cls")

        # Vérification en arrière-plan d'une mise à jour GitHub (rapide / mise en cache)
        from .updater import UpdateChecker
        has_update = UpdateChecker.cached_check_has_update()
        display_welcome_banner(self.manager.get_active_preset(), has_update=has_update)

        # Calcul initial des tokens
        active_preset = self.manager.get_active_preset()
        if active_preset:
            sys_p = active_preset.get("context", {}).get("system_prompt", "")
            self.context_tokens_used = int(len(sys_p.split()) * 1.3)

        monitor = threading.Thread(target=self._status_monitor_loop, daemon=True)
        monitor.start()

        try:
            while True:
                try:
                    # Valeurs fraîches à chaque nouvelle invite
                    self._ram_snapshot = get_ram_info()
                    self._last_status_check = 0.0

                    # Pas de refresh_interval : le redessin périodique empêchait de
                    # remonter dans l'historique (Termux redescend à chaque rendu).
                    # erase_when_done : la barre d'effort, le séparateur et l'invite sont effacés
                    # à la validation, sinon chaque Entrée les laisse empilés dans la conversation.
                    # Le message est ensuite réaffiché seul (_echo_input).
                    user_input = self.session.prompt(
                        self.get_prompt_message,
                        style=self.style,
                        bottom_toolbar=self.get_bottom_toolbar
                    )
                    self._echo_input(user_input)
                    should_continue = self.handle_command(user_input)
                    if not should_continue:
                        break
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    console.print("\n[yellow]Au revoir ![/yellow]")
                    break
                except Exception as e:
                    # Enregistrement automatique du crash dans les Téléchargements et affichage propre
                    preset = self.manager.get_active_preset() or {}
                    ctx = {
                        "preset": preset.get("name", "Inconnu"),
                        "device": preset.get("hardware", {}).get("device", "Inconnu"),
                        "server_status": self.server.get_status()
                    }
                    handle_crash(e, extra_context=ctx)
        finally:
            self._monitor_stop.set()
            self.agent.kill_jobs()
            self.server.stop()
