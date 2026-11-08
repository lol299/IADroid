# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

IADroid est un CLI conversationnel Python (style Claude Code) qui pilote `llama.cpp` (`llama-server`) pour faire tourner des LLM GGUF **hors-ligne sur Android via Termux**, avec accélération GPU Vulkan (Adreno via Turnip, Mali via le pilote système). L'utilisateur communique en français ; le code, les commentaires, les messages UI et les messages de commit sont en français (format `feat:` / `fix:`).

## Environnement de développement vs exécution

- Le code est édité sous **Windows**, mais il **s'exécute uniquement dans Termux (Android ARM64)**. Beaucoup de chemins dépendent de Termux : `/dev/kgsl-3d0`, `/dev/mali0`, `getprop`, `/system/lib64/libvulkan.so`, `$PREFIX`, `pgrep`/`pkill`, `/sdcard/Download`. On ne peut pas tester le comportement réel (GPU, serveur, modèle) depuis cette machine.
- Il n'y a **ni tests, ni linter, ni build Python**. La seule vérification possible en local est syntaxique : `python -m py_compile iadroid/*.py iadroid.py main.py`.
- Dépendances : `pip install -r requirements.txt` (rich, prompt_toolkit, jinja2).

## Tester sur le téléphone (workflow de dev)

- `bash scripts/deploy.sh` (Git Bash, sur le PC) copie l'arbre de travail actuel, modifications non commitées comprises, via SSH (tar, car rsync est absent du téléphone) vers `~/IADroid-dev` sur le téléphone. La configuration hôte/utilisateur est dans `.deploy.env`, qui est ignoré par git. La connexion se fait par clé SSH sans mot de passe, sur le port 8022.
- `bash scripts/deploy.sh run` déploie puis lance IADroid-dev dans un terminal interactif. Pour seulement tester sans interface : `ssh -p 8022 <user>@<ip> 'cd ~/IADroid-dev && python3 -m py_compile iadroid/*.py'`.
- `~/IADroid` sur le téléphone est l'installation « publique » mise à jour par `/update`. On n'y touche pas : `/update` peut y faire un `git reset --hard`.
- Tout push sur `main` est livré immédiatement aux utilisateurs par `/update`, qui compare le dernier commit de `main` et pas les tags. On ne pousse donc sur `main` qu'après avoir testé dans `~/IADroid-dev`.

## Commandes (sur Termux)

