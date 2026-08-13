#!/usr/bin/env bash
# Shared helpers: create sshchat-federation system user on Linux and macOS,
# and resolve the live authorized_keys path for inbound federation SSH.

is_darwin() { [[ "$(uname -s)" == "Darwin" ]]; }

primary_group_of() {
  local user_name=$1
  id -gn "$user_name" 2>/dev/null || printf '%s' "$user_name"
}

darwin_next_uid() {
  local max=500 uid _name
  while read -r _name uid; do
    [[ "$uid" =~ ^[0-9]+$ ]] || continue
    ((uid > max)) && max=$uid
  done < <(dscl . -list /Users UniqueID 2>/dev/null || true)
  echo $((max + 1))
}

darwin_staff_gid() {
  local g
  g=$(dscl . -read /Groups/staff PrimaryGroupID 2>/dev/null | awk '{print $2}' || true)
  if [[ -n "$g" && "$g" =~ ^[0-9]+$ ]]; then
    printf '%s' "$g"
    return
  fi
  printf '%s' "20"
}

_ensure_federation_user_linux() {
  local fed_user=$1 fed_home=$2 cur_home
  if ! id "$fed_user" &>/dev/null; then
    mkdir -p "$fed_home"
    if command -v useradd &>/dev/null; then
      useradd -r -s /usr/sbin/nologin -d "$fed_home" -c "SSHChat federation" "$fed_user" 2>/dev/null || true
    elif command -v adduser &>/dev/null; then
      adduser -S -D -h "$fed_home" -s /sbin/nologin -g "SSHChat federation" "$fed_user" 2>/dev/null || \
        adduser -S -D -h "$fed_home" -s /bin/false "$fed_user" 2>/dev/null || true
    fi
    if id "$fed_user" &>/dev/null; then
      echo "info: created federation user $fed_user (home $fed_home)"
    fi
    return 0
  fi
  cur_home=$(getent passwd "$fed_user" | cut -d: -f6)
  if [[ -n "$cur_home" && "$cur_home" != "$fed_home" ]]; then
    mkdir -p "$fed_home"
    if [[ -f "$cur_home/.ssh/authorized_keys" && ! -f "$fed_home/.ssh/authorized_keys" ]]; then
      mkdir -p "$fed_home/.ssh"
      cp -a "$cur_home/.ssh/authorized_keys" "$fed_home/.ssh/authorized_keys"
    fi
    if command -v usermod >/dev/null 2>&1; then
      usermod -d "$fed_home" "$fed_user" 2>/dev/null || true
      echo "info: federation SSH home -> $fed_home (was $cur_home)"
    fi
  fi
}

_ensure_federation_user_darwin() {
  local fed_user=$1 fed_home=$2 record="/Users/$fed_user" uid staff_gid pw
  if id "$fed_user" &>/dev/null; then
    return 0
  fi
  if ! command -v dscl &>/dev/null; then
    echo "error: macOS: dscl not found (cannot create $fed_user)" >&2
    return 1
  fi
  uid=$(darwin_next_uid)
  staff_gid=$(darwin_staff_gid)
  pw=$(openssl rand -base64 32 | tr -d '\n')

  dscl . -create "$record"
  dscl . -create "$record" UserShell /bin/bash
  dscl . -create "$record" RealName "SSHChat federation"
  dscl . -create "$record" UniqueID "$uid"
  dscl . -create "$record" PrimaryGroupID "$staff_gid"
  dscl . -create "$record" NFSHomeDirectory "$fed_home"
  dscl . -create "$record" AuthenticationAuthority ';LocalAuthority;'
  dscl . -passwd "$record" "$pw"

  mkdir -p "$fed_home/.ssh"
  chown "$fed_user:$(primary_group_of "$fed_user")" "$fed_home" "$fed_home/.ssh" 2>/dev/null || \
    chown "$fed_user:staff" "$fed_home" "$fed_home/.ssh"
  chmod 755 "$fed_home"
  chmod 700 "$fed_home/.ssh"

  if ! id "$fed_user" &>/dev/null; then
    echo "error: macOS: user record created but id(1) does not see $fed_user" >&2
    return 1
  fi
  echo "info: created macOS federation user $fed_user (uid $uid, home $fed_home)"
}

# Create sshchat-federation (if missing), ensure ~/.ssh, migrate legacy inbound keys.
# Args: [fed_user] [fed_home] [fed_dir]
ensure_federation_user() {
  local fed_user=${1:-sshchat-federation}
  local fed_home=${2:-/var/lib/sshchat-federation}
  local fed_dir=${3:-}
  local fed_group inbound live

  if is_darwin; then
    _ensure_federation_user_darwin "$fed_user" "$fed_home"
  else
    _ensure_federation_user_linux "$fed_user" "$fed_home"
  fi

  if ! id "$fed_user" &>/dev/null; then
    echo "warning: federation user $fed_user missing; inbound SSH federation will not work" >&2
    return 1
  fi

  fed_group=$(primary_group_of "$fed_user")
  mkdir -p "$fed_home/.ssh"
  chown "$fed_user:$fed_group" "$fed_home" "$fed_home/.ssh"
  chmod 755 "$fed_home"
  chmod 700 "$fed_home/.ssh"

  live="$fed_home/.ssh/authorized_keys"
  inbound=""
  if [[ -n "$fed_dir" ]]; then
    inbound="$fed_dir/authorized_keys_inbound"
  fi

  if [[ ! -f "$live" && -n "$inbound" && -f "$inbound" ]]; then
    cp -a "$inbound" "$live"
    echo "info: migrated federation inbound keys -> $live"
  fi
  if [[ -f "$live" ]]; then
    chown "$fed_user:$fed_group" "$live"
    chmod 600 "$live"
  fi
  sync_federation_staging_keys "$live" "$inbound"
}

# Keep FED_DIR/authorized_keys_inbound aligned with the live sshd file (macOS ops/debug).
sync_federation_staging_keys() {
  local live=$1 inbound=$2
  [[ -n "$inbound" && -f "$live" ]] || return 0
  cp -a "$live" "$inbound"
  chmod 600 "$inbound"
}

# Path sshd reads for inbound federation keys.
federation_auth_keys_path() {
  local fed_user=$1 fed_home=$2 fed_dir=$3 auth_dir
  if id "$fed_user" &>/dev/null; then
    if is_darwin; then
      auth_dir="$fed_home"
    else
      auth_dir=$(getent passwd "$fed_user" | cut -d: -f6)
      auth_dir=${auth_dir:-$fed_home}
    fi
    printf '%s/.ssh/authorized_keys' "$auth_dir"
    return 0
  fi
  if is_darwin && [[ -n "$fed_dir" ]]; then
    printf '%s/authorized_keys_inbound' "$fed_dir"
    return 0
  fi
  printf '%s/.ssh/authorized_keys' "$fed_home"
}
