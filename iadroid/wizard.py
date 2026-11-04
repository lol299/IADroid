"""
IADroid - Assistant Interactif de Configuration (Wizard)
Gère /create-preset, /profil_config, et /profil_Prompt_Systeme
"""

import os
from typing import Optional, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt, FloatPrompt

from .config import (
    ANDROID_GREEN,
    CYAN,
    YELLOW,
    REASONING_EFFORT_LEVELS,
    REASONING_TOKEN_BUDGETS,
    get_default_preset,
    MODELS_DIR
)
from .preset_manager import PresetManager

console = Console()

class PresetWizard:
    def __init__(self, manager: PresetManager):
        self.manager = manager

    def _ask_str(self, label: str, default: str, help_text: str = "") -> str:
        if help_text:
            console.print(f"  [dim]{help_text}[/dim]")
        return Prompt.ask(f"  [cyan]{label}[/cyan]", default=default)

    def _ask_int(self, label: str, default: int, help_text: str = "") -> int:
        if help_text:
            console.print(f"  [dim]{help_text}[/dim]")
        return IntPrompt.ask(f"  [cyan]{label}[/cyan]", default=default)

    def _ask_float(self, label: str, default: float, help_text: str = "") -> float:
        if help_text:
            console.print(f"  [dim]{help_text}[/dim]")
        return FloatPrompt.ask(f"  [cyan]{label}[/cyan]", default=default)

    def _ask_bool(self, label: str, default: bool, help_text: str = "") -> bool:
        if help_text:
            console.print(f"  [dim]{help_text}[/dim]")
        return Confirm.ask(f"  [cyan]{label}[/cyan]", default=default)

    def run(self, preset_name: Optional[str] = None, edit_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Alias universel pour lancer l'assistant (supporte run(), run(name), run(edit_name=name))."""
        target = preset_name or edit_name
        return self.run_wizard(target)

    def run_wizard(self, preset_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Lance l'assistant complet.
        Si preset_name est fourni et existe -> Mode Modification (/profil_config)
        Sinon -> Mode Création (/create-preset)
        """
        is_edit = False
        current_data = None

        if preset_name and self.manager.preset_exists(preset_name):
            is_edit = True
            current_data = self.manager.load_preset(preset_name)
            title = f"⚙️ Modification du Profil : [bold yellow]{preset_name}[/bold yellow]"
        else:
            title = "✨ Création d'un Nouveau Profil IADroid"
            current_data = get_default_preset(preset_name or "mon-modele")

        console.print(Panel(title, border_style=ANDROID_GREEN, expand=False))

        try:
            # 1. Identité du profil
            console.print(f"\n[bold {ANDROID_GREEN}]1. Identité du profil[/bold {ANDROID_GREEN}]")
            if not is_edit:
                while True:
                    name = self._ask_str("Nom unique du profil", current_data.get("name", "nouveau-profil")).strip()
                    if not name:
                        console.print("  [red]Le nom ne peut pas être vide.[/red]")
                        continue
                    if self.manager.preset_exists(name):
                        console.print(f"  [yellow]Le profil '{name}' existe déjà. Choisissez un autre nom.[/yellow]")
                        continue
                    current_data["name"] = name
                    break
            else:
                console.print(f"  Nom du profil : [bold cyan]{current_data['name']}[/bold cyan] (fixe)")

            current_data["description"] = self._ask_str(
                "Description",
                current_data.get("description", "Profil local pour Android")
            )

            # 2. Fichiers et Modèle
            console.print(f"\n[bold {ANDROID_GREEN}]2. Chemins des Fichiers Modèles[/bold {ANDROID_GREEN}]")
            current_data["model_path"] = self._ask_str(
                "Chemin du modèle GGUF (-m)",
                current_data.get("model_path", str(MODELS_DIR / "model.gguf")),
                help_text="Exemple : /data/data/com.termux/files/home/models/qwen.gguf ou /sdcard/Download/model.gguf"
            )

            current_data["mmproj_path"] = self._ask_str(
                "Projecteur de vision multimodal (--mmproj, optionnel)",
                current_data.get("mmproj_path", ""),
                help_text="Laissez vide si le modèle est texte-seul (ou indiquez le fichier mmproj-*.gguf)"
            )

            # 3. Matériel & RAM Unifiée
            console.print(f"\n[bold {ANDROID_GREEN}]3. Matériel, Calcul & Gestion RAM Unifiée[/bold {ANDROID_GREEN}]")
            hw = current_data.setdefault("hardware", {})

            device = Prompt.ask(
                "  [cyan]Périphérique d'accélération[/cyan]",
                choices=["gpu", "cpu"],
                default=hw.get("device", "gpu")
            )
            hw["device"] = device
            hw["ngl"] = self._ask_int(
                "Couches déchargées sur GPU (-ngl)",
                99 if device == "gpu" else 0,
                help_text="99 = tout sur GPU Vulkan, 0 = 100% CPU"
            )

            hw["cpu_threads"] = self._ask_int(
                "Nombre de threads CPU (-t)",
                hw.get("cpu_threads", 4),
                help_text="Recommandé : 4 ou le nombre de cœurs de performance du processeur"
            )

            hw["batch"] = self._ask_int("Taille de Batch prompt (-b)", hw.get("batch", 512))
            hw["ubatch"] = self._ask_int("Taille de Micro-batch (-ub)", hw.get("ubatch", 128))

            # RAM unifiée Android : explications claires
            console.print("  [dim]Spécificités RAM Unifiée Mobile (LPDDR) :[/dim]")
            hw["mmap"] = self._ask_bool(
                "Activer mmap ? (Fortement Recommandé : évite la saturation et le crash LMK d'Android)",
                hw.get("mmap", True)
            )

            if hw["mmap"]:
                hw["mlock"] = self._ask_bool(
                    "Activer mlock ? (Déconseillé sans root : échoue avec 'invalid argument' sur Android)",
                    hw.get("mlock", False)
                )
            else:
                hw["mlock"] = False
                console.print("  [dim]mlock désactivé automatiquement (incompatible avec --no-mmap).[/dim]")

            hw["flash_attention"] = self._ask_bool(
                "Activer Flash Attention (-fa) ? (Recommandé : réduit l'empreinte mémoire par 2-3)",
                hw.get("flash_attention", True)
            )

            hw["kv_unified"] = self._ask_bool(
                "Activer le Cache KV unifié ?",
                hw.get("kv_unified", True)
            )

            hw["cache_type_k"] = Prompt.ask(
                "  [cyan]Quantification Cache Clés (K)[/cyan]",
                choices=["f16", "q8_0", "q4_0"],
                default=hw.get("cache_type_k", "f16")
            )
            hw["cache_type_v"] = Prompt.ask(
                "  [cyan]Quantification Cache Valeurs (V)[/cyan]",
                choices=["f16", "q8_0", "q4_0"],
                default=hw.get("cache_type_v", "f16")
            )

            # 4. Contexte & Prompt Système
            console.print(f"\n[bold {ANDROID_GREEN}]4. Taille de Contexte & Prompt Système[/bold {ANDROID_GREEN}]")
            ctx = current_data.setdefault("context", {})
            ctx["ctx_size"] = self._ask_int(
                "Taille du contexte en tokens (-c)",
                ctx.get("ctx_size", 4096),
                help_text="Ex: 2048 (économie RAM), 4096 (standard), 8192 (modèles 8B+)"
            )
            ctx["system_prompt"] = self._ask_str(
                "Prompt Système",
                ctx.get("system_prompt", "Tu es un assistant IA local concis et performant.")
            )

            # 5. Échantillonnage (Sampling)
            console.print(f"\n[bold {ANDROID_GREEN}]5. Réglages d'Échantillonnage (Sampling)[/bold {ANDROID_GREEN}]")
            sm = current_data.setdefault("sampling", {})
            sm["temperature"] = self._ask_float("Température (--temp)", sm.get("temperature", 0.7))
            sm["top_p"] = self._ask_float("Top P (--top-p)", sm.get("top_p", 0.9))
            sm["top_k"] = self._ask_int("Top K (--top-k)", sm.get("top_k", 40))
            sm["min_p"] = self._ask_float("Min P (--min-p)", sm.get("min_p", 0.05))
            sm["presence_penalty"] = self._ask_float("Pénalité de présence", sm.get("presence_penalty", 0.0))
            sm["repeat_penalty"] = self._ask_float("Pénalité de répétition", sm.get("repeat_penalty", 1.1))

            # 6. Raisonnement & Effort Jinja
            console.print(f"\n[bold {ANDROID_GREEN}]6. Raisonnement (Thinking) & Template Jinja[/bold {ANDROID_GREEN}]")
            rs = current_data.setdefault("reasoning", {})
            rs["enabled"] = self._ask_bool("Activer le mode raisonnement (Thinking) ?", rs.get("enabled", True))
            
            if rs["enabled"]:
                rs["effort"] = Prompt.ask(
                    "  [cyan]Niveau d'effort de réflexion (Jinja / Thinking)[/cyan]",
                    choices=REASONING_EFFORT_LEVELS,
                    default=rs.get("effort", "medium")
                )
                rs["budget_tokens"] = REASONING_TOKEN_BUDGETS.get(rs["effort"], 1024)
                console.print(f"  [dim]Budget tokens associé : {rs['budget_tokens']} tokens[/dim]")
                
                rs["jinja_template"] = self._ask_str(
                    "Template Jinja de réflexion",
                    rs.get("jinja_template", "{% if reasoning_effort %}<think>\nEffort: {{ reasoning_effort }}\n</think>\n{% endif %}{{ prompt }}")
                )

            # Enregistrement
            final_name = current_data["name"]
            success = self.manager.save_preset(final_name, current_data)
            if success:
                self.manager.set_active_preset_name(final_name)
                console.print(f"\n[bold {ANDROID_GREEN}]✓ Profil '{final_name}' enregistré et activé avec succès ![/bold {ANDROID_GREEN}]\n")
                return current_data
            else:
                console.print(f"\n[red]✗ Échec de l'enregistrement du profil '{final_name}'.[/red]\n")
                return None

        except KeyboardInterrupt:
            console.print("\n[yellow]Création / modification du profil annulée.[/yellow]\n")
            return None

    def edit_system_prompt_only(self, preset_name: str) -> bool:
        """Permet de voir et modifier directement le prompt système (/profil_Prompt_Systeme)."""
        data = self.manager.load_preset(preset_name)
        if not data:
            console.print(f"[red]Profil '{preset_name}' introuvable.[/red]")
            return False

        current_prompt = data.get("context", {}).get("system_prompt", "")
        console.print(Panel(
            f"[bold cyan]Prompt Système actuel pour le profil : [yellow]{preset_name}[/yellow][/bold cyan]\n\n"
            f"[white]{current_prompt}[/white]",
            border_style=ANDROID_GREEN
        ))

        modify = Confirm.ask("Voulez-vous modifier ce prompt système ?", default=True)
        if not modify:
            return True

        new_prompt = Prompt.ask("Nouveau prompt système", default=current_prompt)
        data.setdefault("context", {})["system_prompt"] = new_prompt
        self.manager.save_preset(preset_name, data)
        console.print(f"[bold {ANDROID_GREEN}]✓ Prompt système mis à jour pour '{preset_name}' ![/bold {ANDROID_GREEN}]")
        return True
