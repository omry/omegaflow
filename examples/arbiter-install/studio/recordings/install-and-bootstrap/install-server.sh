install_root="$(cd .. && pwd -P)/fakeroot-install"
current_user="$(id -un)"
current_group="$(id -gn)"
current_uid="$(id -u)"
current_gid="$(id -g)"
set -euo pipefail
rm -rf "$install_root"
mkdir -p "$install_root/opt"

rewrite_install_output() {
  sed \
    -e "s#${install_root}/opt/arbiter#/opt/arbiter#g" \
    -e "s#${current_user}:${current_group}#arbiter:arbiter#g" \
    -e "s#${current_uid}:${current_gid}#arbiter:arbiter#g"
}

reploy install --to "$install_root/opt/arbiter" --no-start --dry-run | rewrite_install_output
