#!/bin/bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

systemd_user_dir="${HOME}/.config/systemd/user"
openchamber_env_dir="${HOME}/.config/openchamber-stack"
openchamber_env_file="${openchamber_env_dir}/env"

mkdir -p "${systemd_user_dir}" "${openchamber_env_dir}"

for service in openchamber-serve openchamber-fanin openchamber-web; do
  ensure_link "${DOTFILES_DIR}/openchamber/${service}.service" "${systemd_user_dir}/${service}.service"
done

if [[ ! -f "${openchamber_env_file}" ]]; then
  umask 077
  printf 'OPENCHAMBER_UI_PASSWORD=%s\n' "$(openssl rand -base64 18)" > "${openchamber_env_file}"
fi

chmod 600 "${openchamber_env_file}"

# User units must survive logout/reboot; without linger systemd kills the
# user manager (and this whole stack) when the last session closes.
if [ "$(loginctl show-user "$(id -un)" --property=Linger --value 2>/dev/null)" != "yes" ]; then
  loginctl enable-linger "$(id -un)"
fi
