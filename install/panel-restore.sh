#!/bin/bash
# Repair: bring back the taskbar after it was stopped.
# Run as root:  sudo bash /opt/bmo/install/panel-restore.sh
# (root is needed to reach sylas's systemd user session; a bare
#  `sudo -u sylas lxpanel-pi` segfaults — no session D-Bus.)
set -e
d=/home/sylas/.config/lxpanel-pi/panels

# Un-park the edited config if an earlier run parked it: it wasn't the
# culprit (the stock config crashed identically outside the session).
if [ -f "$d/panel.broken" ] && [ ! -f "$d/panel" ]; then
  mv "$d/panel.broken" "$d/panel"
  chown sylas:sylas "$d/panel"
  echo "restored edited panel config (with the BMO button)"
fi

# Launch inside sylas's real session: systemd user manager provides the
# session bus; X access mirrors what bmo.service does.
systemd-run -M sylas@ --user --collect --unit="panel-restore-$$" \
  --setenv=DISPLAY=:0 --setenv=XAUTHORITY=/home/sylas/.Xauthority \
  lxpanel-pi
sleep 3
if pgrep -u sylas -x lxpanel-pi >/dev/null; then
  echo "panel is back"
else
  echo "panel STILL down — reboot will restore it (lxsession autostarts it"
  echo "at login, and the fixed config is already in place)"
fi
