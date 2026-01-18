# Tailscale proxy environment variables
# Usage: source ~/.dotfiles/proxy.sh
#    or: . proxy.sh

export HTTP_PROXY="http://localhost:1080"
export HTTPS_PROXY="http://localhost:1080"
export http_proxy="$HTTP_PROXY"
export https_proxy="$HTTPS_PROXY"
export ALL_PROXY="socks5://localhost:1055"
export all_proxy="$ALL_PROXY"
export NO_PROXY="localhost,127.0.0.1,::1,.local"
export no_proxy="$NO_PROXY"

echo "Proxy enabled: HTTP=$HTTP_PROXY SOCKS=$ALL_PROXY"
