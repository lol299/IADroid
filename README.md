# 🤖 IADroid - Local AI Assistant for Android with Termux

<div align="center">

<img src="assets/logo.svg" width="160" alt="IADroid Logo" />

# IADroid 🤖⚡

### Assistant IA Local & Performant pour Android avec Termux
*Moteur natif `llama.cpp` ARM64 • Accélération GPU Vulkan 1.4 (Turnip Adreno / Mali) • Mémoire Unifiée LPDDR*

[![Vibe Coding](https://img.shields.io/badge/Built%20with-Vibe%20Coding%20%E2%9A%A1-ff007f?style=for-the-badge&logo=openai&logoColor=white)](https://en.wikipedia.org/wiki/Vibe_coding)
[![Platform](https://img.shields.io/badge/Platform-Android%20%7C%20Termux-3DDC84?style=for-the-badge&logo=android&logoColor=white)](https://f-droid.org/packages/com.termux/)
[![Engine](https://img.shields.io/badge/Engine-llama.cpp%20ARM64-blue?style=for-the-badge)](https://github.com/ggerganov/llama.cpp)
[![GPU](https://img.shields.io/badge/GPU-Vulkan%201.4%20(Turnip)-red?style=for-the-badge&logo=vulkan&logoColor=white)](https://vulkan.org)
[![Python](https://img.shields.io/badge/Python-3.10%2B-yellow?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

</div>

> ⚡ **Projet propulsé par le Vibe Coding :** Ce projet a été entièrement conçu, architecturé et programmé en mode **Vibe Coding** assisté par Intelligence Artificielle. L'objectif : créer un environnement CLI élégant inspiré de *Claude Code* et *Antigravity*, tout en exploitant au maximum le silicium de votre smartphone (Qualcomm Adreno 750 / Snapdragon 8 Gen 3) via Vulkan 1.4, **100% hors-ligne et sans aucun abonnement**.

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

**IADroid** est un environnement d'exécution et un CLI conversationnel moderne inspiré de *Claude Code* et *Antigravity CLI (agy)*, conçu pour exécuter des modèles de langage (LLM) et multimodaux (Vision) directement et **100% hors-ligne sur smartphone Android via Termux**, avec accélération matérielle native **GPU Vulkan** et **CPU ARM64**.

---

## ✨ Fonctionnalités Clés

* 🚀 **Accélération Matérielle GPU Vulkan Native :** Déchargement complet des couches sur le GPU Qualcomm Adreno (pilote Turnip Mesa) ou ARM Mali via Vulkan 1.4.
* 🧠 **Gestion Optimisée de la RAM Unifiée (UMA) :** Configuration pensée pour Android évitant l'OOM Killer (`mmap`, désactivation sécurisée de `mlock`, Flash Attention `-fa on`).
* ⚡ **Diffusion en Streaming Token-par-Token :** Affichage de la réponse en direct sous vos yeux avec détection et mise en page soignée des blocs de raisonnement (`<think> ... </think>`).
* 📊 **Jauge de Contexte Visuelle & T/S en Direct :** Barre d'état inférieure dynamique affichant la jauge en blocs `[████░░░░░░]`, le ratio de tokens occupés, la vitesse en `t/s`, le statut `● Prêt` et le matériel actif.
* 📁 **Gestionnaire de Profils (Presets) :** Créez, modifiez et basculez entre plusieurs modèles GGUF en un éclair (`/create-preset`, `/load`, `/profil_config`).
* 🛑 **Contrôle Total des Processus :** Détection automatique des processus `llama.cpp` orphelins, arrêt et libération instantanée de la RAM et de la VRAM (`/stop`).
* 🛠️ **Suite de Maintenance CMake Intégrée :** Reconfiguration automatique avec Vulkan, gestionnaire de threads de compilation sécurisé anti-crash Signal 9 (`/cmake-*`).

---

## 📋 Prérequis

1. **Termux :** Téléchargez impérativement Termux depuis **[F-Droid](https://f-droid.org/packages/com.termux/)** ou [GitHub Releases](https://github.com/termux/termux-app/releases). *(Ne pas utiliser la version Google Play Store qui est obsolète depuis 2020)*.
2. **Autorisation de Stockage :** Accordez l'accès au stockage Android pour charger vos fichiers `.gguf` situés dans `/sdcard/Download` :
   ```bash
   termux-setup-storage
   ```

---

## 🚀 Installation Rapide (1 Commande)

Transférez le dossier `IADroid` dans votre répertoire personnel Termux (`~`) puis lancez :

```bash
cd ~/IADroid
chmod +x install.sh scripts/*.sh iadroid.py
bash install.sh
```

L'installateur automatique va :
1. Installer l'environnement de compilation (`clang`, `cmake`, `ninja`, `git`).
2. Installer les bibliothèques GPU Vulkan (`vulkan-loader`, `vulkan-headers`, `shaderc`, `spirv-tools`, etc.).
3. Installer les dépendances Python du CLI (`rich`, `prompt_toolkit`, `jinja2`).
4. Analyser votre matériel (`check_env.sh` - GPU, RAM, cœurs).
5. Compiler `llama.cpp` avec support Vulkan optimisé pour ARM64.
6. Créer la commande globale `iadroid`.

---

## 🎮 Lancement du CLI

Une fois l'installation terminée, tapez simplement depuis n'importe où dans Termux :

```bash
iadroid
```

Vous arrivez sur l'interface épurée avec la mascotte Bugdroid, la ligne de saisie minimaliste `> ` et la barre d'état dynamique :

```text
────────────────────────────────────────────────────────────────────────
> Bonjour ! Présente-toi brièvement.
```
```text
 ● Prêt | [████░░░░░░] 240/8192 (2.9%) | ⚡ 18.2 t/s | GPU | Profil: Qwen3.5_9b
```

---

## ⚡ Référence Complète des Commandes Slash

| Commande | Syntaxe | Description |
| :--- | :--- | :--- |
| **`/load`** | `/load <nom>` | Sélectionne un profil et le charge immédiatement en RAM pour converser. |
| **`/stop`** | `/stop` *(ou `/kill`)* | Arrête tout processus `llama.cpp` actif et libère instantanément la RAM et la VRAM GPU. |
| **`/create-preset`** | `/create-preset` | Assistant interactif pas à pas pour configurer un nouveau modèle GGUF. |
| **`/profil_config`** | `/profil_config <nom>` | Ouvre l'assistant pré-rempli pour modifier un profil existant. |
| **`/profil_config_env`** | `/profil_config_env <nom>` | Affiche la configuration JSON brute du profil avec coloration syntaxique. |
| **`/profil_Prompt_Systeme`** | `/profil_Prompt_Systeme <nom>` | Affiche et permet d'éditer directement le prompt système. |
| **`/device`** | `/device <gpu\|cpu>` | Bascule instantanément le profil actif entre GPU (Vulkan) et CPU. |
| **`/presets`** | `/presets` | Affiche le tableau récapitulatif de tous les profils enregistrés. |
| **`/use`** | `/use <nom>` | Active un profil spécifique sans le lancer immédiatement. |
| **`/reset`** | `/reset` | Réinitialise la mémoire et le contexte de la conversation en cours. |
| **`/check-gpu`** | `/check-gpu` | Diagnostic GPU complet et liaison automatique du pilote Android dans Termux. |
| **`/boost-gpu`** | `/boost-gpu` | Débloque la fréquence GPU maximale (1 000 MHz pour Adreno 750) et optimise Turnip. |
| **`/cmake-thread-compile`** | `/cmake-thread-compile <2\|4\|6\|8>` | Règle le nombre de cœurs de compilation avec grille des risques de crash (Signal 9). |
| **`/cmake-config-auto`** | `/cmake-config-auto` | Reconfigure automatiquement CMake avec le support GPU Vulkan (`-DGGML_VULKAN=ON`). |
| **`/cmake-recompiler`** | `/cmake-recompiler` | Vérifie les mises à jour de llama.cpp (`git pull`) et recompile avec vos threads choisis. |
| **`/cmake-del-cache`** | `/cmake-del-cache` | Supprime le dossier de build et le cache CMake pour repartir sur une base vierge. |
| **`/run`** | `/run` | Démarre une session de chat interactive plein écran native avec `llama-cli`. |
| **`/help`** | `/help` | Affiche le tableau d'aide des commandes. |
| **`/clear`** | `/clear` | Nettoie l'écran et réaffiche la bannière IADroid. |
| **`/exit`** | `/exit` | Quitte IADroid. |

---

## 🏎️ Accélération GPU : Qualcomm Adreno & Turnip

Sur Android, le pilote Vulkan matériel pour les puces **Qualcomm Snapdragon (Adreno 6xx, 7xx, 8xx)** est fourni par **Turnip (Mesa Freedreno)**.

Pour activer l'accélération matérielle Adreno à 100% :
```bash
pkg install -y mesa-vulkan-icd-freedreno
```

Vérifiez ensuite dans IADroid avec :
```text
> /check-gpu
```
Vous devez voir :
```text
deviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
deviceName = Turnip Adreno (TM) ...
```

---

## ⚙️ Optimisation des Performances (Snapdragon 8 Gen 3 / Adreno 750)

Pour tirer le maximum de bande passante et de tokens/seconde de votre SoC :

1. **Cache KV en `f16` (au lieu de `q4_0`) :**
   Avec les ~77 Go/s de bande passante LPDDR5X des processeurs récents, la déquantification continue de `q4_0` ralentit le calcul. Le mode `f16` calcule directement sans surcharge.
2. **Micro-batch (-ub) :** Réglez `-ub 256` ou `-ub 512` pour saturer tous les cœurs de calcul du GPU.
3. **Threads CPU (-t) :** Mettez `-t 4` (ou `-t 2`) avec déchargement GPU complet (`-ngl 99`). Ne mettez pas 8 threads : le CPU entrerait en compétition avec le GPU sur le bus mémoire et ferait chauffer le téléphone.
4. **Optimisation du pilote Turnip :** Tapez `/boost-gpu` pour activer le mode `noconform` et le mode de présentation `mailbox` à latence minimale.

---

## 🎮 Gestion de la Fréquence GPU & Protection Thermique CPU

Sur les SoC Qualcomm Snapdragon (comme le Snapdragon 8 Gen 3 / Adreno 750) :
* Par défaut au repos ou hors jeux lourds, le noyau Android et le démon thermique régulent l'Adreno au palier de sécurité **578 MHz** (~6 t/s).
* **Pourquoi éviter les modes de surcadençage global (ex: Mode Diablo) :** Ces modes forcent les cœurs CPU Cortex-X4 à pleine fréquence en continu, dégageant plus de 15 Watts de chaleur inutile. Cela provoque un **thermal-throttling sévère** qui fait chuter la vitesse du GPU.
* **Déverrouillage matériel avec Root (`/boost-gpu`) :**  
  Si votre téléphone dispose des droits `su` (Magisk, KernelSU, APatch), la commande `/boost-gpu` déverrouille le palier `thermal_pwrlevel` de Qualcomm, verrouille l'Adreno 750 à **1 000 MHz** et **bride automatiquement les cœurs CPU** en mode tempéré pour concentrer 100% du budget thermique sur le GPU.
* **Sans Root :**  
  Le noyau Android alloue dynamiquement les paliers de fréquence GPU en fonction de la charge de calcul Vulkan. IADroid active automatiquement les flags de performance Turnip (`TU_DEBUG=noconform`).

---

## ⚡ Architecture : Moteur Serveur Local Dédié (`llama-server`)

IADroid intègre une architecture **Client ↔ Serveur local** via `llama-server` :
1. **Zéro pollution visuelle :** Vous ne voyez jamais l'art ASCII brut ou le prompt rustique de `llama-cli`.
2. **Persistance en mémoire :** Le modèle GGUF est chargé **une seule fois** en VRAM GPU / RAM. Les questions suivantes répondent instantanément sans temps de rechargement.
3. **Streaming fluide Token-par-Token :** Les tokens sont reçus en temps réel via l'API REST locale (`/v1/chat/completions`) et affichés sous le prompt minimaliste `>`.
4. **Gestion des blocs de pensée :** Les balises de raisonnement (`<think> ... </think>`) sont détectées et formatées avec élégance en italique avant la réponse finale.
5. **Contrôle total (`/run`, `/stop`) :** Démarrez ou arrêtez le serveur en un clin d'œil pour libérer immédiatement la RAM et la VRAM.

---

## 📁 Arborescence du Projet

```text
~/IADroid/
├── iadroid/
│   ├── __init__.py           # Version et métadonnées
│   ├── banner.py             # Mascotte Android pixel Bugdroid & bannières
│   ├── builder.py            # Maintenance CMake, compilateur & diagnostic GPU
│   ├── cli.py                # Interface REPL interactive style Claude Code
│   ├── config.py             # Chemins (~/.iadroid), constantes et mémoire unifiée
│   ├── preset_manager.py     # Gestion JSON des profils, processus & flags llama-cli
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

### 1. `Permission denied` lors de l'exécution d'un script
Android interdit l'exécution directe (`noexec`) sur le stockage partagé (`/sdcard`).  
Assurez-vous que le dossier se trouve bien dans votre répertoire privé Termux :
```bash
cp -r /sdcard/Download/IADroid ~/
cd ~/IADroid
bash install.sh
```

### 2. Compilation tuée par `Signal 9` (LMK - Low Memory Killer)
Clang consomme ~1,5 Go de RAM par thread de compilation. Si trop de cœurs compilent en parallèle, la mémoire vive sature et Android tue le processus.  
Réglez simplement les threads de compilation sur 2 ou 4 dans IADroid :
```text
> /cmake-thread-compile 2
> /cmake-recompiler
```

### 3. `warning: no usable GPU found`
Si seul `llvmpipe` est détecté, installez le pilote Turnip matériel :
```bash
pkg install -y mesa-vulkan-icd-freedreno
ln -sf /system/lib64/libvulkan.so $PREFIX/lib/libvulkan.so
```

### 4. Fréquence GPU bridée (ex: 578 MHz sur Adreno 750)
Par défaut, le noyau Android et le gouverneur de devfreq réduisent la fréquence d'horloge GPU pour économiser la batterie.
* **Avec Root (`su`) :** Tapez `/boost-gpu` dans IADroid pour verrouiller le GPU à sa fréquence maximale (1 000 MHz) tout en bridant le CPU pour éviter tout échauffement.
* **Sans Root :** L'Adreno monte dynamiquement en fréquence dès que des calculs de shaders Vulkan lourds sont envoyés. Vérifiez toujours la présence du pilote matériel Turnip avec `/check-gpu`.

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
