"""
IADroid - Interface en Ligne de Commande Interactive (REPL)
Style Claude Code & Antigravity CLI (agy) avec barre de commandes et autocomplétion
"""

import os
import sys
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

        # Style de l'invite de commande (exactement comme le prompt minimaliste de Claude Code)
        self.style = Style.from_dict({
            'prompt': '#3DDC84 bold',
            'bottom-toolbar': 'bg:#1A1A1A #CCCCCC',
            'bottom-toolbar.on': '#3DDC84 bold',
            'bottom-toolbar.off': '#FFD32A bold',
        })

    def get_bottom_toolbar(self):
        """Barre d'état inférieure sous la zone de saisie avec jauge de contexte et t/s."""
        active_preset = self.manager.get_active_preset()
        if not active_preset:
            return HTML(' <i>Aucun profil actif. Tapez /create-preset pour commencer.</i>')

        p_name = active_preset.get("name", "Inconnu")
        hw = active_preset.get("hardware", {})
        dev = hw.get("device", "gpu").upper()
        ctx_total = active_preset.get("context", {}).get("ctx_size", 4096)
        
        # Calcul de la jauge de progression du contexte
        used = self.context_tokens_used
        ratio = min(1.0, max(0.0, used / max(1, ctx_total)))
        pct = ratio * 100
        
        bar_width = 10
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

        dev_tag = "on" if dev == "GPU" else "off"
        speed_str = f"{self.last_speed} t/s" if self.last_speed != "--" else "-- t/s"

        return HTML(
            f' {status_badge} | '
            f'<style fg="#3DDC84"><b>[{filled_bar}</b></style>'
            f'<style fg="#555555"><b>{empty_bar}]</b></style> '
            f'<b>{used}/{ctx_total}</b> ({pct:.1f}%) | '
            f'⚡ <b>{speed_str}</b> | '
            f'<{dev_tag}>{dev}</{dev_tag}> | '
            f'Profil: <b>{p_name}</b>'
        )

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

        # Construction des messages pour l'API /v1/chat/completions (OpenAI compatible)
        messages = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})

        for role, content in self.conversation_history:
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_prompt})

        console.print(f"\n[bold {ANDROID_GREEN}]>[/bold {ANDROID_GREEN}] ", end="")
        sys.stdout.flush()

        buffer = []
        in_think_block = False

        def on_token(token: str):
            nonlocal in_think_block
            buffer.append(token)
            curr = "".join(buffer)

            # Détection des balises de réflexion <think> ... </think>
            if "<think>" in curr and not in_think_block:
                in_think_block = True
                console.print(f"\n[dim italic]💭 Réflexion :[/dim italic] ", end="")
                sys.stdout.flush()
                return

            if in_think_block and "</think>" in curr:
                in_think_block = False
                console.print(f"\n\n[bold {ANDROID_GREEN}]>[/bold {ANDROID_GREEN}] ", end="")
                sys.stdout.flush()
                return

            if in_think_block:
                console.print(f"[dim italic]{token}[/dim italic]", end="")
            else:
                console.print(token, end="")
            sys.stdout.flush()

        try:
            full_reply, elapsed, gen_tokens = self.server.stream_chat(
                messages=messages,
                sampling=preset.get("sampling", {}),
                on_token=on_token
            )

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
        display_welcome_banner(self.manager.get_active_preset())

        # Calcul initial des tokens
        active_preset = self.manager.get_active_preset()
        if active_preset:
            sys_p = active_preset.get("context", {}).get("system_prompt", "")
            self.context_tokens_used = int(len(sys_p.split()) * 1.3)

        try:
            while True:
                try:
                    term_width = shutil.get_terminal_size().columns
                    console.print(f"[dim]{'─' * min(term_width, 80)}[/dim]")

                    user_input = self.session.prompt(
                        [('class:prompt', '> ')],
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
