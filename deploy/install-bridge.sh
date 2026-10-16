#!/bin/sh
# Idempotent installer for the bridge host (Linux with systemd and udev, Python 3.9 or newer).
#
# Usage: sh deploy/install-bridge.sh --public-key KEY [--user U] [--dir D] [--home H] [--root DIR]
#                                    [--serial-device PATH] [--receiver-device PATH]
#                                    [--receiver-vendor HEX --receiver-product HEX [--receiver-serial S]]
#                                    [--no-systemctl] [--from CIDR]... [--skip-prereq-check]
#                                    [--allow-custom-home] [--create-home]
# Defaults: --user bose, --dir /opt/bose-lifestyle, --home from the account (else /home/<user>).
# --home other than that default needs --allow-custom-home. A missing home is refused unless --create-home,
# which creates it mode 0700 for the user. The key file, .ssh and the state directory are opened without following symbolic links.
# --serial-device must be a /dev/serial/by-id/ path; the unit passes it to the bridge as --serial-port.
# A udev rule is written only when --receiver-vendor and --receiver-product are given, and matches only that receiver.
# Creates config.json (every room off, no source connected) only when absent; never overwrites it.
# With --root or --no-systemctl it runs no systemctl or udevadm and prints the manual steps.
# Everything it writes is under --root (--root, --dir and --home may not contain . or .. components); with --root,
# device nodes are looked up under --root too.
# It needs the Python package pyserial, and evdev when --receiver-device is given. A missing package stops the run
# before anything is written. --skip-prereq-check skips that check (the tests use it on hosts without the packages).
set -eu
umask 022

fail() { echo "install-bridge: $*" >&2; exit 1; }

PUBKEY=""; USER_NAME=bose; INSTALL_DIR=/opt/bose-lifestyle; HOME_DIR=""; ROOT=""
SERIAL=""; RECEIVER=""; RX_VENDOR=""; RX_PRODUCT=""; RX_SERIAL=""; NOSYSTEMCTL=0; FROM_ARGS=""; SKIP_PREREQ=0
ALLOW_CUSTOM_HOME=0; CREATE_HOME=0
while [ $# -gt 0 ]; do
  case $1 in
    --public-key|--user|--dir|--home|--root|--serial-device|--receiver-device|--receiver-vendor|--receiver-product|--receiver-serial|--from)
      [ $# -ge 2 ] || fail "$1 needs a value"
      case $1 in
        --public-key) PUBKEY=$2 ;;
        --user) USER_NAME=$2 ;;
        --dir) INSTALL_DIR=$2 ;;
        --home) HOME_DIR=$2 ;;
        --root) ROOT=$2 ;;
        --serial-device) SERIAL=$2 ;;
        --receiver-device) RECEIVER=$2 ;;
        --receiver-vendor) RX_VENDOR=$2 ;;
        --receiver-product) RX_PRODUCT=$2 ;;
        --receiver-serial) RX_SERIAL=$2 ;;
        --from)
          case $2 in ''|*[!0-9A-Fa-f.:/]*) fail "--from takes an address or CIDR, got: $2" ;; esac
          FROM_ARGS="$FROM_ARGS --from $2" ;;
      esac
      shift 2 ;;
    --no-systemctl) NOSYSTEMCTL=1; shift ;;
    --skip-prereq-check) SKIP_PREREQ=1; shift ;;
    --allow-custom-home) ALLOW_CUSTOM_HOME=1; shift ;;
    --create-home) CREATE_HOME=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

# Validate everything before the first write.
[ -n "$PUBKEY" ] || fail "--public-key is required (copy it from the setup screen)"
case $USER_NAME in ''|[!a-z_]*|*[!a-z0-9_-]*) fail "--user must be a plain account name (lowercase letters, digits, _ -)" ;; esac
ROOT=${ROOT%/}
case $ROOT in ''|/*) ;; *) fail "--root must be an absolute path" ;; esac
INSTALL_DIR=${INSTALL_DIR%/}
case $INSTALL_DIR in /*) ;; *) fail "--dir must be an absolute path" ;; esac
case $INSTALL_DIR in *[!A-Za-z0-9._/-]*) fail "--dir may use letters, digits, . _ / - only" ;; esac
# A "." or ".." component would let a destination leave --root, so no path option may contain one.
no_dot_parts() { case $2 in */../*|*/..|*/./*|*/.|..|.) fail "$1 must not contain a . or .. path component" ;; esac; }
no_dot_parts --root "$ROOT"
no_dot_parts --dir "$INSTALL_DIR"

