"""
IADroid - Interface en Ligne de Commande Interactive (REPL)
Style Claude Code & Antigravity CLI (agy) avec barre de commandes et autocomplétion
"""

import os
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
    get_gpu_type
)
from .banner import display_welcome_banner, print_help_table
from .preset_manager import PresetManager, is_llama_running, stop_llama_processes
from .wizard import PresetWizard
from .builder import LlamaBuilder
from .server import LlamaServer
from .crash_handler import handle_crash

console = Console()

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

        # Complétion pour /fa
        if words[0] in ("/fa", "/flash-attention"):
            sub_prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for opt in ("on", "off"):
                if opt.startswith(sub_prefix):
                    yield Completion(opt, start_position=-len(sub_prefix))
            return


class IADroidCLI:
    def __init__(self):
        ensure_directories()
        self.manager = PresetManager()
        self.wizard = PresetWizard(self.manager)
        self.builder = LlamaBuilder()
        self.server = LlamaServer()
        self.conversation_history = []  # Liste de tuples (role, content)
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

        # Historique persistant des commandes
        # reserve_space_for_menu=0 : pas de lignes vides réservées pour l'autocomplétion,
        # la zone de saisie ne fait que la hauteur de son contenu et grandit si le texte déborde.
        history_file = str(IADROID_HOME / "history.txt")
        self.session = PromptSession(
            history=FileHistory(history_file),
            auto_suggest=AutoSuggestFromHistory(),
            completer=IADroidCompleter(self.manager),
            reserve_space_for_menu=0
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
        pad = max(0, width - len(tag))

        return HTML(
            f'<style fg="#555555">{" " * pad}</style>'
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
        effort_line = f"{' ' * max(0, width - len(tag))}[bold {effort_color}]● {escape(effort)}[/][#777777] · /effort[/]"

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

        if width < 75:
            lines = [f"{metrics} [#555555]|[/] {badge}", f" {ram}", info]
        else:
            lines = [f"{metrics} [#555555]|[/] {ram} [#555555]|[/] {badge}", info]

        return Text.from_markup("\n".join([effort_line, sep, f"[bold #3DDC84]>[/] {prompt_hint}", sep] + lines))

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
        active_preset = self.manager.get_active_preset()
        sys_p = active_preset.get("context", {}).get("system_prompt", "") if active_preset else ""
        self.context_tokens_used = int(len(sys_p.split()) * 1.3)
        self.last_speed = "--"

    def handle_command(self, user_input: str) -> bool:
        """Exécute une commande utilisateur. Retourne False pour quitter."""
        raw = user_input.strip()
        if not raw:
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
            self.conversation_history.clear()
            preset = self.manager.get_active_preset()
            sys_p = preset.get("context", {}).get("system_prompt", "") if preset else ""
            self.context_tokens_used = int(len(sys_p.split()) * 1.3)
            self.last_speed = "--"

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
                    self.conversation_history.clear()
                    active_preset = self.manager.get_active_preset()
                    sys_p = active_preset.get("context", {}).get("system_prompt", "") if active_preset else ""
                    self.context_tokens_used = int(len(sys_p.split()) * 1.3)
                    self.last_speed = "--"
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

        # Commande non reconnue
        elif cmd.startswith("/"):
            console.print(f"[red]Commande inconnue : {cmd}. Tapez /help pour la liste.[/red]")
            return True

        else:
            # Traitement direct du prompt utilisateur avec streaming en direct dans l'interface IADroid
            self.stream_response(user_input)
            return True

    def stream_response(self, user_prompt: str):
        """Exécute et diffuse la réponse du modèle en streaming token par token via l'API locale."""
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

        ctx = preset.get("context", {})
        sys_prompt = ctx.get("system_prompt", "")
        hw = preset.get("hardware", {})
        device_label = hw.get("device", "gpu").upper()

        # Raisonnement & Thinking
        rs = preset.get("reasoning", {})
        thinking_enabled = rs.get("enabled", True) and rs.get("effort", "medium") != "off"
        effort = rs.get("effort", "medium") if thinking_enabled else "off"
        show_thinking = rs.get("show_thinking", True)

        # Construction des messages pour l'API /v1/chat/completions (OpenAI compatible)
        messages = []
        full_sys_prompt = sys_prompt

        # La réflexion est pilotée uniquement par chat_template_kwargs.enable_thinking
        # (server.stream_chat). Ne rien ajouter au prompt système à ce sujet :
        # - citer <think>/</think> : le modèle recopie la balise et llama-server coupe la réflexion dessus ;
        # - demander des titres d'étape : le modèle les écrit dans la RÉPONSE et alterne
        #   réflexion/réponse (testé sur Qwen3.5-9B). Le sommaire (ThinkStepTracker) s'appuie
        #   sur les titres que le modèle produit naturellement (« 1. **Analyze the Request:** »).

        if full_sys_prompt:
            messages.append({"role": "system", "content": full_sys_prompt})

        for role, content in self.conversation_history:
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_prompt})

        # Zone dynamique en bas d'écran : indicateur + réponse en cours + barre d'état en direct
        user_tokens = int(len(user_prompt.split()) * 1.3)
        self._live = {"status": "generating", "t0": time.time(), "tokens": 0, "first_tok": None,
                      "ctx_base": self.context_tokens_used + user_tokens}
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

        def on_raw_token(token: str):
            live = self._live
            if live is not None and token not in ("<think>", "</think>"):
                if live["first_tok"] is None:
                    live["first_tok"] = time.time()
                live["tokens"] += 1
            parser.feed(token)

        try:
            full_reply, elapsed, gen_tokens = self.server.stream_chat(
                messages=messages,
                sampling=preset.get("sampling", {}),
                on_token=on_raw_token,
                reasoning_effort=effort,
                thinking_enabled=thinking_enabled
            )
            parser.finish()
            md_renderer.finish()

            clean_reply = "".join(parser.full_response_text).strip()
            if not clean_reply and full_reply:
                clean_reply = full_reply.strip()

            self.conversation_history.append(("user", user_prompt))
            self.conversation_history.append(("assistant", clean_reply))

            tokens_count = max(1, gen_tokens if gen_tokens > 0 else int(len(clean_reply.split()) * 1.3))
            self.context_tokens_used += user_tokens + tokens_count

            # Vitesse de génération mesurée depuis le premier token (hors traitement du prompt)
            first_tok = self._live.get("first_tok") if self._live else None
            gen_time = (time.time() - first_tok) if first_tok else elapsed
            if gen_time > 0.05:
                self.last_speed = str(round(tokens_count / gen_time, 1))

            view.stop()
            console.print()

        except KeyboardInterrupt:
            view.stop()
            console.print("\n[yellow]Génération interrompue par l'utilisateur (Ctrl+C).[/yellow]\n")
        except RuntimeError as e:
            view.stop()
            console.print(f"\n[red]✗ {escape(str(e))}[/red]")
            console.print("[yellow]Conseil : tapez /stop puis /load pour redémarrer le serveur.[/yellow]\n")
        except Exception as e:
            view.stop()
            console.print(f"\n[red]Erreur lors de la génération : {escape(str(e))}[/red]\n")
        finally:
            view.stop()
            self._live = None
            self._last_status_check = 0.0

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
                    # remonter dans l'historique (Termux redescend à chaque rendu)
                    user_input = self.session.prompt(
                        self.get_prompt_message,
                        style=self.style,
                        bottom_toolbar=self.get_bottom_toolbar
                    )
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
            self.server.stop()
