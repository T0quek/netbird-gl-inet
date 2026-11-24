#!/bin/sh
# GL toggle button function "NetBird-Exit-Node" (SYSTEM -> Toggle Button Settings).
case "$1" in
	on | off) ;;
	*) echo "Usage: $0 [on|off]"; exit 1 ;;
esac
# run in the background - the GL firmware does not wait for the tunnel
/usr/sbin/netbird-gl switch exit "$1" </dev/null >/dev/null 2>&1 &
exit 0
