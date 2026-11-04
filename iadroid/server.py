"""
IADroid - Moteur Serveur Local (llama-server)
Gère le cycle de vie de llama-server en arrière-plan et la communication
REST / SSE via l'API OpenAI locale (127.0.0.1:8080).
"""

import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Tuple

from rich.console import Console

from .config import (
    LLAMA_SERVER_PATH,
    LOGS_DIR,
    ANDROID_GREEN
)
from .preset_manager import is_llama_running, stop_llama_processes

console = Console()

class LlamaServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.host = host
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self.active_preset_name: Optional[str] = None
        self.log_file = LOGS_DIR / "llama-server.log"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def get_status(self) -> str:
        """
        Retourne le statut actuel du serveur :
        - 'ready'   : Serveur en ligne et modèle chargé prêt à inférer (200 OK)
        - 'loading' : Modèle en cours de chargement en RAM/VRAM (503)
        - 'stopped' : Serveur non actif ou éteint
        """
        try:
            req = urllib.request.Request(f"{self.base_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=0.6) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ok":
                        return "ready"
                    elif data.get("status") == "loading model":
                        return "loading"
        except urllib.error.HTTPError as e:
            if e.code == 503:
                return "loading"
        except Exception:
            pass
        return "stopped"

    def is_ready(self) -> bool:
        return self.get_status() == "ready"

    def start(self, preset_data: Dict[str, Any], verbose: bool = True) -> bool:
        """
        Démarre llama-server en arrière-plan avec la configuration du profil.
        Attend que le modèle soit entièrement chargé en VRAM/RAM avant de rendre la main.
        """
        p_name = preset_data.get("name", "inconnu")

        # Si le serveur tourne déjà avec le MÊME profil et est prêt, ne rien faire
        if self.is_ready() and self.active_preset_name == p_name:
            if verbose:
                console.print(f"[bold {ANDROID_GREEN}]✓ Le modèle '{p_name}' est déjà chargé et prêt en mémoire ![/bold {ANDROID_GREEN}]")
            return True

        # Arrêt des processus existants pour libérer la VRAM
        if is_llama_running() or self.get_status() != "stopped":
            if verbose:
                console.print("[dim]Arrêt du processus précédent et libération de la RAM/VRAM...[/dim]")
            self.stop()

        # Vérification de l'existence du binaire llama-server
        if not LLAMA_SERVER_PATH.exists():
            console.print(f"[red]Binaire llama-server introuvable dans {LLAMA_SERVER_PATH}.[/red]")
            console.print("[yellow]Veuillez taper /cmake-recompiler pour compiler llama-server.[/yellow]")
            return False

        # Vérification du modèle
        model_path = preset_data.get("model_path")
        if not model_path or not os.path.exists(model_path):
            console.print(f"[red]Fichier modèle introuvable : {model_path}[/red]")
            return False

        # Construction des arguments pour llama-server
        cmd = [
            str(LLAMA_SERVER_PATH),
            "-m", model_path,
            "--host", self.host,
            "--port", str(self.port)
        ]

        # Multimodal vision (mmproj)
        mmproj = preset_data.get("mmproj_path")
        if mmproj and os.path.exists(mmproj):
            cmd.extend(["--mmproj", mmproj])

        # Hardware & GPU
        hw = preset_data.get("hardware", {})
        dev = hw.get("device", "gpu")
        ngl = hw.get("ngl", 99 if dev == "gpu" else 0)
        cmd.extend(["-ngl", str(ngl)])
        cmd.extend(["-t", str(hw.get("cpu_threads", 4))])
        cmd.extend(["-b", str(hw.get("batch", 512)), "-ub", str(hw.get("ubatch", 128))])

        # Flash Attention : Sur GPU Vulkan mobile (Adreno), -fa on fait crasher le pilote avec 'createComputePipeline: ErrorUnknown'
        use_fa = hw.get("gpu_flash_attention", False) if dev == "gpu" else hw.get("flash_attention", True)
        if use_fa:
            cmd.extend(["-fa", "on"])
        else:
            cmd.extend(["-fa", "off"])

        if not hw.get("mmap", True):
            cmd.append("--no-mmap")
        elif hw.get("mlock", False):
            cmd.append("--mlock")

        if hw.get("cache_type_k"):
            cmd.extend(["--cache-type-k", str(hw["cache_type_k"])])
        if hw.get("cache_type_v"):
            cmd.extend(["--cache-type-v", str(hw["cache_type_v"])])

        ctx_size = preset_data.get("context", {}).get("ctx_size", 4096)
        cmd.extend(["-c", str(ctx_size)])

        # Assurer la liaison du pilote Vulkan système sous Termux
        system_vk = Path("/system/lib64/libvulkan.so")
        prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
        termux_vk = Path(prefix) / "lib" / "libvulkan.so"
        if system_vk.exists() and termux_vk.parent.exists():
            try:
                if not termux_vk.is_symlink() or str(termux_vk.resolve()) != str(system_vk):
                    if termux_vk.exists() or termux_vk.is_symlink():
                        termux_vk.unlink()
                    os.symlink(str(system_vk), str(termux_vk))
            except Exception:
                pass

        env = os.environ.copy()
        env["TU_DEBUG"] = "noconform"
        env["MESA_VK_WSI_PRESENT_MODE"] = "mailbox"
        if system_vk.exists():
            env["LD_LIBRARY_PATH"] = f"/system/lib64:{prefix}/lib:{env.get('LD_LIBRARY_PATH', '')}"

        # Lancement en arrière-plan avec redirection des logs
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_fp = open(self.log_file, "w", encoding="utf-8", errors="replace")

        if verbose:
            dev_str = "GPU Vulkan (Adreno)" if ngl > 0 else "CPU ARM64"
            console.print(f"[cyan]⚡ Démarrage de llama-server en arrière-plan ({dev_str})...[/cyan]")
            console.print(f"[dim]Modèle : {os.path.basename(model_path)} | Contexte : {ctx_size} tokens[/dim]")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True
            )
        except Exception as e:
            console.print(f"[red]Erreur lors du lancement de llama-server : {e}[/red]")
            log_fp.close()
            return False

        # Attente active du chargement (Health Check)
        max_wait = 45  # secondes
        start_t = time.time()
        ready = False

        with console.status(f"[bold cyan]Chargement du modèle en VRAM...[/bold cyan]", spinner="dots"):
            while time.time() - start_t < max_wait:
                if self.process.poll() is not None:
                    break

                status = self.get_status()
                if status == "ready":
                    ready = True
                    break

                time.sleep(0.5)

        if ready:
            self.active_preset_name = p_name
            if verbose:
                console.print(f"[bold {ANDROID_GREEN}]✓ Modèle chargé avec succès en mémoire ![/bold {ANDROID_GREEN}]")
            return True
        else:
            console.print(f"\n[red]✗ Échec du chargement du modèle par llama-server.[/red]")
            try:
                if self.log_file.exists():
                    lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    err_lines = lines[-15:] if len(lines) >= 15 else lines
                    console.print(f"[yellow]Derniers logs du serveur ({self.log_file}) :[/yellow]")
                    for l in err_lines:
                        console.print(f"  [dim]{l}[/dim]")
                    full_log = "\n".join(err_lines).lower()
                    if "createcomputepipeline" in full_log or "vk::" in full_log or "errorunknown" in full_log:
                        console.print("\n[bold red]💡 Diagnostic GPU Vulkan :[/bold red]")
                        console.print("  [yellow]Le pilote Vulkan Adreno du smartphone a rejeté la compilation du pipeline de calcul GPU.[/yellow]")
                        console.print("  [cyan]Solutions recommandées :[/cyan]")
                        console.print("  • Basculer en mode CPU ultra-rapide (6 t/s) : [bold green]/device cpu[/bold green]")
                        console.print("  • Désactiver Flash Attention sur GPU : [bold green]/fa off[/bold green]")
                        console.print("  • Décharger moins de couches VRAM : [bold green]/device gpu 20[/bold green]")
            except Exception:
                pass
            self.stop()
            return False

    def stop(self) -> bool:
        """Arrête le serveur en arrière-plan et libère toutes les ressources."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

        stop_llama_processes()
        self.active_preset_name = None
        return True

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        sampling: Dict[str, Any],
        on_token: Callable[[str], None]
    ) -> Tuple[str, float, int]:
        """
        Envoie une requête de chat au format standard OpenAI /v1/chat/completions
        et diffuse la réponse token par token via Server-Sent Events (SSE).
        Retourne (texte_complet, durée_secondes, nombre_tokens).
        """
        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "messages": messages,
            "stream": True,
            "temperature": sampling.get("temperature", 0.7),
            "top_p": sampling.get("top_p", 0.9),
            "top_k": sampling.get("top_k", 40),
            "min_p": sampling.get("min_p", 0.05),
            "repeat_penalty": sampling.get("repeat_penalty", 1.1)
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        full_response = []
        token_count = 0
        start_time = time.time()

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                token_count += 1
                                full_response.append(content)
                                on_token(content)
                    except Exception:
                        continue

        except KeyboardInterrupt:
            raise
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connexion au serveur local interrompue : {e}")

        elapsed = max(0.01, time.time() - start_time)
        return "".join(full_response), elapsed, token_count
