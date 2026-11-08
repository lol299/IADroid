"""
IADroid - Moteur Serveur Local (llama-server)
Gère le cycle de vie de llama-server en arrière-plan et la communication
REST / SSE via l'API OpenAI locale (127.0.0.1:8080).
"""

import os
import sys
import json
import time
import socket
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Tuple

from rich.console import Console

from .config import (
    LLAMA_SERVER_PATH,
    LOGS_DIR,
    ANDROID_GREEN,
    get_gpu_type,
    get_vulkan_env,
    get_vulkan_driver
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
        # Vérifier d'abord que le processus est vivant
        # (copie locale : self.process peut être remis à None par un autre thread)
        proc = self.process
        if proc and proc.poll() is not None:
            self.process = None
            return "stopped"

        try:
            req = urllib.request.Request(
                f"{self.base_url}/health",
                method="GET",
                headers={"Connection": "close"}
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
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

        # Vérification du modèle et des permissions de lecture
        model_path = preset_data.get("model_path")
        if not model_path:
            console.print("[red]Chemin du modèle non spécifié dans le profil.[/red]")
            return False

        try:
            with open(model_path, "rb") as f:
                header = f.read(4)
                if header != b"GGUF":
                    console.print(f"[yellow]⚠️ Attention : Le fichier {model_path} ne semble pas être un modèle GGUF valide.[/yellow]")
        except PermissionError:
            console.print(f"\n[bold red]✗ Erreur de permission Android (Permission denied) :[/bold red]")
            console.print(f"  [yellow]Termux n'a pas l'autorisation d'accéder au stockage Android pour lire ce fichier :[/yellow]")
            console.print(f"  [white]{model_path}[/white]\n")
            console.print("  [cyan]Solutions recommandées :[/cyan]")
            console.print("  1. Tapez dans Termux : [bold green]termux-setup-storage[/bold green] et acceptez la demande d'accès.")
            console.print("  2. Dans Paramètres Android > Applications > Termux > Autorisations > Fichiers : cochez [bold green]'Autoriser la gestion de tous les fichiers'[/bold green].")
            console.print("  3. Ou déplacez le modèle .gguf dans le stockage interne de Termux : [bold green]~/.iadroid/models/[/bold green]\n")
            return False
        except FileNotFoundError:
            console.print(f"[red]Fichier modèle introuvable : {model_path}[/red]")
            console.print("[yellow]Vérifiez le chemin du fichier avec /profil_config ou téléchargez un modèle.[/yellow]")
            return False
        except Exception as e:
            console.print(f"[red]Erreur lors de l'accès au fichier modèle : {e}[/red]")
            return False

        # Construction des arguments pour llama-server
        cmd = [
            str(LLAMA_SERVER_PATH),
            "-m", model_path,
            "--host", self.host,
            "--port", str(self.port),
            "--alias", p_name
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

        # Pilote Vulkan : Turnip (Adreno) ou pilote système Android (Mali) — voir config.get_vulkan_driver
        env = get_vulkan_env()

        # Lancement en arrière-plan avec redirection des logs
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_fp = open(self.log_file, "w", encoding="utf-8", errors="replace")

        if verbose:
            gpu_name = get_gpu_type()
            drv = get_vulkan_driver()
            drv_label = "Turnip" if drv == "turnip" else "pilote système"
            dev_str = f"GPU Vulkan ({gpu_name} · {drv_label})" if ngl > 0 else "CPU ARM64"
            console.print(f"[cyan]⚡ Démarrage de llama-server en arrière-plan ({dev_str})...[/cyan]")
            console.print(f"[dim]Modèle : {os.path.basename(model_path)} | Contexte : {ctx_size} tokens[/dim]")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True
            )
            self.process = proc
        except Exception as e:
            console.print(f"[red]Erreur lors du lancement de llama-server : {e}[/red]")
            log_fp.close()
            return False

        # Attente active du chargement (Health Check)
        # Le premier chargement d'un modèle depuis /storage/emulated (FUSE) peut dépasser
        # la minute ; on attend tant que le processus est vivant (un crash sort immédiatement).
        max_wait = 300  # secondes
        start_t = time.time()
        ready = False

        with console.status(f"[bold cyan]Chargement du modèle en VRAM...[/bold cyan]", spinner="dots") as load_status:
            while time.time() - start_t < max_wait:
                # Référence locale : get_status() peut remettre self.process à None
                if proc.poll() is not None:
                    break

                elapsed = int(time.time() - start_t)
                load_status.update(f"[bold cyan]Chargement du modèle en VRAM... {elapsed}s[/bold cyan]")

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
                    if "permission denied" in full_log:
                        console.print("\n[bold red]💡 Diagnostic Permission Android (Permission denied) :[/bold red]")
                        console.print("  [yellow]Termux n'a pas l'autorisation d'accéder au fichier dans le stockage Android partagé.[/yellow]")
                        console.print("  [cyan]Solutions recommandées :[/cyan]")
                        console.print("  1. Tapez dans Termux : [bold green]termux-setup-storage[/bold green] et validez l'autorisation.")
                        console.print("  2. Dans Paramètres Android > Applications > Termux > Autorisations > Fichiers : cochez [bold green]'Autoriser la gestion de tous les fichiers'[/bold green].")
                        console.print("  3. Ou déplacez le modèle .gguf dans le stockage privé de Termux : [bold green]~/.iadroid/models/[/bold green]")
                    elif "createcomputepipeline" in full_log or "vk::" in full_log or "errorunknown" in full_log:
                        gpu_name = get_gpu_type()
                        gpu_title = f" ({gpu_name})" if gpu_name != "Vulkan" else ""
                        gpu_driver = f" {gpu_name}" if gpu_name != "Vulkan" else ""
                        console.print(f"\n[bold red]💡 Diagnostic GPU Vulkan{gpu_title} :[/bold red]")
                        console.print(f"  [yellow]Le pilote Vulkan{gpu_driver} du smartphone a rejeté la compilation du pipeline de calcul GPU (createComputePipeline).[/yellow]")
                        if "gemma" in os.path.basename(model_path).lower():
                            console.print("  [red]⚠️ Incompatibilité connue : L'architecture Gemma (logit soft-capping / head_dim=256) fait souvent échouer les shaders Vulkan sur GPU mobile.[/red]")
                        console.print("  [cyan]Solutions recommandées :[/cyan]")
                        console.print("  • Basculer en mode CPU : [bold green]/device cpu[/bold green]")
                        console.print("  • Désactiver Flash Attention sur GPU : [bold green]/fa off[/bold green]")
                        console.print("  • Décharger moins de couches VRAM : [bold green]/device gpu 15[/bold green]")
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
        on_token: Callable[[str], None],
        reasoning_effort: Optional[str] = None
    ) -> Tuple[str, float, int]:
        """
        Envoie une requête de chat au format standard OpenAI /v1/chat/completions
        et diffuse la réponse token par token via Server-Sent Events (SSE).
        Gère à la fois le flux de réponse standard (content) et le flux de réflexion (reasoning_content).
        Retourne (texte_complet, durée_secondes, nombre_tokens).
        """
        # Vérifier que le processus serveur est toujours vivant avant d'envoyer
        proc = self.process
        if proc and proc.poll() is not None:
            raise RuntimeError(
                f"Le processus llama-server a crashé (code retour: {proc.returncode}). "
                "Relancez avec /load ou /run."
            )

        # Vérifier la disponibilité du serveur via health check
        status = self.get_status()
        if status != "ready":
            raise RuntimeError(
                f"Le serveur n'est pas prêt (statut: {status}). "
                "Relancez avec /load ou /run."
            )

        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "model": self.active_preset_name or "default",
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
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Connection": "close"
            }
        )

        full_response = []
        token_count = 0
        start_time = time.time()
        in_reasoning_mode = False

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data: "):
                        # Vérifier que le processus serveur est toujours vivant
                        proc = self.process
                        if proc and proc.poll() is not None:
                            raise RuntimeError("Le processus llama-server a crashé pendant la génération.")
                        continue

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})

                            # Support des tokens de réflexion (llama.cpp b4500+, DeepSeek, Qwen2.5, Gemma)
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if reasoning:
                                if not in_reasoning_mode:
                                    in_reasoning_mode = True
                                    on_token("<think>")
                                token_count += 1
                                on_token(reasoning)

                            content = delta.get("content")
                            if content:
                                if in_reasoning_mode:
                                    in_reasoning_mode = False
                                    on_token("</think>")
                                token_count += 1
                                full_response.append(content)
                                on_token(content)
                    except Exception:
                        continue

                # Fermer proprement la balise de réflexion si le flux se termine pendant le raisonnement
                if in_reasoning_mode:
                    in_reasoning_mode = False
                    on_token("</think>")

        except (socket.timeout, TimeoutError):
            elapsed = time.time() - start_time
            if token_count == 0:
                raise RuntimeError(
                    f"Le serveur n'a produit aucun token en {elapsed:.0f}s. "
                    "Le modèle est peut-être trop lourd pour la RAM disponible, ou le serveur ne répond pas. "
                    "Essayez : /stop puis /load"
                )
        except KeyboardInterrupt:
            raise
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"Erreur HTTP {e.code} du serveur llama-server : {e.reason}\n{error_body}".strip()
            )
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connexion au serveur local interrompue : {e}")

        elapsed = max(0.01, time.time() - start_time)
        return "".join(full_response), elapsed, token_count
