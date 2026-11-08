# 🤖 IADroid - Local AI Assistant for Android with Termux

<div align="center">

<img src="assets/logo.svg" width="160" alt="IADroid Logo" />

# IADroid 🤖⚡

### Assistant IA Local & Performant pour Android avec Termux
*Moteur natif `llama.cpp` ARM64 • Accélération GPU Vulkan 1.4 (Turnip Adreno & ARM Mali) • Mémoire Unifiée LPDDR*

[![Vibe Coding](https://img.shields.io/badge/Built%20with-Vibe%20Coding%20%E2%9A%A1-ff007f?style=for-the-badge&logo=openai&logoColor=white)](https://en.wikipedia.org/wiki/Vibe_coding)
[![Platform](https://img.shields.io/badge/Platform-Android%20%7C%20Termux-3DDC84?style=for-the-badge&logo=android&logoColor=white)](https://f-droid.org/packages/com.termux/)
[![Engine](https://img.shields.io/badge/Engine-llama.cpp%20ARM64-blue?style=for-the-badge)](https://github.com/ggerganov/llama.cpp)
[![GPU](https://img.shields.io/badge/GPU-Vulkan%201.4%20(Turnip%20Adreno%20%26%20Mali)-red?style=for-the-badge&logo=vulkan&logoColor=white)](https://vulkan.org)
[![Python](https://img.shields.io/badge/Python-3.10%2B-yellow?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

</div>

> ⚡ **Projet propulsé par le Vibe Coding :** Ce projet a été entièrement conçu, architecturé et programmé en mode **Vibe Coding** assisté par Intelligence Artificielle. L'objectif : créer un environnement CLI élégant inspiré de *Claude Code* et *Antigravity*, tout en exploitant au maximum le silicium de votre smartphone via Vulkan 1.4, **100% hors-ligne et sans aucun abonnement**.

<details>
<summary>👾 <i>Afficher la mascotte Bugdroid Pixel-Art du CLI</i></summary>

```text
       ██                  ██
     ██████████████████████████
     ████  ██████████████  ████
     ██████████████████████████

   ██  ██████████████████████  ██
   ██  ██████████████████████  ██
   ██  ██████████████████████  ██
       ██████████████████████
         ██████      ██████
         ██████      ██████
```
</details>

**IADroid** est un environnement d'exécution et un CLI conversationnel moderne inspiré de *Claude Code* et *Antigravity CLI (agy)*, conçu pour exécuter des modèles de langage (LLM) et multimodaux (Vision) directement et **100% hors-ligne sur smartphone Android via Termux**, avec accélération matérielle native **GPU Vulkan** (Qualcomm Adreno & ARM Mali) et **CPU ARM64**.

---

## ✨ Fonctionnalités Clés

* 🚀 **Accélération Matérielle GPU Vulkan Native :** Déchargement complet des couches sur le GPU Qualcomm Adreno (pilote Turnip Mesa Freedreno) ou ARM Mali (MediaTek Dimensity / Exynos) via Vulkan 1.4.
* 🧠 **Gestion Optimisée de la RAM Unifiée (UMA) :** Configuration pensée pour Android évitant l'OOM Killer (`mmap`, désactivation sécurisée de `mlock`, contrôle Flash Attention `/fa on|off`).
* 💭 **Gestion Avancée du Raisonnement (Thinking) :** Prise en charge native des modèles de réflexion (DeepSeek R1, Qwen 2.5, Gemma, etc.) avec streaming en direct, fenêtre glissante de 5 mots (`/show-think on`), ou masquage discret (`/show-think off`).
* ⚡ **Diffusion en Streaming Token-par-Token :** Affichage de la réponse en direct via l'API locale OpenAI `/v1/chat/completions` avec gestion hybride de `reasoning_content` et `content`.
* 📊 **Barre d'État Inférieure Dynamique :** Affichage en temps réel de la vitesse en `t/s`, du ratio de contexte tokens utilisé, de la RAM système disponible/occupée et du statut du serveur (`● Prêt (VRAM)`, `⏳ Chargement...`, `○ Arrêté`).
* 📁 **Gestionnaire de Profils (Presets) :** Créez, configurez et basculez entre plusieurs modèles GGUF en un éclair (`/create-preset`, `/load`, `/profil_config`).
* 🛑 **Contrôle Total de la Mémoire :** Démarrage transparent de `llama-server` en arrière-plan et libération instantanée de la RAM et de la VRAM (`/stop`).
* 🛠️ **Suite de Maintenance CMake Intégrée :** Diagnostic GPU automatique (`/check-gpu`), boost de fréquence (`/boost-gpu`), gestionnaire de threads anti-crash Signal 9 (`/cmake-thread-compile`) et recompilation assistée (`/cmake-recompiler`).

---

## 📋 Prérequis

1. **Termux :** Téléchargez impérativement Termux depuis **[F-Droid](https://f-droid.org/packages/com.termux/)** ou [GitHub Releases](https://github.com/termux/termux-app/releases). *(Ne pas utiliser la version Google Play Store qui est obsolète depuis 2020)*.
2. **Autorisation de Stockage :** Accordez l'accès au stockage Android pour charger vos fichiers `.gguf` situés dans vos dossiers partagés (`/sdcard/Download`, etc.) :
   ```bash
   termux-setup-storage
   ```

---

## 🚀 Installation Rapide (Depuis GitHub)

Dans Termux, lancez l'installation en une seule ligne :

```bash
pkg update -y && pkg install -y git && git clone https://github.com/lol299/IADroid.git ~/IADroid && cd ~/IADroid && bash install.sh
```

Ou étape par étape :

```bash
# 1. Mettre à jour Termux et installer Git
pkg update -y && pkg install -y git

# 2. Cloner le dépôt officiel IADroid
git clone https://github.com/lol299/IADroid.git ~/IADroid

# 3. Lancer l'installateur interactif
cd ~/IADroid
chmod +x install.sh scripts/*.sh iadroid.py
bash install.sh
```

L'installateur automatique va :
1. Installer l'environnement de compilation (`clang`, `cmake`, `ninja`, `git`).
2. Installer les bibliothèques GPU Vulkan (`vulkan-loader`, `vulkan-headers`, `shaderc`, `spirv-tools`, etc.).
3. Installer les dépendances Python du CLI (`rich`, `prompt_toolkit`, `jinja2`).
4. Analyser votre matériel (`check_env.sh` - GPU Adreno/Mali, RAM, cœurs).
5. Compiler `llama.cpp` avec support Vulkan optimisé pour ARM64.
6. Créer la commande globale `iadroid`.

---

## 🎮 Lancement & Utilisation

Une fois l'installation terminée, tapez simplement depuis n'importe où dans Termux :

```bash
iadroid
```

Vous arrivez sur l'interface épurée avec la mascotte Bugdroid, la ligne de saisie minimaliste `> ` et la barre d'état dynamique :

```text
                                                        ● medium · /effort
────────────────────────────────────────────────────────────────────────
> Bonjour ! Présente-toi brièvement.
────────────────────────────────────────────────────────────────────────
 ⚡ 18.2 t/s | 🧠 Ctx: 240/8192 (2.9%) | 💾 RAM: 8.5G (56%)/15.0G | ● Prêt (VRAM)
 ❯❯ Profil: Qwen2.5-7B · GPU · Tapez /help · /stop (VRAM)
```

---

## ⚡ Référence Complète des Commandes Slash

| Commande | Arguments | Description |
| :--- | :--- | :--- |
| **`/load`** | `<nom>` | Sélectionne un profil et le charge immédiatement en RAM/VRAM pour converser. |
| **`/run`** | | Démarre le serveur local du profil actif en arrière-plan. |
| **`/stop`** *(ou `/kill`)* | | Arrête le serveur `llama-server` actif et libère instantanément la RAM et la VRAM GPU. |
| **`/think`** | `<on\|off>` | Active ou désactive rapidement le mode réflexion (Thinking). |
| **`/show-think`** | `<on\|off>` | Aperçu de la réflexion (ON : 5 mots en direct, OFF : indicateur masqué). |
| **`/effort`** | `<level>` | Règle l'effort de réflexion (`minimal`, `low`, `medium`, `high`, `xhigh`, `off`). |
| **`/device`** | `<gpu\|cpu> [ngl]` | Bascule le calcul entre GPU Vulkan et CPU ARM64 (avec couches `-ngl` optionnelles). |
| **`/fa`** | `<on\|off>` | Active ou désactive Flash Attention (`-fa`) pour le profil en cours. |
| **`/presets`** | | Affiche le tableau récapitulatif de tous les profils enregistrés. |
| **`/create-preset`** | | Assistant interactif pas à pas pour configurer un nouveau modèle GGUF. |
| **`/profil_config`** | `<nom>` | Ouvre l'assistant pré-rempli pour éditer un profil existant. |
| **`/profil_config_env`** | `<nom>` | Affiche le contenu JSON brut du profil avec coloration syntaxique. |
| **`/profil_Prompt_Systeme`** | `<nom>` | Affiche et permet d'éditer directement le prompt système du modèle. |
| **`/use`** | `<nom>` | Définit le profil actif par défaut sans le démarrer immédiatement. |
| **`/reset`** | | Réinitialise l'historique et le contexte de la conversation en cours. |
| **`/check-gpu`** | | Diagnostic GPU Vulkan (Adreno / Mali) et liaison du pilote Android dans Termux. |
| **`/boost-gpu`** | | Forcer la fréquence GPU maximale (1 000 MHz sur Adreno 750) et optimiser Turnip. |
| **`/update`** | | Vérifie et installe automatiquement les mises à jour d'IADroid depuis GitHub. |
| **`/cmake-thread-compile`** | `<2\|4\|6\|8>` | Règle le nombre de threads de compilation avec grille des risques (Signal 9). |
| **`/cmake-config-auto`** | | Reconfigure automatiquement CMake avec le support GPU Vulkan (`-DGGML_VULKAN=ON`). |
| **`/cmake-recompiler`** | | Met à jour les sources `llama.cpp` (`git pull`) et recompile proprement. |
| **`/cmake-del-cache`** | | Supprime le cache de build CMake pour repartir sur une installation propre. |
| **`/llama-cli`** | | Lance une session brute interactive avec `llama-cli` (mode expert / débug). |
| **`/help`** | | Affiche le tableau complet des commandes. |
| **`/clear`** | | Démarre une nouvelle conversation : efface l'écran et réinitialise le contexte du modèle. |
| **`/exit`** *(ou `/quit`)* | | Arrête les processus actifs et quitte IADroid. |

---

## 💭 Gestion de la Réflexion (Reasoning / Thinking)

IADroid intègre un parseur de flux streaming dédié ([`ThinkStreamParser`](iadroid/cli.py)) compatible avec les modèles avec raisonnement interne (DeepSeek-R1, Qwen 2.5, Gemma-thinking, etc.) :

### 1. Activer ou désactiver la réflexion (`/think`)
* **`/think on`** : Le modèle réfléchit avant de répondre. La réflexion est mesurée en temps réel (`💭 Réfléchi en 2.3s`).
* **`/think off`** : Désactive le mode réflexion. Les éventuels blocs de réflexion générés par le modèle sont interceptés et absorbés silencieusement pour afficher directement la réponse finale, sans afficher de temps de réflexion.

### 2. Personnaliser l'affichage en direct (`/show-think`)
* **`/show-think on`** : Affiche `💭 Réflexion en cours...` et fait défiler sur la ligne du dessous une **fenêtre glissante des 5 derniers mots** pensés par l'IA (`... mot1 mot2 mot3 mot4 mot5`). Les anciens mots disparaissent au fur et à mesure pour préserver la lisibilité du terminal.
* **`/show-think off`** : Affiche uniquement l'indicateur discret `💭 Réflexion en cours...` sans afficher de texte défilant, suivi du récapitulatif `💭 Réfléchi en Xs` une fois la réflexion achevée.

### 3. Niveaux d'effort (`/effort`)
Réglez le niveau de raisonnement via `/effort <minimal|low|medium|high|xhigh|off>`. Chaque niveau ajuste le budget indicatif de tokens alloués à la réflexion.

---

## 🏎️ Accélération GPU : Qualcomm Adreno & ARM Mali

### Qualcomm Snapdragon (Adreno 6xx, 7xx, 8xx)
Sur les puces Qualcomm, le pilote Vulkan haute performance est fourni par **Turnip (Mesa Freedreno)** :

```bash
pkg install -y mesa-vulkan-icd-freedreno
```

Vérifiez la détection matérielle dans IADroid :
```text
> /check-gpu
```
Vous devez voir :
```text
• Matériel GPU : Qualcomm Adreno détecté
• Pilote Turnip Vulkan (Adreno) : Installé et actif
deviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
deviceName = Turnip Adreno (TM) ...
```

### MediaTek Dimensity / Samsung Exynos / ARM Mali
Sur les SoC équipés d'un GPU **ARM Mali / Immortalis**, IADroid détecte automatiquement le matériel via `/dev/mali0` et les propriétés système Android, puis effectue la liaison directe avec le pilote Vulkan système (`/system/lib64/libvulkan.so`).

---

## ⚙️ Optimisation des Performances (Snapdragon 8 Gen 3 / Adreno 750)

Pour tirer le maximum de bande passante et de tokens/seconde de votre smartphone :

1. **Cache KV en `f16` :** Avec la bande passante LPDDR5X (~77 Go/s) des processeurs récents, `f16` calcule directement sans le coût de déquantification continue de `q4_0`.
2. **Micro-batch (-ub) :** Sous Turnip (Adreno), IADroid limite automatiquement `-ub` à `128` : au-delà, le GPU est réinitialisé (`ErrorDeviceLost`) dès que la conversation dépasse 1024 tokens.
3. **Threads CPU (-t) :** Réglez `-t 4` (ou `-t 2`) avec déchargement GPU complet (`-ngl 99`). Évitez de saturer tous les cœurs CPU pour ne pas entrer en compétition avec le GPU sur le bus mémoire.
4. **Optimisation Turnip (`/boost-gpu`) :** Active le mode `TU_DEBUG=noconform` et le mode de présentation `mailbox` à latence minimale.

---

## 🎮 Gestion de la Fréquence GPU & Protection Thermique CPU

Sur les SoC Qualcomm Snapdragon :
* Au repos ou hors jeux 3D, le gouverneur Android régule le GPU au palier d'économie **578 MHz**.
* **Pourquoi éviter les modes de surcadençage global (ex: Mode Diablo) :** Ces modes forcent les cœurs CPU Cortex-X4 à pleine fréquence en continu, dégageant plus de 15 Watts de chaleur inutile. Cela provoque un **thermal-throttling sévère** qui fait chuter la vitesse du GPU.
* **Déverrouillage matériel avec Root (`/boost-gpu`) :** Si votre téléphone dispose des droits `su` (Magisk, KernelSU, APatch), `/boost-gpu` déverrouille le palier `thermal_pwrlevel` de Qualcomm, verrouille l'Adreno 750 à **1 000 MHz** et **bride automatiquement les cœurs CPU** en mode tempéré pour concentrer 100% du budget thermique sur le GPU.
* **Sans Root :** Le noyau Android alloue dynamiquement les paliers de fréquence GPU selon la charge de calcul Vulkan.

---

## ⚡ Architecture : Moteur Serveur Local Dédié (`llama-server`)

IADroid s'appuie sur une architecture **Client ↔ Serveur local** via `llama-server` :
1. **Zéro pollution visuelle :** Pas d'art ASCII brut ni de logs système encombrants.
2. **Persistance en mémoire :** Le modèle GGUF est chargé **une seule fois** en VRAM / RAM. Les messages suivants répondent instantanément.
3. **Gestion anti-famine HTTP :** Fermeture systématique des sockets (`Connection: close`) et mise en cache du statut du serveur pour éviter la saturation du pool de threads `cpp-httplib`.
4. **Streaming hybride robuste :** Prise en charge transparente de `choices[0].delta.reasoning_content` et `choices[0].delta.content`.
5. **Gestionnaire de crash intégré :** En cas d'erreur ou d'exception non gérée, un rapport de diagnostic complet est généré automatiquement dans votre dossier Téléchargements Android (`/sdcard/Download/`).

---

## 📁 Arborescence du Projet

```text
~/IADroid/
├── iadroid/
│   ├── __init__.py           # Version et métadonnées du package
│   ├── banner.py             # Mascotte Android pixel Bugdroid & bannières
│   ├── builder.py            # Maintenance CMake, compilateur & diagnostic GPU
│   ├── cli.py                # Interface REPL interactive style Claude Code
│   ├── config.py             # Chemins (~/.iadroid), constantes et détection GPU/RAM
│   ├── crash_handler.py      # Gestionnaire d'erreurs et rapports de crash
│   ├── preset_manager.py     # Gestion JSON des profils, matériel & processus
│   ├── server.py             # Moteur llama-server en arrière-plan & client SSE
│   ├── updater.py            # Système de mise à jour automatique via GitHub (/update)
│   ├── version.py            # Numéro de version officiel d'IADroid
│   └── wizard.py             # Assistant interactif (/create-preset, /profil_config)
├── scripts/
│   ├── build_llama.sh        # Script de compilation autonome llama.cpp
│   └── check_env.sh          # Script de diagnostic matériel (ARM64, Vulkan, RAM)
├── install.sh                # Installateur automatisé pour Termux
├── iadroid.py                # Point d'entrée exécutable principal
├── main.py                   # Lanceur alternatif
├── requirements.txt          # rich, prompt_toolkit, jinja2
└── README.md
```

---

## 🛠️ Dépannage Fréquent (FAQ)

### 1. `Permission denied` lors de l'accès au modèle ou à un script
* **Pour les scripts :** Android interdit l'exécution directe (`noexec`) sur le stockage partagé (`/sdcard`). Installez toujours IADroid dans le stockage privé de Termux (`~/IADroid/`).
* **Pour les fichiers `.gguf` :** Tapez `termux-setup-storage` et vérifiez dans les Paramètres Android > Applications > Termux > Autorisations que l'option **"Autoriser la gestion de tous les fichiers"** est bien cochée.

### 2. Compilation tuée par `Signal 9` (LMK - Low Memory Killer)
Clang consomme ~1,5 Go de RAM par thread de compilation. Si trop de cœurs compilent en parallèle, la mémoire vive sature et Android tue le compilateur.  
Réglez simplement les threads de compilation sur 2 ou 4 dans IADroid :
```text
> /cmake-thread-compile 2
> /cmake-recompiler
```

### 3. Échec de shaders Vulkan (`createComputePipeline` / ErrorUnknown)
Sur certains GPU mobiles, des architectures de modèles spécifiques (ex: Gemma avec logit soft-capping / head_dim=256) ou l'option Flash Attention peuvent être rejetées par le pilote Vulkan :
* Basculez temporairement en mode CPU : `/device cpu`
* Ou désactivez Flash Attention sur le GPU : `/fa off`
* Ou déchargez moins de couches sur le GPU : `/device gpu 15`

### 4. `warning: no usable GPU found`
Si seul `llvmpipe` (CPU logiciel) est détecté sur une puce Qualcomm Snapdragon, installez le pilote Turnip puis lancez `/check-gpu` :
```bash
pkg install -y mesa-vulkan-icd-freedreno
```
IADroid choisit automatiquement le pilote Vulkan : **Turnip** sur Adreno (le pilote Qualcomm d'origine fait échouer les shaders de llama.cpp avec `createComputePipeline: ErrorUnknown`), et le **pilote système Android** sur Mali. Pour forcer un choix : `export IADROID_VULKAN=turnip` ou `export IADROID_VULKAN=system`.

### 5. Fréquence GPU bridée (ex: 578 MHz sur Adreno 750)
* **Avec Root (`su`) :** Tapez `/boost-gpu` dans IADroid pour verrouiller le GPU à sa fréquence maximale (1 000 MHz) tout en bridant le CPU pour concentrer l'énergie thermique sur le GPU.
* **Sans Root :** L'Adreno monte dynamiquement en fréquence dès que des calculs de shaders Vulkan sont envoyés. Vérifiez toujours la présence du pilote matériel Turnip avec `/check-gpu`.

---

## 👏 Remerciements & Projets Amont

IADroid s'appuie avec gratitude sur les travaux exceptionnels de la communauté open-source :
* **[llama.cpp](https://github.com/ggerganov/llama.cpp)** (par Georgi Gerganov & contributeurs - Licence MIT) : Moteur d'inférence LLM haute performance ARM64 et backend Vulkan.
* **[Mesa 3D / Freedreno Turnip](https://gitlab.freedesktop.org/mesa/mesa)** (Licence MIT) : Pilote Vulkan open-source pour puces Qualcomm Adreno.
* **[Termux](https://github.com/termux/termux-app)** (Licence GPLv3) : Environnement terminal et écosystème Linux pour Android.
* **[Rich](https://github.com/Textualize/rich)** & **[Prompt Toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit)** (Licences MIT & BSD) : Rendu visuel élégant dans le terminal et moteur REPL interactif.

---

## 📄 Licence

Ce projet est sous licence **MIT**. Voir le fichier [LICENSE](LICENSE) pour plus de détails.  
Vous êtes libre d'utiliser, modifier et distribuer ce logiciel.
