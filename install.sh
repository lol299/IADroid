#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# IADroid - Script d'installation globale (Phase 1)
# Configure Termux, installe les dépendances et compile llama.cpp avec Vulkan
# ==============================================================================

set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
ANDROID_GREEN='\033[38;2;61;220;132m'

# Affichage de la mascotte pixel au démarrage
clear 2>/dev/null || true
echo -e "${ANDROID_GREEN}"
cat << "EOF"
       ██                  ██        
     ██████████████████████████      IADroid - Phase 1 : Installation
     ████  ██████████████  ████      ────────────────────────────────
     ██████████████████████████      Configuration de l'environnement
                                     et compilation de llama.cpp
   ██  ██████████████████████  ██    
   ██  ██████████████████████  ██    
   ██  ██████████████████████  ██    
       ██████████████████████        
           ██████      ██████        
           ██████      ██████        
EOF
echo -e "${NC}"

echo -e "${CYAN}====================================================${NC}"
echo -e "${GREEN}   Bienvenue dans l'installation d'IADroid !         ${NC}"
echo -e "${CYAN}====================================================${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IADROID_HOME="$HOME/.iadroid"

# 1. Vérification de sécurité noexec (Android /sdcard)
CURRENT_DIR="$(pwd)"
if [[ "$CURRENT_DIR" == *"/sdcard"* ]] || [[ "$CURRENT_DIR" == *"/storage/emulated"* ]] || [[ "$CURRENT_DIR" == *"$HOME/storage"* ]]; then
    echo -e "${RED}[ERREUR CRITIQUE] Emplacement non exécutable détecté !${NC}"
    echo -e "${YELLOW}Vous êtes dans : $CURRENT_DIR${NC}"
    echo -e "Android interdit strictement d'exécuter des scripts ou des binaires sur le stockage partagé (/sdcard)."
    echo -e "\nPour corriger, clonez simplement le dépôt dans votre espace privé Termux :"
    echo -e "  ${CYAN}cd ~ && git clone https://github.com/lol299/IADroid.git ~/IADroid${NC}"
    echo -e "  ${CYAN}cd ~/IADroid && bash install.sh${NC}\n"
    exit 1
fi

# 2. Configuration Git et liaison au dépôt officiel pour /update
git config --global --add safe.directory "*" 2>/dev/null || true
if [ ! -d "$SCRIPT_DIR/.git" ]; then
    echo -e "${YELLOW}[NOTE] Initialisation du lien Git vers https://github.com/lol299/IADroid.git...${NC}"
    (cd "$SCRIPT_DIR" && git init -q && git remote add origin https://github.com/lol299/IADroid.git 2>/dev/null || git remote set-url origin https://github.com/lol299/IADroid.git)
fi

# 3. Création de l'arborescence
mkdir -p "$IADROID_HOME/bin" \
         "$IADROID_HOME/presets" \
         "$IADROID_HOME/models" \
         "$IADROID_HOME/logs"

# 3. Correction automatique des permissions et shebangs
chmod +x "$SCRIPT_DIR/scripts/"*.sh "$SCRIPT_DIR/iadroid.py" "$SCRIPT_DIR/main.py" 2>/dev/null || true
if command -v termux-fix-shebang >/dev/null 2>&1; then
    termux-fix-shebang "$SCRIPT_DIR/scripts/"*.sh "$SCRIPT_DIR/iadroid.py" "$SCRIPT_DIR/main.py" "$0" 2>/dev/null || true
fi

# 4. Mise à jour des dépôts Termux et installation des paquets
echo -e "\n${CYAN}[Étape 1/4]${NC} Installation des paquets Termux (Compilateur, CMake, Vulkan, Python)..."
if ! pkg update -y; then
    echo -e "${YELLOW}[NOTE] Échec des miroirs par défaut. Basculement automatique sur le miroir stable (Grimler)...${NC}"
    if [ -n "$PREFIX" ] && [ -d "$PREFIX/etc/apt" ]; then
        echo "deb https://grimler.se/termux/termux-main stable main" > "$PREFIX/etc/apt/sources.list"
        apt update -y || true
    fi
fi
pkg install -y git cmake clang ninja vulkan-loader vulkan-headers vulkan-tools shaderc glslang spirv-headers spirv-tools python termux-tools || apt install -y git cmake clang ninja vulkan-loader vulkan-headers vulkan-tools shaderc glslang spirv-headers spirv-tools python termux-tools

# 4. Installation des bibliothèques Python pour IADroid CLI
echo -e "\n${CYAN}[Étape 2/4]${NC} Installation des dépendances Python (Rich, Prompt-Toolkit, Jinja2)..."
pip install --upgrade pip --no-warn-script-location 2>/dev/null || true
pip install rich prompt_toolkit jinja2 --no-warn-script-location

# 5. Diagnostic d'environnement
echo -e "\n${CYAN}[Étape 3/4]${NC} Exécution du diagnostic matériel..."
bash "$SCRIPT_DIR/scripts/check_env.sh"

# 6. Choix de compilation (Vulkan recommandé par défaut)
echo -e "${CYAN}[Étape 4/4]${NC} Compilation du moteur llama.cpp..."
echo -e "Quel backend souhaitez-vous compiler ?"
echo -e "  1) ${GREEN}Vulkan GPU (Recommandé - Adreno/Mali)${NC}"
echo -e "  2) ${YELLOW}CPU uniquement (NEON ARM64)${NC}"

read -r -p "Votre choix [1/2] (défaut: 1) : " CHOICE
CHOICE=${CHOICE:-1}

if [ "$CHOICE" == "2" ]; then
    echo -e "${YELLOW}[INFO]${NC} Lancement de la compilation CPU..."
    bash "$SCRIPT_DIR/scripts/build_llama.sh" --cpu
else
    echo -e "${GREEN}[INFO]${NC} Lancement de la compilation Vulkan GPU..."
    bash "$SCRIPT_DIR/scripts/build_llama.sh" --gpu
fi

# 7. Création des liens et du lanceur global 'iadroid'
chmod +x "$SCRIPT_DIR/iadroid.py" "$SCRIPT_DIR/main.py" 2>/dev/null || true
if [ -n "$PREFIX" ] && [ -d "$PREFIX/bin" ]; then
    ln -sf "$IADROID_HOME/bin/llama-cli" "$PREFIX/bin/llama-cli" 2>/dev/null || true
    ln -sf "$IADROID_HOME/bin/llama-server" "$PREFIX/bin/llama-server" 2>/dev/null || true
    
    cat << EOF > "$PREFIX/bin/iadroid"
#!/data/data/com.termux/files/usr/bin/bash
exec python3 "$SCRIPT_DIR/iadroid.py" "\$@"
EOF
    chmod +x "$PREFIX/bin/iadroid"
    echo -e "${GREEN}[OK]${NC} Commande globale installée : vous pouvez taper simplement ${CYAN}iadroid${NC} depuis n'importe où !"
fi

echo -e "\n${ANDROID_GREEN}====================================================${NC}"
echo -e "${ANDROID_GREEN}       INSTALLATION TERMINÉE AVEC SUCCÈS !           ${NC}"
echo -e "${ANDROID_GREEN}====================================================${NC}"
echo -e "• Moteur llama.cpp prêt dans : ${CYAN}$IADROID_HOME/bin/llama-cli${NC}"
echo -e "• Répertoire des profils : ${CYAN}$IADROID_HOME/presets/${NC}"
echo -e "• Répertoire des modèles : ${CYAN}$IADROID_HOME/models/${NC}"
echo -e "\nPour démarrer l'application, tapez simplement : ${GREEN}iadroid${NC}\n"
