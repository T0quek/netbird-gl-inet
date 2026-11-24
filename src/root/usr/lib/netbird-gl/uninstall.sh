#!/bin/sh
# Uninstall netbird-gl.
#   --purge       also remove the NetBird binary, enrollment (/etc/netbird) and configuration
#   --deregister  deregister this peer from the NetBird management server first
: "${DESTDIR:=}"
export NBGL_LIB="${NBGL_LIB:-$DESTDIR/usr/lib/netbird-gl}" NB_BIN="${NB_BIN:-$DESTDIR/usr/bin/netbird}" NB_INIT="${NB_INIT:-$DESTDIR/etc/init.d/netbird}"
PURGE=0
DEREG=0
for a in "$@"; do
	case "$a" in --purge) PURGE=1 ;; --deregister) DEREG=1 ;; esac
done
[ $DEREG = 1 ] && [ -x "$DESTDIR/usr/bin/netbird" ] && "$DESTDIR/usr/bin/netbird" deregister >/dev/null 2>&1
"$DESTDIR/usr/sbin/netbird-gl" fw-remove 2>/dev/null
for s in netbird-gl netbird; do
	[ -x "$DESTDIR/etc/init.d/$s" ] && { "$DESTDIR/etc/init.d/$s" stop; "$DESTDIR/etc/init.d/$s" disable; } 2>/dev/null
done
# NetBird is stopped: give /etc/resolv.conf back to GL and drop the dnsmasq drop-in
"$DESTDIR/usr/sbin/netbird-gl" resolv-unguard 2>/dev/null
"$DESTDIR/usr/sbin/netbird-gl" guard-off >/dev/null 2>&1
"$DESTDIR/usr/sbin/netbird-gl" dns-reset >/dev/null 2>&1
case "$(uci -q get switch-button.@main[0].func)" in
	NetBird-VPN | NetBird-Exit-Node | netbird)
		echo "Note: the GL toggle button was assigned to NetBird - pick another function in SYSTEM -> Toggle Button Settings." ;;
esac
rm -f "$DESTDIR/etc/nginx/gl-conf.d/netbird-gl.conf" "$DESTDIR/etc/gl-switch.d/NetBird-VPN.sh" \
	"$DESTDIR/etc/gl-switch.d/NetBird-Exit-Node.sh" "$DESTDIR/etc/gl-switch.d/netbird.sh" \
	"$DESTDIR/usr/share/oui/menu.d/netbird-gl.json" "$DESTDIR/www/views/gl-sdk4-ui-netbirdgl.common.js.gz" \
	"$DESTDIR/etc/init.d/netbird-gl" "$DESTDIR/etc/init.d/netbird" "$DESTDIR/usr/sbin/netbird-gl" \
	"$DESTDIR/lib/upgrade/keep.d/netbird-gl"
rm -rf "$DESTDIR/www/netbird-gl" "$DESTDIR/var/run/netbird-gl"
if [ $PURGE = 1 ]; then
	rm -rf "$DESTDIR/etc/netbird" "$DESTDIR/var/log/netbird"
	rm -f "$DESTDIR/usr/bin/netbird" "$DESTDIR/etc/config/netbird_gl" "$DESTDIR/var/lib/netbird"
fi
rm -rf "$DESTDIR/usr/lib/netbird-gl"
nginx -t >/dev/null 2>&1 && nginx -s reload
echo "netbird-gl uninstalled."
