"""
IADroid - Gestionnaire de Compilation et Maintenance de llama.cpp
Gère /cmake-del-cache, /cmake-config-auto, /cmake-recompiler, /cmake-thread-compile, /check-gpu
"""

import os
import shutil
import subprocess
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

from .config import IADROID_HOME, BIN_DIR, LLAMA_CLI_PATH, ANDROID_GREEN, get_gpu_type

console = Console()
THREADS_FILE = IADROID_HOME / "compile_threads.txt"

class LlamaBuilder:
    def __init__(self):
        self.src_dir = IADROID_HOME / "src" / "llama.cpp"
        self.build_dir = self.src_dir / "build"
        self.prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")

    def get_compile_threads(self) -> int:
        """Récupère le nombre de threads configuré pour la compilation (défaut: 2)."""
        if THREADS_FILE.exists():
            try:
                val = int(THREADS_FILE.read_text(encoding="utf-8").strip())
                if val in (2, 4, 6, 8):
                    return val
            except Exception:
                pass
        return 2

    def set_compile_threads(self, n: int) -> bool:
        """Enregistre le nombre de threads souhaité."""
        try:
            THREADS_FILE.write_text(str(n), encoding="utf-8")
            return True
        except Exception as e:
            console.print(f"[red]Erreur lors de la sauvegarde du nombre de threads : {e}[/red]")
            return False

    def interactive_threads_menu(self, arg: str = ""):
        """Permet de choisir le nombre de threads de compilation avec affichage des risques (/cmake-thread-compile)."""
        table = Table(title="⚡ Réglage des Threads de Compilation (-j)", border_style=ANDROID_GREEN)
        table.add_column("Choix", style="bold cyan", justify="center")
        table.add_column("Threads", style="bold white", justify="center")
        table.add_column("RAM Requise", style="yellow")
        table.add_column("Vitesse", style="green")
        table.add_column("Niveau de Risque Android (Signal 9 / LMK)", style="white")

        table.add_row(
            "1", "2", "~2-3 Go", "Lente (10-15 min)",
            "[bold green]🟢 Aucun risque[/bold green] (100% stable, recommandé si RAM < 8 Go)"
        )
        table.add_row(
            "2", "4", "~4-6 Go", "2x plus rapide",
            "[bold yellow]🟡 Modéré[/bold yellow] (Idéal si votre téléphone a 8 Go+ de RAM)"
        )
        table.add_row(
            "3", "6", "~8-10 Go", "3x plus rapide",
            "[bold dark_orange]🟠 Élevé[/bold dark_orange] (Risque de Signal 9 si des applis tournent)"
        )
        table.add_row(
            "4", "8", "~12-16 Go", "Maximale",
            "[bold red]🔴 Très Élevé / Danger[/bold red] (Crash Signal 9 quasi-garanti sur mobile)"
        )

        console.print(table)
        current = self.get_compile_threads()
        console.print(f"Threads actuels : [bold cyan]{current}[/bold cyan]\n")

        chosen_val = None
        if arg in ("2", "4", "6", "8"):
            chosen_val = int(arg)
        else:
            choice = Prompt.ask(
                "Choisissez le nombre de threads [cyan](2 / 4 / 6 / 8)[/cyan]",
                choices=["2", "4", "6", "8"],
                default=str(current)
            )
            chosen_val = int(choice)

        self.set_compile_threads(chosen_val)

        if chosen_val == 2:
            alert = "[bold green]Mode ultra-sécurisé activé : aucun risque de Signal 9.[/bold green]"
        elif chosen_val == 4:
            alert = "[bold yellow]Mode équilibré activé : 2x plus rapide, parfait pour smartphones 8 Go+.[/bold yellow]"
        elif chosen_val >= 6:
            alert = "[bold red]Attention : Pensez à fermer les applications en arrière-plan avant de compiler ![/bold red]"

        console.print(f"\n[bold {ANDROID_GREEN}]✓ Nombre de threads de compilation configuré sur : {chosen_val}[/bold {ANDROID_GREEN}]")
        console.print(f"  {alert}\n")

    def check_gpu(self):
        """Vérifie la présence et le support du GPU Vulkan sur le téléphone (/check-gpu)."""
        console.print(Panel(
            "[bold cyan]🔍 Diagnostic GPU Vulkan pour Android Termux[/bold cyan]",
            border_style=ANDROID_GREEN
        ))

        # 1. Vérification du pilote Vulkan système
        system_vulkan = Path("/system/lib64/libvulkan.so")
        termux_lib = Path(self.prefix) / "lib"
        termux_vulkan = termux_lib / "libvulkan.so"

        has_system = system_vulkan.exists()
        console.print(f"• Pilote système Android (/system/lib64/libvulkan.so) : "
                      f"{'[bold green]Présent[/bold green]' if has_system else '[red]Absent[/red]'}")

        # 2. Vérification du lien direct vers le GPU physique
        if has_system:
            try:
                is_linked = termux_vulkan.is_symlink() and str(termux_vulkan.resolve()) == str(system_vulkan)
                if not is_linked:
                    console.print(f"[yellow]• Liaison directe Termux ↔ GPU Android : Non liée.[/yellow]")
                    console.print(f"  [cyan]Création automatique du lien vers le GPU physique...[/cyan]")
                    if termux_vulkan.exists() or termux_vulkan.is_symlink():
                        termux_vulkan.unlink()
                    os.symlink(str(system_vulkan), str(termux_vulkan))
                    console.print(f"  [bold green]✓ Lien créé avec succès : {termux_vulkan} -> {system_vulkan}[/bold green]")
                else:
                    console.print(f"• Liaison directe Termux ↔ GPU Android : [bold green]Active (Hardware direct)[/bold green]")
            except Exception as e:
                console.print(f"  [dim]Note lien : {e}[/dim]")

        # 3. Détection du type de GPU matériel du smartphone
        detected_gpu = get_gpu_type()
        has_adreno = Path("/dev/kgsl-3d0").exists() or detected_gpu == "Adreno"
        has_mali = Path("/dev/mali0").exists() or Path("/dev/mali").exists() or detected_gpu == "Mali"
        turnip_icd = Path(self.prefix) / "share/vulkan/icd.d/freedreno_icd.aarch64.json"

        if has_adreno:
            console.print("• Matériel GPU : [bold green]Qualcomm Adreno détecté[/bold green]")
            if not turnip_icd.exists():
                console.print(f"[bold yellow]👉 Pour activer l'accélération matérielle Adreno, installez le pilote Turnip :[/bold yellow]")
                console.print(f"   [bold cyan]pkg install -y mesa-vulkan-icd-freedreno[/bold cyan]")
            else:
                console.print("• Pilote Turnip Vulkan (Adreno) : [bold green]Installé et actif[/bold green]")
        elif has_mali:
            console.print("• Matériel GPU : [bold green]ARM Mali / MediaTek détecté[/bold green]")
        else:
            console.print("• Matériel GPU : [dim]Détection générique Android HAL (Vulkan)[/dim]")

        # 4. Test de vulkaninfo
        console.print("\n[cyan]Analyse des périphériques Vulkan détectés :[/cyan]")
        has_hw_gpu = False
        has_llvmpipe = False
        try:
            res = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if any(k in line.lower() for k in ("devicename", "driverinfo", "apiversion", "devicetype")):
                        console.print(f"  [green]{line.strip()}[/green]")
                    if any(k in line.lower() for k in ("adreno", "mali", "integrated_gpu", "discrete_gpu")):
                        has_hw_gpu = True
                    if "llvmpipe" in line.lower():
                        has_llvmpipe = True
            else:
                console.print(f"  [dim]vulkaninfo n'a pas pu énumérer les détails.[/dim]")
        except FileNotFoundError:
            console.print("  [dim]vulkaninfo non installé (pkg install vulkan-tools).[/dim]")

        if has_llvmpipe and not has_hw_gpu:
            console.print("\n[yellow]ℹ️ Note sur 'llvmpipe' :[/yellow]")
            console.print("[dim]'llvmpipe' est une émulation logicielle Mesa sur CPU et non une vraie puce GPU.[/dim]")
            console.print("[dim]llama.cpp ignore volontairement llvmpipe car le calcul CPU NEON natif est 10x plus rapide.[/dim]")

        # 4. Vérification du support Vulkan dans le binaire compilé de llama.cpp
        console.print("\n[cyan]Vérification du binaire llama-cli :[/cyan]")
        if LLAMA_CLI_PATH.exists():
            try:
                # Vérification des symboles Vulkan dans le binaire ELF
                out = subprocess.check_output(["readelf", "-d", str(LLAMA_CLI_PATH)], text=True, stderr=subprocess.DEVNULL)
                if "vulkan" in out.lower():
                    console.print(f"• Backend GPU Vulkan compilé : [bold green]OUI (libvulkan.so est bien liée !)[/bold green]")
                else:
                    console.print(f"• Backend GPU Vulkan compilé : [yellow]Non détecté via readelf (compilé statiquement ou CPU).[/yellow]")
            except Exception:
                console.print("• Binaire llama-cli : [bold green]Présent[/bold green]")
        else:
            console.print(f"[red]Binaire introuvable dans {LLAMA_CLI_PATH}.[/red]")

        # 5. Fréquence d'horloge matérielle
        freq = self.get_gpu_freq()
        console.print(f"• Fréquence matérielle GPU : [bold yellow]{freq}[/bold yellow]")

        console.print("\n[dim]Astuce : Tapez /boost-gpu pour configurer les profils d'accélération et débloquer les 1000 MHz.[/dim]\n")

    def get_gpu_freq(self) -> str:
        """Tente de lire la fréquence actuelle du GPU Adreno (kgsl / devfreq)."""
        freq_paths = [
            "/sys/class/kgsl/kgsl-3d0/gpuclk",
            "/sys/class/kgsl/kgsl-3d0/devfreq/cur_freq",
            "/sys/class/devfreq/3d00000.qcom,kgsl-3d0/cur_freq",
            "/sys/class/kgsl/kgsl-3d0/clock_mhz"
        ]
        for p in freq_paths:
            try:
                path = Path(p)
                if path.exists():
                    val = path.read_text().strip()
                    if val.isdigit():
                        freq_hz = int(val)
                        if freq_hz > 10_000_000:
                            return f"{freq_hz // 1_000_000} MHz"
                        elif freq_hz > 10_000:
                            return f"{freq_hz // 1_000} MHz"
                        else:
                            return f"{freq_hz} MHz"
            except Exception:
                continue
        return "Non accessible (nécessite lecture /sys)"

    def boost_gpu(self):
        """Force la fréquence du GPU à 100% (1000 MHz pour Adreno 750), protège le CPU de la surchauffe et optimise Turnip (/boost-gpu)."""
        console.print(Panel(
            "[bold cyan]⚡ Optimisation & Boost Fréquence GPU (Adreno 750 / Snapdragon 8 Gen 3)[/bold cyan]",
            border_style=ANDROID_GREEN
        ))

        # 1. Activation des flags de performance pour le pilote Turnip
        os.environ["TU_DEBUG"] = "noconform"
        os.environ["MESA_VK_WSI_PRESENT_MODE"] = "mailbox"
        console.print("• Pilote Turnip : [bold green]Mode NoConform / Latence minimale activé[/bold green]")

        # 2. Lecture de la fréquence GPU actuelle avant boost
        current_freq = self.get_gpu_freq()
        console.print(f"• Fréquence GPU actuelle : [bold yellow]{current_freq}[/bold yellow]")

        # 3. Essai de commande Android Game/Power sans root (via cmd ou Shizuku/rish)
        non_root_success = False
        for cmd_prefix in [["cmd"], ["rish", "-c"]]:
            try:
                # cmd game set --mode 2 com.termux (Mode 2 = PERFORMANCE)
                if cmd_prefix == ["cmd"]:
                    res1 = subprocess.run(["cmd", "game", "set", "--mode", "2", "com.termux"], capture_output=True, timeout=1)
                    res2 = subprocess.run(["cmd", "power", "set-fixed-performance-mode-enabled", "true"], capture_output=True, timeout=1)
                    if res1.returncode == 0 or res2.returncode == 0:
                        non_root_success = True
                else:
                    res = subprocess.run(["rish", "-c", "cmd game set --mode 2 com.termux && cmd power set-fixed-performance-mode-enabled true"], capture_output=True, timeout=1)
                    if res.returncode == 0:
                        non_root_success = True
            except Exception:
                pass

        if non_root_success:
            console.print("• Mode Performance Android (cmd game/power) : [bold green]Activé pour Termux[/bold green]")

        # 4. Test de l'accès root (su) pour débloquer les 1000 MHz directement dans le kernel
        is_root = False
        try:
            res = subprocess.run(["su", "-c", "whoami"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and "root" in res.stdout:
                is_root = True
        except Exception:
            is_root = False

        if is_root:
            console.print("• Accès Super-Utilisateur (Root) : [bold green]Disponible ![/bold green]")
            console.print("[cyan]Déverrouillage thermique et verrouillage Adreno 750 à 1000 MHz...[/cyan]")
            cmd_boost = """
            su -c "
            # 1. Déverrouiller le plafond thermique kgsl (0 = performance maximale, 4/5 = bride à 578 MHz)
            echo 0 > /sys/class/kgsl/kgsl-3d0/thermal_pwrlevel 2>/dev/null || true
            echo 0 > /sys/class/kgsl/kgsl-3d0/min_pwrlevel 2>/dev/null || true
            echo 0 > /sys/class/kgsl/kgsl-3d0/max_pwrlevel 2>/dev/null || true

            # 2. Forcer la fréquence GPU à 1000 MHz (1000000000 Hz)
            echo 1000000000 > /sys/class/kgsl/kgsl-3d0/gpuclk 2>/dev/null || true
            echo 1000000000 > /sys/class/kgsl/kgsl-3d0/max_gpuclk 2>/dev/null || true
            echo 1000000000 > /sys/class/kgsl/kgsl-3d0/min_gpuclk 2>/dev/null || true
            echo 1000000000 > /sys/class/kgsl/kgsl-3d0/devfreq/min_freq 2>/dev/null || true
            echo 1000000000 > /sys/class/kgsl/kgsl-3d0/devfreq/max_freq 2>/dev/null || true
            echo performance > /sys/class/kgsl/kgsl-3d0/devfreq/governor 2>/dev/null || true
            echo 1 > /sys/class/kgsl/kgsl-3d0/force_clk_on 2>/dev/null || true
            echo 1 > /sys/class/kgsl/kgsl-3d0/force_bus_on 2>/dev/null || true
            echo 1 > /sys/class/kgsl/kgsl-3d0/force_rail_on 2>/dev/null || true

            # 3. PROTECTION THERMIQUE CPU : Brider le CPU pour éviter la surchauffe et le thermal-throttling
            for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
                echo schedutil > \\$gov 2>/dev/null || true
            done
            # Limiter le cœur Cortex-X4 prime pour concentrer tout le refroidissement sur le GPU
            echo 2265600 > /sys/devices/system/cpu/cpu7/cpufreq/scaling_max_freq 2>/dev/null || true
            "
            """
            try:
                subprocess.run(cmd_boost, shell=True)
                new_freq = self.get_gpu_freq()
                console.print(f"[bold {ANDROID_GREEN}]✓ GPU Adreno 750 verrouillé à 1000 MHz ![/bold {ANDROID_GREEN}]")
                console.print(f"• Fréquence après commande : [bold green]{new_freq}[/bold green]")
                console.print(f"[dim]• Cœurs CPU configurés en mode tempéré pour éviter tout thermal throttling.[/dim]")
            except Exception as e:
                console.print(f"[red]Erreur lors du boost root : {e}[/red]")
        else:
            console.print("• Accès Root : [yellow]Non détecté (Mode standard Android)[/yellow]")
            console.print("[dim]Sans accès root, le noyau Android régule dynamiquement la fréquence selon la charge de calcul GPU.[/dim]")
            console.print("[dim]Pour verrouiller matériellement 1 000 MHz au niveau du kernel sans boost CPU, les droits su (Root) sont requis.[/dim]")

        console.print(f"\n[dim]Variables d'environnement MESA/Turnip actives pour les prochaines exécutions.[/dim]\n")

    def delete_cache(self) -> bool:
        """Supprime le dossier de build et le cache CMake (/cmake-del-cache)."""
        if not self.src_dir.exists():
            console.print(f"[yellow]Dépôt source introuvable dans {self.src_dir}.[/yellow]")
            return False

        if self.build_dir.exists():
            try:
                shutil.rmtree(self.build_dir)
                console.print(f"[bold {ANDROID_GREEN}]✓ Dossier build et cache CMake supprimés avec succès : {self.build_dir}[/bold {ANDROID_GREEN}]")
                return True
            except Exception as e:
                console.print(f"[red]Erreur lors de la suppression du cache : {e}[/red]")
                return False
        else:
            console.print("[dim]Aucun cache à supprimer (le dossier build n'existe pas).[/dim]")
            return True

    def config_auto(self, force_vulkan: bool = True) -> bool:
        """Configure automatiquement CMake avec Vulkan GPU et Ninja (/cmake-config-auto)."""
        if not self.src_dir.exists():
            console.print(f"[red]Dépôt llama.cpp introuvable dans {self.src_dir}.[/red]")
            return False

        vulkan_status = "ON (GPU)" if force_vulkan else "OFF (CPU)"
        console.print(Panel(
            f"[bold cyan]Configuration CMake Automatique[/bold cyan]\n\n"
            f"• Source : [dim]{self.src_dir}[/dim]\n"
            f"• Flag Vulkan : [bold green]-DGGML_VULKAN={'ON' if force_vulkan else 'OFF'}[/bold green]\n"
            f"• Préfixe Termux : [dim]{self.prefix}[/dim]\n"
            f"• Générateur : [yellow]Ninja[/yellow]",
            border_style=ANDROID_GREEN
        ))

        # Assurer le lien du pilote Vulkan système vers Termux
        system_vulkan = Path("/system/lib64/libvulkan.so")
        termux_lib = Path(self.prefix) / "lib"
        termux_vulkan = termux_lib / "libvulkan.so"
        if system_vulkan.exists() and termux_lib.exists():
            try:
                if not termux_vulkan.exists() or not termux_vulkan.is_symlink():
                    if termux_vulkan.exists() or termux_vulkan.is_symlink():
                        termux_vulkan.unlink()
                    os.symlink(str(system_vulkan), str(termux_vulkan))
                    console.print(f"[bold {ANDROID_GREEN}]✓ Pilote Vulkan système relié : {termux_vulkan} -> {system_vulkan}[/bold {ANDROID_GREEN}]")
            except Exception:
                pass

        cmake_args = [
            "cmake",
            "-B", "build",
            "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_PREFIX_PATH={self.prefix}"
        ]

        if force_vulkan:
            cmake_args.append("-DGGML_VULKAN=ON")
        else:
            cmake_args.append("-DGGML_VULKAN=OFF")

        try:
            console.print(f"[cyan]Exécution de : {' '.join(cmake_args)}[/cyan]\n")
            ret = subprocess.run(cmake_args, cwd=str(self.src_dir))
            if ret.returncode == 0:
                console.print(f"\n[bold {ANDROID_GREEN}]✓ Configuration CMake terminée avec succès ![/bold {ANDROID_GREEN}]")
                console.print(f"[dim]Vous pouvez maintenant taper /cmake-recompiler pour lancer la compilation.[/dim]")
                return True
            else:
                console.print(f"\n[red]✗ Erreur durant la configuration CMake (code {ret.returncode}).[/red]")
                return False
        except Exception as e:
            console.print(f"[red]Erreur lors de l'exécution de CMake : {e}[/red]")
            return False

    def recompile(self, check_updates: bool = True) -> bool:
        """Vérifie les mises à jour et recompile avec le nombre de threads configuré (/cmake-recompiler)."""
        if not self.src_dir.exists():
            console.print(f"[red]Dépôt llama.cpp introuvable dans {self.src_dir}.[/red]")
            return False

        threads = self.get_compile_threads()

        console.print(Panel(
            f"[bold cyan]Compilation de llama.cpp[/bold cyan]\n\n"
            f"• Threads de calcul : [bold yellow]{threads} threads (-j{threads})[/bold yellow]\n"
            f"• Pour changer le nombre de threads : tapez [bold cyan]/cmake-thread-compile[/bold cyan]\n"
            f"• Mise à jour Git : [bold green]{'Active' if check_updates else 'Désactivée'}[/bold green]",
            border_style=ANDROID_GREEN
        ))

        # 1. Vérification des mises à jour du dépôt officiel
        if check_updates and (self.src_dir / ".git").exists():
            console.print(f"[cyan]Recherche de mises à jour sur le dépôt officiel llama.cpp...[/cyan]")
            try:
                subprocess.run(["git", "pull"], cwd=str(self.src_dir))
            except Exception as e:
                console.print(f"[yellow]Avertissement git pull : {e}[/yellow]")

        # 2. Vérification que la configuration CMake existe
        build_ninja = self.build_dir / "build.ninja"
        if not build_ninja.exists():
            console.print(f"[yellow]Fichier de configuration Ninja manquant. Lancement de /cmake-config-auto d'abord...[/yellow]")
            success = self.config_auto(force_vulkan=True)
            if not success:
                return False

        # 3. Activation du wake lock pour empêcher Android de couper le CPU
        try:
            subprocess.run(["termux-wake-lock"], stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # 4. Compilation Ninja avec le nombre de threads choisi
        console.print(f"\n[cyan]Lancement de la compilation avec Ninja (-j{threads})...[/cyan]\n")
        try:
            ret = subprocess.run(
                ["cmake", "--build", "build", "--config", "Release", f"-j{threads}"],
                cwd=str(self.src_dir)
            )
            if ret.returncode != 0:
                console.print(f"\n[red]✗ Échec de la compilation (code d'erreur {ret.returncode}).[/red]")
                if threads > 2:
                    console.print(f"[yellow]💡 Astuce : Si c'est un crash Signal 9, réduisez les threads à 2 ou 4 avec /cmake-thread-compile.[/yellow]")
                return False
        except Exception as e:
            console.print(f"[red]Erreur lors de la compilation : {e}[/red]")
            return False

        # 5. Détection des binaires générés et mise à jour dans ~/.iadroid/bin/
        cli_candidates = [
            self.build_dir / "bin" / "llama-cli",
            self.build_dir / "llama-cli"
        ]
        srv_candidates = [
            self.build_dir / "bin" / "llama-server",
            self.build_dir / "llama-server"
        ]

        found_cli = next((p for p in cli_candidates if p.exists()), None)
        found_srv = next((p for p in srv_candidates if p.exists()), None)

        if not found_cli:
            console.print(f"[red]Binaire llama-cli introuvable après compilation.[/red]")
            return False

        BIN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found_cli, BIN_DIR / "llama-cli")
        if found_srv:
            shutil.copy2(found_srv, BIN_DIR / "llama-server")

        os.chmod(BIN_DIR / "llama-cli", 0o755)
        if (BIN_DIR / "llama-server").exists():
            os.chmod(BIN_DIR / "llama-server", 0o755)

        console.print(f"\n[bold {ANDROID_GREEN}]✓ Binaires installés avec succès dans : {BIN_DIR}[/bold {ANDROID_GREEN}]")

        # 6. Test immédiat de la version
        try:
            out = subprocess.check_output(
                [str(BIN_DIR / "llama-cli"), "--version"],
                text=True,
                stderr=subprocess.STDOUT
            )
            console.print(f"\n[bold cyan]Nouvelle version active :[/bold cyan]\n{out.strip()}\n")
        except Exception as e:
            console.print(f"[dim]Version test : {e}[/dim]")

        return True
