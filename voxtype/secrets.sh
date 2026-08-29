# Export voxtype's API keys from the sops-encrypted secrets. Sourced by
# voxtype-daemon (systemd) and voxtype-retranscribe-last (COSMIC shortcut);
# neither runs in a login shell, so nothing else puts these in the environment.

MISE="${DOTFILES_DIR:-$HOME/.dotfiles}/bin/mise"

SECRETS=$("$MISE" exec sops age -- \
    sops -d --output-type dotenv "${DOTFILES_DIR:-$HOME/.dotfiles}/secrets.env")

export VOXTYPE_WHISPER_API_KEY
VOXTYPE_WHISPER_API_KEY=$(echo "$SECRETS" | grep '^GROQ_API_KEY=' | cut -d= -f2-)

export VOXTYPE_DEEPGRAM_API_KEY
VOXTYPE_DEEPGRAM_API_KEY=$(echo "$SECRETS" | grep '^DEEPGRAM_API_KEY=' | cut -d= -f2-)
