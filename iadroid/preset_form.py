"""
IADroid - Création et modification des profils par formulaire (/create-preset, /profil)

Remplace l'assistant question par question (wizard.py) : tous les réglages sont visibles
d'un coup, regroupés par section, et chacun s'ouvre dans un sous-menu.
"""

import os
import re
from typing import Optional

from .config import (
    MODELS_DIR,
    REASONING_TOKEN_BUDGETS,
    get_default_preset,
    recommended_cpu_threads,
    reasoning_caps,
    gguf_has_mtp,
    get_gpu_type,
    console,
)
from .menus import Field, edit_form, select, confirm, get_path

EFFORT_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh"]
MODEL_DIRS = [str(MODELS_DIR), "~", "~/models", "~/bench", "/storage/emulated/0/Models",
              "/storage/emulated/0/Download"]


def effort_note(model_path: str, level: str) -> str:
    """Effet réel d'un niveau d'effort sur ce modèle (template du modèle lu dans le GGUF)."""
    caps = reasoning_caps(model_path)
    if not caps["thinking"]:
        return "ce modèle ne réfléchit pas : réglage sans effet"
    if level == "off":
        return "réponse directe, sans réflexion" if caps["toggle"] else "ce modèle ne sait pas désactiver sa réflexion"
    if caps["effort"]:
        return f"niveau « {level} » transmis au modèle (reasoning_effort)"
    return f"réflexion limitée à {REASONING_TOKEN_BUDGETS.get(level, 1024)} tokens"


def _thinking_ok(d: dict) -> bool:
    return reasoning_caps(d.get("model_path") or "")["thinking"]


def preset_fields() -> list:
    gpu = get_gpu_type()
    best = recommended_cpu_threads()
    on_gpu = lambda d: get_path(d, "hardware.device") == "gpu"
    on_cpu = lambda d: get_path(d, "hardware.device") == "cpu"
    return [
        Field("name", "Nom", "text", "Profil", required=True, help="Nom unique (lettres, chiffres, - _ .)"),
        Field("description", "Description", "text", "Profil"),

        Field("model_path", "Modèle (GGUF)", "path", "Modèle", required=True, path_dirs=MODEL_DIRS,
              note=lambda d: d.get("model_path") or ""),
        Field("mmproj_path", "Vision (mmproj)", "path", "Modèle", path_dirs=MODEL_DIRS,
              help="Projecteur de vision, uniquement pour les modèles multimodaux"),

        Field("hardware.device", "Calcul", "choice", "Matériel", choices=["gpu", "cpu"],
              note=lambda d: ("Sur Adreno, le CPU est souvent plus rapide (surtout en Q4_0)"
                              if gpu == "Adreno" else "")),
        Field("hardware.ngl", "Couches GPU (-ngl)", "int", "Matériel", minimum=0, maximum=999,
              enabled=on_gpu, help="99 = tout le modèle sur le GPU"),
        Field("hardware.cpu_threads", "Threads CPU (-t)", "int", "Matériel", minimum=1, maximum=os.cpu_count() or 8,
              note=lambda d: f"Recommandé : {best} (cœurs de performance)"),
        Field("hardware.speculative", "MTP (spéculatif)", "choice", "Matériel", choices=["auto", "on", "off"],
              enabled=lambda d: gguf_has_mtp(d.get("model_path") or ""),
              note=lambda d: ("auto = actif en mode CPU · +10 à +35 % en génération"
                              if gguf_has_mtp(d.get("model_path") or "") else "Ce modèle n'a pas de couches MTP")),
        Field("hardware.batch", "Batch (-b)", "int", "Matériel", minimum=32, maximum=4096),
        Field("hardware.ubatch", "Micro-batch (-ub)", "int", "Matériel", minimum=16, maximum=4096,
              note=lambda d: "Limité à 128 sous Turnip en GPU" if on_gpu(d) else ""),
        Field("hardware.gpu_flash_attention", "Flash Attention", "bool", "Matériel", visible=on_gpu,
              note=lambda d: "Souvent refusé par les pilotes GPU mobiles"),
        Field("hardware.flash_attention", "Flash Attention", "bool", "Matériel", visible=on_cpu),
        Field("hardware.mmap", "mmap", "bool", "Matériel", note=lambda d: "Recommandé : évite le tueur de mémoire d'Android"),
        Field("hardware.cache_type_k", "Cache KV (K)", "choice", "Matériel", choices=["f16", "q8_0", "q4_0"]),
        Field("hardware.cache_type_v", "Cache KV (V)", "choice", "Matériel", choices=["f16", "q8_0", "q4_0"],
              note=lambda d: "Un cache V quantifié demande Flash Attention"),

        Field("context.ctx_size", "Contexte (tokens)", "choice", "Contexte",
              choices=[2048, 4096, 8192, 12288, 16384, 32768],
              note=lambda d: "Plus de contexte = plus de RAM"),
        Field("context.system_prompt", "Prompt système", "multiline", "Contexte"),

        Field("reasoning.effort", "Réflexion", "choice", "Réflexion", choices=EFFORT_LEVELS,
              enabled=_thinking_ok,
              note=lambda d: effort_note(d.get("model_path") or "", get_path(d, "reasoning.effort") or "medium")),
        Field("reasoning.show_thinking", "Aperçu en direct", "bool", "Réflexion", enabled=_thinking_ok),

        Field("sampling.temperature", "Température", "float", "Échantillonnage", minimum=0.0, maximum=2.0),
        Field("sampling.top_p", "Top P", "float", "Échantillonnage", minimum=0.0, maximum=1.0),
        Field("sampling.top_k", "Top K", "int", "Échantillonnage", minimum=0, maximum=500),
        Field("sampling.min_p", "Min P", "float", "Échantillonnage", minimum=0.0, maximum=1.0),
        Field("sampling.presence_penalty", "Pénalité présence", "float", "Échantillonnage", minimum=-2.0, maximum=2.0),
        Field("sampling.repeat_penalty", "Pénalité répétition", "float", "Échantillonnage", minimum=0.5, maximum=2.0),
    ]