# The interpreter is resolved once, to an absolute path, and used for the unit and every helper call.
PYTHON=$(command -v python3 2>/dev/null) || PYTHON=""
case $PYTHON in /*) ;; *) fail "python3 (3.9 or newer) is required" ;; esac
case $PYTHON in *[!A-Za-z0-9._/+-]*) fail "the python3 path may use letters, digits, . _ / + - only" ;; esac
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/.." && pwd)
HELPER="$HERE/authorized_keys.py"
ROOT_DIR=${ROOT:-/}
# Pin the selected staging/live root before reading anything below it. Every later helper process
# validates this device/inode, so replacement between subprocesses cannot redirect the install.
unset BOSE_INSTALL_ROOT_ID || :
ROOT_ID=$("$PYTHON" -B "$HELPER" root-id "$ROOT_DIR") || fail "could not pin the install root"
export BOSE_INSTALL_ROOT_ID=$ROOT_ID

# The unit is rewritten from these options. An installed service that already names a serial port or a receiver file
# must be given them again: leaving one out would silently drop it (and its device group) from the new unit.
EXISTING_UNIT="$ROOT/etc/systemd/system/bose-bridge.service"
if [ -f "$EXISTING_UNIT" ]; then
  if [ -z "$SERIAL" ] && grep -q -e '--serial-port' "$EXISTING_UNIT"; then
    fail "the installed service already uses a serial port. Run this again with --serial-device PATH (and --receiver-device PATH if it uses a receiver) so the new service keeps them. Nothing was changed."
  fi
  if [ -z "$RECEIVER" ] && grep -q -e '--ir-config' "$EXISTING_UNIT"; then
    fail "the installed service already uses an infrared receiver. Run this again with --receiver-device PATH (and --serial-device PATH) so the new service keeps it. Nothing was changed."
  fi
fi

# Prerequisites: the bridge imports pyserial (as serial) and, with a receiver, evdev. Checked before any write.
if [ "$SKIP_PREREQ" = 0 ]; then
  "$PYTHON" -B -c 'import serial' >/dev/null 2>&1 ||
    fail "the Python package pyserial is not installed: install it with your system package manager or pip, then run the installer again."
  if [ -n "$RECEIVER" ]; then
    "$PYTHON" -B -c 'import evdev' >/dev/null 2>&1 ||
      fail "the Python package evdev is not installed (needed with --receiver-device): install it with your system package manager or pip, then run the installer again."
  fi
fi

CMD="$INSTALL_DIR/bose_request.py"

# Serial: a by-id path only, because the bridge opens it by that name.
if [ -n "$SERIAL" ]; then
  case $SERIAL in /dev/serial/by-id/?*) ;; *) fail "--serial-device must be a path under /dev/serial/by-id/" ;; esac
  serial_name=${SERIAL#/dev/serial/by-id/}
  case $serial_name in ''|*/*|*..*|*[!A-Za-z0-9._+:-]*) fail "--serial-device name may use letters, digits, . _ + : - only" ;; esac
fi

# Receiver rule: only when the vendor and product are both given; rendered by bose_receivers.render_udev_rule.
RX_RULE=0; RULE=""
if [ -n "$RX_VENDOR$RX_PRODUCT$RX_SERIAL" ]; then
  [ -n "$RX_VENDOR" ] && [ -n "$RX_PRODUCT" ] || fail "--receiver-vendor and --receiver-product must be given together"
  [ -n "$RECEIVER" ] || fail "--receiver-device is needed too (the rule's group comes from it)"
  RENDER='import sys; sys.path.insert(0, sys.argv[1]); import bose_receivers as r; a = sys.argv[2:]; sys.stdout.write(r.render_udev_rule(a[0], a[1], a[2] if len(a) > 2 else None))'
  if [ -n "$RX_SERIAL" ]; then
    RULE=$("$PYTHON" -I -B -c "$RENDER" "$SRC" "$RX_VENDOR" "$RX_PRODUCT" "$RX_SERIAL") || fail "the receiver vendor, product or serial was refused"
  else
    RULE=$("$PYTHON" -I -B -c "$RENDER" "$SRC" "$RX_VENDOR" "$RX_PRODUCT") || fail "the receiver vendor or product was refused"
  fi
  RX_RULE=1