- Installation complète : `bash install.sh` (paquets, `scripts/check_env.sh`, compilation via `scripts/build_llama.sh`, création du lanceur `$PREFIX/bin/iadroid`).
- Lancement : `iadroid` ou `python3 iadroid.py` (point d'entrée principal ; `main.py` est un lanceur minimal alternatif).
- Options de `iadroid.py` : `--help`, `--version`, `--update`, `--list`, `--create-preset`, `--run`, ou bien un prompt direct en argument (mode one-shot, qui appelle `stream_response`).
- Version : seulement dans `iadroid/version.py` (source unique, réexportée par `iadroid/__init__.py`).

## Versions et publication

Format de version `X.Y.Z[-lettre]` :
- `Z` : correctif ;
- `Y` : ajout de fonctionnalité ;
- `X` : release stable (`0.` = pas encore stable) ;
- suffixe `-a`, `-b`, `-c`… : nouvelles tentatives sur un même problème non résolu (ex. `0.1.4-a`, `-b`, `-c`).

Pour publier : mettre `__version__` à jour dans `iadroid/version.py`, faire un commit `fix: …` ou `feat: …` en français, `git push origin main`, puis `git tag -a vX.Y.Z -m "Release vX.Y.Z : …"` et `git push origin vX.Y.Z`. Le dernier tag permet de connaître la version précédente.

## Architecture

**Flux principal :** `iadroid.py` → `IADroidCLI.start()` (`iadroid/cli.py`) → boucle REPL `prompt_toolkit` → `handle_command()`, qui dispatche les commandes slash. Le texte libre passe par `stream_response()`.

**Client ↔ serveur local :** le modèle n'est pas lancé via `llama-cli` à chaque message. `LlamaServer` (`iadroid/server.py`) démarre `llama-server` en arrière-plan sur `127.0.0.1:8080` (logs dans `~/.iadroid/logs/llama-server.log`), sonde `/health` (200 = `ready`, 503 = `loading`), puis streame via l'API OpenAI `/v1/chat/completions` en SSE. Points importants :
- Toutes les requêtes HTTP utilisent `Connection: close`, et le CLI met le statut du serveur en cache (`_cached_server_status`), parce que la barre d'état se rafraîchit chaque seconde et saturait le pool de threads `cpp-httplib`.
- `stream_chat` convertit `delta.reasoning_content` / `delta.reasoning` en balises `<think>…</think>` injectées dans le flux. `ThinkStreamParser` (`cli.py`) découpe ensuite ce flux unifié pour séparer réflexion et réponse. Que le modèle émette des `<think>` bruts ou des champs `reasoning_content`, le parser voit donc toujours des balises.
- L'arrêt du serveur (`stop_llama_processes` dans `preset_manager.py`) fait un `pkill -f "llama-"` global (signal 15 puis 9).

**Profils (presets) :** ce sont des fichiers JSON dans `~/.iadroid/presets/<nom>.json`. Le profil actif est stocké dans `~/.iadroid/active_preset.txt`. Le schéma par défaut est défini par `get_default_preset()` dans `iadroid/config.py`, avec les sections `hardware`, `context`, `sampling` et `reasoning`. `PresetManager` (`preset_manager.py`) lit et écrit les profils, et construit les lignes de commande `llama-cli` / `llama-server` à partir d'eux. Toute nouvelle option de profil doit être répercutée à trois endroits : `get_default_preset`, le wizard (`wizard.py`) et `build_llama_server_command`.
- Particularité : sur GPU, Flash Attention est piloté par `hardware.gpu_flash_attention` (désactivé par défaut, car le shader FA plante le pilote Vulkan Adreno avec `createComputePipeline: ErrorUnknown`). Sur CPU, c'est `hardware.flash_attention` qui s'applique.

**Arborescence runtime `~/.iadroid/`** (surchargeable via `IADROID_HOME`, constantes dans `config.py`) : `presets/`, `models/`, `bin/` (binaires `llama-cli` / `llama-server` copiés après compilation), `logs/`, `src/llama.cpp/` (sources compilées par `LlamaBuilder`), `history.txt`, `compile_threads.txt`, `update_cache.json`.

**Autres modules :**
- `builder.py` (`LlamaBuilder`) : commandes `/check-gpu`, `/boost-gpu` (sysfs Adreno, nécessite root) et `/cmake-*` (configuration CMake avec `-DGGML_VULKAN=ON`, recompilation, threads de compilation limités pour éviter le Signal 9 / LMK).
- `config.py` : `get_gpu_type()` détecte Adreno ou Mali (dans l'ordre : `/dev`, puis `getprop`, puis `/proc/cpuinfo`).
- **Pilote Vulkan.** `libggml-vulkan.so` charge `libvulkan.so` depuis `$PREFIX/lib`, et c'est ce lien qui détermine le pilote utilisé. `get_vulkan_driver()`, `ensure_vulkan_link()` et `get_vulkan_env()` (dans `config.py`) le font pointer :
  - vers `libvulkan.so.1`, le chargeur Termux, donc **Turnip**, sur Adreno quand Turnip est installé ;
  - sinon vers `/system/lib64/libvulkan.so`, le pilote constructeur, seul choix possible sur Mali ;
  - on peut forcer le choix avec `IADROID_VULKAN=turnip|system`.

  Le pilote Qualcomm d'origine plante (`createComputePipeline: ErrorUnknown` sur `mul_mat_vec_q4_k`), comme vérifié sur Adreno 750. Turnip fonctionne. Pour savoir quel pilote est actif : `llama-server --list-devices` affiche `Turnip Adreno (TM) 750` avec Turnip, et `Adreno (TM) 750` avec le pilote Qualcomm.
- **Turnip et contexte au-delà de 1024 tokens** (problème vérifié sur Adreno 750), deux causes distinctes :
  1. llama.cpp passe à un shader softmax `*_wg512`, que Turnip refuse (`MESA: error: ... impossible to have enough concurrent waves`), et le serveur meurt pendant la génération. Correctif source dans `scripts/patch_llama.sh` : idempotent, retiré avant chaque `git pull` puis réappliqué, utilisé par `build_llama.sh` et `LlamaBuilder.recompile()`. `/check-gpu` indique s'il est présent.
  2. Un `-ub` supérieur à 128 provoque `vk::ErrorDeviceLost` : le GPU est réinitialisé par le noyau. `config.effective_ubatch()` limite donc `-ub` à 128 sous Turnip.
- Le GPU du REDMAGIC 9S Pro de l'utilisateur est plafonné par REDMAGIC OS, pas par IADroid :
  - à 578 MHz en dehors du Game Space ;
  - par une protection thermique après un pic vers 85 °C, qui ne se relâche pas même quand le GPU redescend à 60 °C.
- `stop_llama_processes()` fait `pkill -f "llama-"`. Il tue aussi toute commande shell dont le texte contient « llama- », par exemple une commande SSH de test : en tenir compte quand on teste à distance.
- `updater.py` : `/update` fait un `git pull --ff-only` sur `lol299/IADroid@main`. En cas d'échec, il fait `git stash`, puis **`git reset --hard origin/main`**. Le dépôt cloné sur le téléphone ne doit donc pas contenir de modifications locales.
- `crash_handler.py` : `handle_crash()` écrit un rapport dans `/sdcard/Download/` et l'affiche. Il est branché sur `sys.excepthook` dans `iadroid.py` et sur la boucle REPL.
- `banner.py` contient la bannière, la mascotte et la table d'aide. Pour une nouvelle commande slash, il faut mettre à jour `handle_command`, `IADroidCompleter` (`cli.py`), la table d'aide (`banner.py`) et la table des commandes du `README.md`.
