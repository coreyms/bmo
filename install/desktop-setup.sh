#!/bin/bash
# BMO desktop integration: wallpaper, quick-launch panel button, desktop icon.
# Run as the kiosk user:  sudo -u sylas bash /opt/bmo/install/desktop-setup.sh
set -e
export DISPLAY=${DISPLAY:-:0}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

# --- 1. wallpaper: BMO's idle face (rendered by the repo, not a screenshot)
pcmanfm --profile LXDE-pi --set-wallpaper=/opt/bmo/assets/wallpaper.png \
        --wallpaper-mode=crop 2>/dev/null ||
  pcmanfm --set-wallpaper=/opt/bmo/assets/wallpaper.png --wallpaper-mode=crop
echo "wallpaper: set"

# --- 2. desktop shortcut
mkdir -p "$HOME/Desktop"
cp /usr/share/applications/bmo.desktop "$HOME/Desktop/bmo.desktop"
chmod +x "$HOME/Desktop/bmo.desktop"
# pcmanfm trusts executables it knows about; gio metadata skips the
# "is this launcher safe?" prompt where supported
gio set "$HOME/Desktop/bmo.desktop" metadata::trusted true 2>/dev/null || true
echo "desktop shortcut: created"

# --- 3. quick-launch button on the lxpanel-pi launch bar
# The session starts the panel bare (@lxpanel-pi in the rpd-x autostart), so
# its user config lives at exactly this path — no profile directory.
panel="$HOME/.config/lxpanel-pi/panels/panel"
if [ ! -f "$panel" ]; then
  mkdir -p "$(dirname "$panel")"
  cp /etc/xdg/lxpanel-pi/panels/panel "$panel"
fi
if grep -q "bmo.desktop" "$panel"; then
  echo "panel button: already present"
else
  # backup goes OUTSIDE panels/ — lxpanel loads every file in that dir as
  # an extra panel, and the duplicate plugins crash it (learned 2026-07-21)
  cp "$panel" "$HOME/.config/lxpanel-pi/panel.bak"
  awk '
    /type *= *launchbar/ { inlb = 1 }
    { print }
    inlb && /Config *\{/ && !done {
      print "    Button {"
      print "      id=bmo.desktop"
      print "    }"
      done = 1
    }
  ' "$HOME/.config/lxpanel-pi/panel.bak" > "$panel"
  echo "panel button: added (backup at ~/.config/lxpanel-pi/panel.bak)"
fi
# lxpanelctl-pi restart makes the panel exit, and lxsession only respawns
# crashes — so restart it ourselves and verify it survived. The relaunch
# must go through the systemd user session: a bare lxpanel-pi from a sudo
# shell segfaults (no session D-Bus).
lxpanelctl-pi restart 2>/dev/null || true
sleep 2
if ! pgrep -x lxpanel-pi >/dev/null; then
  systemd-run --user --collect --unit="panel-up-$$" \
    --setenv=DISPLAY="$DISPLAY" --setenv=XAUTHORITY="$HOME/.Xauthority" \
    lxpanel-pi 2>/dev/null || true
  sleep 2
fi
pgrep -x lxpanel-pi >/dev/null && echo "panel: running" || echo "panel: FAILED"

# --- 4. file-manager behavior: single-click opens things, and executables
# (like the BMO desktop icon) run without the "Execute File?" dialog
libfm="$HOME/.config/libfm/libfm.conf"
mkdir -p "$(dirname "$libfm")"
[ -f "$libfm" ] || : > "$libfm"
grep -q '^\[config\]' "$libfm" || printf '[config]\n' >> "$libfm"
for kv in single_click=1 quick_exec=1; do
  k=${kv%%=*}
  if grep -q "^$k=" "$libfm"; then
    sed -i "s/^$k=.*/$kv/" "$libfm"
  else
    sed -i "/^\[config\]/a $kv" "$libfm"
  fi
done
pcmanfm --reconfigure 2>/dev/null || true
echo "file manager: single-click on, exec prompt off"
echo "done"