fi

"$PYTHON" -B "$HELPER" line --key "$PUBKEY" --command "$CMD" $FROM_ARGS >/dev/null ||
  fail "the public key or --from value was refused (see the message above)"

if [ -z "$ROOT" ] && [ "$(id -u)" = 0 ]; then
  id "$USER_NAME" >/dev/null 2>&1 || fail "user $USER_NAME does not exist (create it or pass --user)"
fi
if [ -z "$ROOT" ]; then DEFAULT_HOME=$("$PYTHON" -B "$HELPER" home "$USER_NAME"); else DEFAULT_HOME=/home/$USER_NAME; fi
[ -n "$HOME_DIR" ] || HOME_DIR=$DEFAULT_HOME
HOME_DIR=${HOME_DIR%/}
case $HOME_DIR in /*) ;; *) fail "--home must be an absolute path" ;; esac
case $HOME_DIR in *[!A-Za-z0-9._/-]*) fail "--home may use letters, digits, . _ / - only" ;; esac
no_dot_parts --home "$HOME_DIR"
# Only the account's own home, or a home inside --root, is used without confirmation: a key written into
# another user's home would grant that user's account to the bridge's forced command.
if [ "$HOME_DIR" != "$DEFAULT_HOME" ] && [ "$ALLOW_CUSTOM_HOME" = 0 ]; then
  inside_root=0
  if [ -n "$ROOT" ]; then case $HOME_DIR in "$ROOT"/*) inside_root=1 ;; esac; fi
  [ "$inside_root" = 1 ] || fail "--home $HOME_DIR is not the home of $USER_NAME ($DEFAULT_HOME); pass --allow-custom-home to confirm it"
fi

PROBLEMS=$("$PYTHON" -B "$HELPER" platform --root "$ROOT_DIR") || fail "could not check the platform"
SYSTEMD_OK=1; UDEV_OK=1
for p in $PROBLEMS; do
  case $p in
    python_too_old) fail "Python 3.9 or newer is required on the bridge host" ;;
    no_systemd) SYSTEMD_OK=0; echo "Note: systemd not found; the service is installed as a file only." ;;
    no_udev) UDEV_OK=0; echo "Note: udev not found; the receiver rule is installed as a file only." ;;
  esac
done

# Groups come from the device nodes, never from fixed names. A device that was given but cannot
# be resolved stops the run before anything is written.
dev_path() { if [ -n "$ROOT" ]; then printf '%s%s' "$ROOT" "$1"; else printf '%s' "$1"; fi; }
UNIT_GROUPS=""; RECV_GROUP=""
add_group() {
  case " $UNIT_GROUPS " in *" $1 "*) ;; *) UNIT_GROUPS="${UNIT_GROUPS:+$UNIT_GROUPS }$1" ;; esac
}
if [ -n "$SERIAL" ]; then
  g=$("$PYTHON" -B "$HELPER" group "$(dev_path "$SERIAL")") || fail "serial device $SERIAL not found (pass the adapter's by-id path)"
  case $g in *[!A-Za-z0-9._-]*) fail "unexpected group name for the serial device" ;; esac
  add_group "$g"
else
  echo "Missing: no --serial-device given. The bridge needs the serial adapter's by-id path to find"
  echo "  the group it must join. Re-run with --serial-device PATH, or add the group by hand."
fi
if [ -n "$RECEIVER" ]; then
  RECV_GROUP=$("$PYTHON" -B "$HELPER" group "$(dev_path "$RECEIVER")") || fail "receiver device $RECEIVER not found"
  case $RECV_GROUP in *[!A-Za-z0-9._-]*) fail "unexpected group name for the receiver device" ;; esac
  add_group "$RECV_GROUP"
fi

IR_ARG=""
# The file lives in the state directory: the service user writes it with `bose-lifestyle ir select`, and
# ProtectSystem=strict leaves the install directory read-only to the service.
if [ -n "$RECEIVER" ]; then IR_ARG=" --ir-config /var/lib/bose-control/ir-config.json"; fi
SERIAL_ARG=""
if [ -n "$SERIAL" ]; then SERIAL_ARG=" --serial-port $SERIAL"; fi

STATE_DIR=$ROOT/var/lib/bose-control
BIN_DIR=$ROOT$INSTALL_DIR
if [ -n "$ROOT" ]; then
  case $HOME_DIR in "$ROOT"/*) HOME_PATH=$HOME_DIR ;; *) HOME_PATH=$ROOT$HOME_DIR ;; esac
else
  HOME_PATH=$HOME_DIR
fi
SSH_DIR=$HOME_PATH/.ssh
CHANGED=0
tmp=""

# Early diagnostic only; each eventual write independently walks and verifies the rooted path by descriptor.
for rooted_path in "$INSTALL_DIR/bose_request.py" "/etc/systemd/system/bose-bridge.service" "/var/lib/bose-control/config.json" "$HOME_DIR/.ssh/authorized_keys"; do
  "$PYTHON" -B "$HELPER" check-path "${ROOT:-/}" "$rooted_path" || fail "refusing a symbolic link or non-directory in rooted path: $rooted_path"
done

for p in "$HOME_PATH" "$SSH_DIR" "$SSH_DIR/authorized_keys"; do
  [ ! -L "$p" ] || fail "refusing to write through a symbolic link: $p"
done

# The key goes first, so a refusal writes nothing. The helper opens the home, .ssh and the key file one by one
# without following links, checks who owns the directories, and sets the owner (root installs only).
OWNER_ARG=""; CREATE_ARG=""
if [ -z "$ROOT" ] && [ "$(id -u)" = 0 ]; then OWNER_ARG="--owner $USER_NAME"; fi
if [ "$CREATE_HOME" = 1 ]; then CREATE_ARG="--create-home"; fi
RESULT=$("$PYTHON" -B "$HELPER" install "$HOME_PATH" --root "${ROOT:-/}" --key "$PUBKEY" --command "$CMD" $FROM_ARGS $OWNER_ARG $CREATE_ARG) ||
  fail "the restricted key could not be installed (see the message above)"
if [ "$RESULT" = changed ]; then CHANGED=1; fi

# Write a finished temp file over DEST unless identical; then set the mode.
place() {  # tmpfile dest mode
  if [ -f "$2" ] && cmp -s "$1" "$2"; then rm -f "$1"; else mv -f "$1" "$2"; CHANGED=1; fi
  chmod "$3" "$2"
}
root_put() {  # source dest mode; publish below the retained root through the helper
  rel=$2
  if [ -n "$ROOT" ]; then rel=${rel#"$ROOT"}; fi
  result=$("$PYTHON" -B "$HELPER" put "${ROOT:-/}" "$rel" "$3" "$1") || fail "could not safely publish $2"
  if [ "$result" = changed ]; then CHANGED=1; fi
}
copy_file() {  # src dest mode
  tmp=$(mktemp)
  cp "$1" "$tmp"
  root_put "$tmp" "$2" "$3"
  rm -f "$tmp"
}
render() {  # template dest groups
  tmp=$(mktemp)
  sed -e "s|@PYTHON@|$PYTHON|g" -e "s|@SERIAL_ARG@|$SERIAL_ARG|g" -e "s|@IR_CONFIG@|$IR_ARG|g" \
    -e "s|@DIR@|$INSTALL_DIR|g" -e "s|@USER@|$USER_NAME|g" -e "s|@GROUPS@|$3|g" "$1" > "$tmp"
  root_put "$tmp" "$2" 0644
  rm -f "$tmp"
}
render_rule() {  # dest groups
  tmp=$(mktemp)
  printf '%s\n' "$RULE" | sed -e "s|@GROUPS@|$2|g" > "$tmp"
  root_put "$tmp" "$1" 0644
  rm -f "$tmp"
}
# The state directory is handled like the key directory: the helper opens it without following a link,
# checks its owner, and sets the mode and owner on the descriptor. changed means it was created here.
STATE_RESULT=$("$PYTHON" -B "$HELPER" state "$STATE_DIR" --root "${ROOT:-/}" $OWNER_ARG) ||
  fail "the state directory could not be prepared (see the message above)"
if [ "$STATE_RESULT" = changed ]; then CHANGED=1; fi

for m in bose_bridge.py bose_commands.py bose_config.py bose_events.py bose_ir.py bose_receivers.py \
         bose_serial.py bose_state.py bose_cli.py; do
  copy_file "$SRC/$m" "$BIN_DIR/$m" 0644
done
copy_file "$SRC/bose_request.py" "$BIN_DIR/bose_request.py" 0755
copy_file "$SRC/bin/bose-lifestyle" "$BIN_DIR/bin/bose-lifestyle" 0755

render "$HERE/bose-bridge.service.in" "$ROOT/etc/systemd/system/bose-bridge.service" "$UNIT_GROUPS"
if [ "$RX_RULE" = 1 ]; then
  render_rule "$ROOT/etc/udev/rules.d/70-bose-receiver.rules" "$RECV_GROUP"
fi

# The starting config is created here and only here, and only when absent. The helper runs from the installed
# files, offline: it never reaches the bridge socket, so --root stays self-contained.
CONFIG=$STATE_DIR/config.json
CONFIG_REL=${CONFIG#"${ROOT:-/}"}; CONFIG_REL=${CONFIG_REL#/}
CONFIG_OWNER=""
if [ -z "$ROOT" ] && [ "$(id -u)" = 0 ]; then CONFIG_OWNER="--owner $USER_NAME"; fi
CONFIG_RESULT=$("$PYTHON" -B "$HELPER" config "${ROOT:-/}" "$CONFIG_REL" --module-path "$SRC" $CONFIG_OWNER) ||
  fail "could not safely prepare $CONFIG; run the installer again"
if [ "$CONFIG_RESULT" = changed ]; then
  CHANGED=1
  echo "Created $CONFIG: every room is off and no source is connected."
else
  echo "config.json left unchanged: $CONFIG already exists."
fi

HOSTKEY=$ROOT/etc/ssh/ssh_host_ed25519_key.pub
if [ -f "$HOSTKEY" ] && command -v ssh-keygen >/dev/null 2>&1; then
  echo "Bridge host key fingerprint (compare it with the setup screen):"
  ssh-keygen -lf "$HOSTKEY"
else
  echo "Bridge host key fingerprint: on the bridge host run ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
  echo "and compare the result with the setup screen."
fi

RUN_SYSTEMCTL=0
if [ "$NOSYSTEMCTL" = 0 ] && [ "$SYSTEMD_OK" = 1 ] && [ -z "$ROOT" ]; then RUN_SYSTEMCTL=1; fi
if [ "$RUN_SYSTEMCTL" = 1 ]; then
  systemctl daemon-reload
  if [ "$RX_RULE" = 1 ] && [ "$UDEV_OK" = 1 ]; then udevadm control --reload-rules; fi
  echo "Next step: sudo systemctl enable --now bose-bridge.service"
else
  echo "Not run by this script (--no-systemctl, --root, or no systemd). Manual steps:"
  if [ "$SYSTEMD_OK" = 1 ]; then
    echo "  sudo systemctl daemon-reload"
    echo "  sudo systemctl enable --now bose-bridge.service"
  else
    echo "  Start the bridge as $USER_NAME with your init system, e.g.:"
    echo "  $PYTHON -u $INSTALL_DIR/bose_bridge.py$SERIAL_ARG$IR_ARG"
    echo "  (the command socket is /run/bose-control/command.sock, owned by $USER_NAME)"
  fi
  if [ "$RX_RULE" = 1 ] && [ "$UDEV_OK" = 1 ]; then
    echo "  sudo udevadm control --reload-rules && sudo udevadm trigger"
  fi
fi

if [ "$CHANGED" = 0 ]; then
  echo "already installed; nothing changed"
else
  echo "installed under $BIN_DIR"
fi
