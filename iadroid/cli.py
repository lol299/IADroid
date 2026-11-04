"""
IADroid - Interface en Ligne de Commande Interactive (REPL)
Style Claude Code & Antigravity CLI (agy) avec barre de commandes et autocomplétion
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

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
    ensure_directories
)
from .banner import display_welcome_banner, print_help_table
from .preset_manager import PresetManager, is_llama_running, stop_llama_processes
from .wizard import PresetWizard
from .builder import LlamaBuilder
from .server import LlamaServer

console = Console()

class ThinkStreamParser:
    """Analyse les tokens en temps réel pour détecter et séparer les blocs <think> ... </think>."""
    def __init__(self, on_think_start, on_think_token, on_think_end, on_response_start, on_response_token):
        self.on_think_start = on_think_start
        self.on_think_token = on_think_token
        self.on_think_end = on_think_end
        self.on_response_start = on_response_start
        self.on_response_token = on_response_token

        self.state = "INIT"  # INIT, THINKING, RESPONDING
        self.buffer = ""
        self.think_tokens_count = 0
        self.response_tokens_count = 0
        self.full_think_text = []
        self.full_response_text = []

    def feed(self, token: str):
        if self.state == "INIT":
            self.buffer += token
            stripped = self.buffer.lstrip()
            if "<think>" in stripped:
                idx = self.buffer.find("<think>")
                before = self.buffer[:idx]
                if before.strip():
                    self.on_response_start()
                    self.on_response_token(before)
                    self.full_response_text.append(before)
                self.state = "THINKING"
                self.on_think_start()
                after = self.buffer[idx + 7:]
                self.buffer = ""
                if after:
                    self.feed(after)
                return
            elif len(stripped) >= 8 and not "<think>".startswith(stripped[:min(len(stripped), 7)]):
                # Ce n'est pas un bloc <think>, on commence la réponse
                self.state = "RESPONDING"
                self.on_response_start()
                self.on_response_token(self.buffer)
                self.full_response_text.append(self.buffer)
                self.response_tokens_count += 1
                self.buffer = ""
                return

        elif self.state == "THINKING":
            self.buffer += token
            if "</think>" in self.buffer:
                idx = self.buffer.find("</think>")
                think_chunk = self.buffer[:idx]
                if think_chunk:
                    self.on_think_token(think_chunk)
                    self.full_think_text.append(think_chunk)
                    self.think_tokens_count += 1
                self.on_think_end(self.think_tokens_count)
                self.state = "RESPONDING"
                self.on_response_start()
                after = self.buffer[idx + 8:].lstrip("\r\n")
                self.buffer = ""
                if after:
                    self.on_response_token(after)
                    self.full_response_text.append(after)
                    self.response_tokens_count += 1
                return
            else:
                if len(self.buffer) > 8:
                    emit_len = len(self.buffer) - 8
                    emit_part = self.buffer[:emit_len]
                    self.buffer = self.buffer[emit_len:]
                    self.on_think_token(emit_part)
                    self.full_think_text.append(emit_part)
                    self.think_tokens_count += 1

        elif self.state == "RESPONDING":
            self.on_response_token(token)
            self.full_response_text.append(token)
            self.response_tokens_count += 1

    def finish(self):
        if self.state == "INIT" and self.buffer:
            self.on_response_start()
            self.on_response_token(self.buffer)
            self.full_response_text.append(self.buffer)
            self.response_tokens_count += 1
            self.buffer = ""
        elif self.state == "THINKING" and self.buffer:
            self.on_think_token(self.buffer)
            self.full_think_text.append(self.buffer)
            self.on_think_end(self.think_tokens_count)
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
        
        # Historique persistant des commandes
        history_file = str(IADROID_HOME / "history.txt")
        self.session = PromptSession(
            history=FileHistory(history_file),
            auto_suggest=AutoSuggestFromHistory(),
            completer=IADroidCompleter(self.manager)
        )

        # Ancrer la zone de saisie tout en bas de l'écran (style Claude Code)
        # pour que le prompt > et la barre inférieure soient collés ensemble au bas du terminal
        try:
            from prompt_toolkit.layout.containers import VerticalAlign
            from prompt_toolkit.filters import to_filter
            root = self.session.layout.container
            if hasattr(root, 'align'):
                root.align = VerticalAlign.BOTTOM
            cw = self.session.layout.current_window
            if cw:
                cw.dont_extend_height = to_filter(True)
        except Exception:
            pass

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
        - Ligne 2 : ⚡ vitesse | 🧠 tokens contexte (%) [jauge] | ● statut VRAM
        - Ligne 3 : ❯❯ Profil actif · Matériel · /help pour commandes
        Aucun fond blanc (arborescence sombre transparente et couleurs vives).
        """
        term_width = shutil.get_terminal_size().columns
        width = max(10, min(term_width - 1, 100))
        sep_line = "─" * width

        active_preset = self.manager.get_active_preset()
        if not active_preset:
            return HTML(
                f'<style fg="#555555">{sep_line}</style>\n'
                f'<style fg="#FFD32A"> <i>Aucun profil actif. Tapez /create-preset pour commencer.</i></style>'
            )

        p_name = active_preset.get("name", "Inconnu")
        hw = active_preset.get("hardware", {})
        dev = hw.get("device", "gpu").upper()
        ctx_total = active_preset.get("context", {}).get("ctx_size", 4096)

        # Calcul de la jauge de progression du contexte
        used = self.context_tokens_used
        ratio = min(1.0, max(0.0, used / max(1, ctx_total)))
        pct = ratio * 100

        bar_width = 8
        filled_count = int(ratio * bar_width)
        empty_count = bar_width - filled_count

        filled_bar = "█" * filled_count
        empty_bar = "░" * empty_count

        # Statut réel en mémoire via llama-server
        server_status = self.server.get_status()
        if server_status == "ready":
            status_badge = '<style fg="#3DDC84"><b>● Prêt (VRAM)</b></style>'
        elif server_status == "loading":
            status_badge = '<style fg="#FFD32A"><b>⏳ Chargement...</b></style>'
        else:
            status_badge = '<style fg="#777777"><b>○ Arrêté (/run)</b></style>'

        speed_str = f"{self.last_speed} t/s" if self.last_speed != "--" else "-- t/s"
        dev_color = "#3DDC84" if dev == "GPU" else "#FFD32A"

        # Ligne 1 : Bordure horizontale inférieure
        line_border = f'<style fg="#555555">{sep_line}</style>'

        if width < 60:
            # Affichage compact pour petits écrans de smartphones Android
            line_metrics = (
                f' <style fg="#FFA500">⚡</style> <style fg="#00D8D6"><b>{speed_str}</b></style> '
                f'<style fg="#555555">|</style> '
                f'<style fg="#FF79C6">🧠</style> <style fg="#FFFFFF"><b>{used}/{ctx_total}</b></style> '
                f'<style fg="#3DDC84">({pct:.0f}%)</style> '
                f'<style fg="#555555">|</style> '
                f'{status_badge}'
            )
            line_info = (
                f' <style fg="#FFD32A"><b>❯❯</b></style> '
                f'<style fg="#00D8D6"><b>{p_name}</b></style> '
                f'<style fg="#555555">·</style> '
                f'<style fg="{dev_color}"><b>{dev}</b></style> '
                f'<style fg="#555555">·</style> '
                f'<style fg="#888888">Tapez </style><style fg="#3DDC84"><b>/help</b></style>'
            )
        else:
            # Affichage complet style Claude Code
            line_metrics = (
                f' <style fg="#FFA500">⚡</style> <style fg="#00D8D6"><b>{speed_str}</b></style> '
                f'<style fg="#555555">|</style> '
                f'<style fg="#FF79C6">🧠</style> <style fg="#CCCCCC">Ctx: </style>'
                f'<style fg="#FFFFFF"><b>{used}/{ctx_total}</b></style> '
                f'<style fg="#3DDC84"><b>({pct:.1f}%)</b></style> '
                f'<style fg="#3DDC84">{filled_bar}</style><style fg="#444444">{empty_bar}</style> '
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
                f'<style fg="#888888"> pour l\'aide · </style><style fg="#FFD32A"><b>/stop</b></style>'
                f'<style fg="#888888"> pour libérer la VRAM</style>'
            )

        return HTML(f"{line_border}\n{line_metrics}\n{line_info}")

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
            os.system("clear" if os.name != "nt" else "cls")
            display_welcome_banner(self.manager.get_active_preset())
            return True

        # /create-preset
        elif cmd == "/create-preset":
            self.wizard.run()
            return True

        # /profil_config <nom>
        elif cmd == "/profil_config":
            target = arg or self.manager.get_active_preset_name()
            if not target:
                console.print("[yellow]Veuillez spécifier le profil à éditer : /profil_config <nom>[/yellow]")
            else:
                self.wizard.run(edit_name=target)
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

        # /device <gpu|cpu>
        elif cmd == "/device":
            active_name = self.manager.get_active_preset_name()
            if not active_name:
                console.print("[yellow]Aucun profil actif.[/yellow]")
            elif not arg or arg.lower() not in ("gpu", "cpu"):
                console.print("[yellow]Usage : /device <gpu|cpu>[/yellow]")
            else:
                self.manager.set_device_mode(active_name, arg.lower())
                if self.server.is_ready():
                    console.print(f"[cyan]Redémarrage du modèle en mode {arg.upper()}...[/cyan]")
                    self.server.start(self.manager.get_active_preset())
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
            success = self.server.start(preset)
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
            success = self.server.start(preset)
            if success:
                console.print(f"[bold {ANDROID_GREEN}]✓ Modèle '{p_name}' chargé et prêt en mémoire ![/bold {ANDROID_GREEN}]")
                console.print("[dim]L'interface IADroid reste active. Tapez directement votre message au prompt > ci-dessous.[/dim]\n")
            return True

        # /stop : Arrêter le serveur actif et libérer la mémoire RAM et VRAM GPU
        elif cmd in ("/stop", "/kill"):
            self.server.stop()
            console.print(f"[bold {ANDROID_GREEN}]✓ Serveur arrêté. Mémoire RAM et VRAM GPU libérées avec succès ![/bold {ANDROID_GREEN}]")
            return True

        # /reset : Réinitialiser la conversation
        elif cmd == "/reset":
            self.conversation_history.clear()
            active_preset = self.manager.get_active_preset()
            sys_p = active_preset.get("context", {}).get("system_prompt", "") if active_preset else ""
            self.context_tokens_used = int(len(sys_p.split()) * 1.3)
            self.last_speed = "--"
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
            success = self.server.start(preset)
            if not success:
                return

        ctx = preset.get("context", {})
        sys_prompt = ctx.get("system_prompt", "")
        hw = preset.get("hardware", {})
        device_label = hw.get("device", "gpu").upper()

        # Raisonnement & Thinking
        rs = preset.get("reasoning", {})
        effort = rs.get("effort", "medium") if rs.get("enabled", True) else "off"

        # Construction des messages pour l'API /v1/chat/completions (OpenAI compatible)
        messages = []
        full_sys_prompt = sys_prompt

        # Si le mode réflexion est activé, guider le modèle vers l'utilisation des balises <think>
        if effort != "off" and "<think>" not in sys_prompt:
            budget = rs.get("budget_tokens", 1024)
            thinking_rule = (
                f"\n[Mode Réflexion Activé - Effort: {effort}]\n"
                f"Avant de donner ta réponse finale, analyse méthodiquement la question de l'utilisateur. "
                f"Inscris impérativement l'intégralité de cette réflexion et de tes étapes de raisonnement "
                f"entre les balises <think> et </think>. "
                f"Après la balise fermante </think>, fournis ta réponse claire et directe."
            )
            full_sys_prompt = (sys_prompt + "\n" + thinking_rule).strip() if sys_prompt else thinking_rule.strip()

        if full_sys_prompt:
            messages.append({"role": "system", "content": full_sys_prompt})

        for role, content in self.conversation_history:
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_prompt})

        # Indicateur de réflexion dynamique
        spinner = console.status("[dim]💭 Réflexion en cours...[/dim]", spinner="dots")
        spinner.start()
        start_time = time.time()
        first_token_received = False
        think_start_time = 0

        def on_think_start():
            nonlocal think_start_time
            think_start_time = time.time()
            console.print(f"\n[dim]┌─ 💭 [italic]Réflexion[/italic][/dim]")
            sys.stdout.flush()

        def on_think_token(tok: str):
            console.print(f"[dim italic]{tok}[/dim italic]", end="")
            sys.stdout.flush()

        def on_think_end(tok_count: int):
            t_elapsed = max(0.1, time.time() - (think_start_time or start_time))
            console.print(f"\n[dim]└─ ✓ Réflexion terminée ({t_elapsed:.1f}s, {tok_count} tokens)[/dim]\n")
            sys.stdout.flush()

        def on_response_start():
            # Si aucune réflexion via <think> n'a été émise, afficher le temps de calcul / réflexion initial
            if not parser.full_think_text:
                prefill_sec = max(0.1, time.time() - start_time)
                console.print(f"\n[dim]💭 Réfléchi en {prefill_sec:.1f}s[/dim]")
            console.print(f"\n[bold {ANDROID_GREEN}]>[/bold {ANDROID_GREEN}] ", end="")
            sys.stdout.flush()

        def on_response_token(tok: str):
            console.print(tok, end="")
            sys.stdout.flush()

        parser = ThinkStreamParser(
            on_think_start=on_think_start,
            on_think_token=on_think_token,
            on_think_end=on_think_end,
            on_response_start=on_response_start,
            on_response_token=on_response_token
        )

        def on_raw_token(token: str):
            nonlocal first_token_received
            if not first_token_received:
                first_token_received = True
                spinner.stop()
            parser.feed(token)

        try:
            full_reply, elapsed, gen_tokens = self.server.stream_chat(
                messages=messages,
                sampling=preset.get("sampling", {}),
                on_token=on_raw_token
            )
            parser.finish()

            clean_reply = "".join(parser.full_response_text).strip()
            if not clean_reply and full_reply:
                clean_reply = full_reply.strip()

            self.conversation_history.append(("user", user_prompt))
            self.conversation_history.append(("assistant", clean_reply))

            user_tokens = int(len(user_prompt.split()) * 1.3)
            tokens_count = max(1, gen_tokens if gen_tokens > 0 else int(len(clean_reply.split()) * 1.3))
            self.context_tokens_used += user_tokens + tokens_count

            if elapsed > 0.05:
                self.last_speed = str(round(tokens_count / elapsed, 1))

            console.print(f"\n\n[dim]⚡ {self.last_speed} t/s | Contexte: {self.context_tokens_used} tokens | Accel: {device_label}[/dim]\n")

        except KeyboardInterrupt:
            console.print("\n[yellow]Génération interrompue par l'utilisateur (Ctrl+C).[/yellow]\n")
        except Exception as e:
            console.print(f"\n[red]Erreur lors de la génération : {e}[/red]\n")
        finally:
            spinner.stop()

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

        try:
            while True:
                try:
                    # Invite de commande style Claude Code avec bordures haut/bas et barre d'état colorée
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
        finally:
            self.server.stop()