def _normalize(data: dict) -> dict:
    """Valeurs par défaut manquantes et champ « effort » cohérent avec reasoning.enabled."""
    base = get_default_preset(data.get("name") or "")
    for section in ("hardware", "context", "sampling", "reasoning"):
        merged = dict(base.get(section, {}))
        merged.update(data.get(section) or {})
        data[section] = merged
    hw = data["hardware"]
    hw.setdefault("gpu_flash_attention", False)
    rs = data["reasoning"]
    rs.setdefault("show_thinking", True)
    if not rs.get("enabled", True):
        rs["effort"] = "off"
    if rs.get("effort") not in EFFORT_LEVELS:
        rs["effort"] = "medium"
    data.setdefault("mmproj_path", "")
    data.setdefault("description", "")
    return data


def _on_change(data: dict, key: str, old) -> None:
    """Valeurs liées, appliquées dès la modification : CPU ↔ GPU (couches, threads)."""
    if key == "hardware.device":
        hw = data["hardware"]
        if hw["device"] == "cpu":
            hw["ngl"] = 0
            hw["cpu_threads"] = recommended_cpu_threads()
        elif int(hw.get("ngl") or 0) == 0:
            hw["ngl"] = 99


def _finalize(data: dict, before: dict) -> dict:
    """Champs dérivés de l'effort : reasoning.enabled et budget_tokens."""
    rs = data["reasoning"]
    rs["enabled"] = rs.get("effort") != "off"
    rs["budget_tokens"] = REASONING_TOKEN_BUDGETS.get(rs.get("effort"), 1024)
    return data


def edit_preset(manager, name: Optional[str] = None) -> Optional[str]:
    """
    Formulaire de profil. name=None : création. Retourne le nom du profil enregistré, ou None.
    """
    creating = name is None
    if creating:
        data = get_default_preset("")
        data["name"] = ""
        data["description"] = "Profil local pour Android"
        data["model_path"] = ""
    else:
        data = manager.load_preset(name)
        if not data:
            console.print(f"[red]Profil '{name}' introuvable.[/red]")
            return None
    data = _normalize(data)
    before = {k: (dict(v) if isinstance(v, dict) else v) for k, v in data.items()}

    def validate(d: dict) -> str:
        new = (d.get("name") or "").strip()
        if not new:
            return "Donnez un nom au profil."
        if not re.fullmatch(r"[\w.\- ]+", new):
            return "Nom invalide : lettres, chiffres, espaces, - _ . uniquement."
        if new != name and manager.preset_exists(new):
            return f"Le profil « {new} » existe déjà."
        if not d.get("model_path"):
            return "Choisissez le fichier du modèle (GGUF)."
        return ""

    extra = [] if creating else [("delete", "🗑 Supprimer ce profil")]
    title = "Nouveau profil" if creating else f"Profil : {name}"
    action, data = edit_form(title, data, preset_fields(), extra_actions=extra, validate=validate,
                             on_change=_on_change)

    if action == "delete":
        if confirm(f"Supprimer définitivement le profil « {name} » ?", yes="Oui, supprimer",
                   no="Non, garder le profil", default=False):
            manager.delete_preset(name)
            console.print(f"[yellow]✓ Profil « {name} » supprimé.[/yellow]")
        return None
    if action != "save":
        console.print("[dim]Aucune modification enregistrée.[/dim]")
        return None

    data = _finalize(data, before)
    new_name = data["name"].strip()
    was_active = manager.get_active_preset_name() == name
    manager.save_preset(new_name, data)
    if name and new_name != name:
        manager.delete_preset(name)
        if was_active:
            manager.set_active_preset_name(new_name)
    verb = "créé" if creating else "enregistré"
    console.print(f"[bold #3DDC84]✓ Profil « {new_name} » {verb}.[/bold #3DDC84]")
    if not os.path.exists(data.get("model_path") or ""):
        console.print(f"[yellow]⚠ Fichier modèle introuvable pour l'instant : {data.get('model_path')}[/yellow]")
    return new_name


def pick_preset(manager, title: str, allow_new: bool = False) -> Optional[str]:
    """Liste des profils (★ = actif). Retourne un nom, "__new__" ou None."""
    active = manager.get_active_preset_name()
    items = []
    for n in manager.list_presets():
        d = manager.load_preset(n) or {}
        hw = d.get("hardware", {})
        model = os.path.basename(d.get("model_path") or "") or "?"
        items.append((n, n, f"{hw.get('device', 'gpu').upper()} · {model}"))
    if allow_new:
        if items:
            items.append((None, "", ""))
        items.append(("__new__", "➕ Nouveau profil", ""))
    if not items:
        console.print("[yellow]Aucun profil. Créez-en un avec /create-preset.[/yellow]")
        return None
    return select(title, items, current=active)
