#!/bin/sh
# netbird-gl installer for GL.iNet routers (firmware 4.x; target: Beryl 7 GL-MT3600BE)
# Usage (on the router, inside the unpacked package directory):
#   sh install.sh [--netbird-tar file.tar.gz --checksums file.txt] [--no-binary]
# Re-running it upgrades in place (configuration and enrollment are kept).
set -e

: "${DESTDIR:=}"
PKG=$(cd "$(dirname "$0")" && pwd)
export NBGL_LIB="${NBGL_LIB:-$DESTDIR/usr/lib/netbird-gl}" NB_BIN="${NB_BIN:-$DESTDIR/usr/bin/netbird}" NB_INIT="${NB_INIT:-$DESTDIR/etc/init.d/netbird}"
NB_TAR=""
NB_SUMS=""
NO_BIN=0
FCGI_SOCK=${NBGL_FCGI_SOCK:-/var/run/fcgiwrap.socket}

while [ $# -gt 0 ]; do
	case "$1" in
		--netbird-tar) NB_TAR=$2; shift 2 ;;
		--checksums) NB_SUMS=$2; shift 2 ;;
		--no-binary) NO_BIN=1; shift ;;
		*) echo "Unknown option: $1"; exit 1 ;;
	esac
done

say() { printf '>> %s\n' "$*"; }
warn() { printf '!! %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"
[ -f "$DESTDIR/etc/openwrt_release" ] || die "this does not look like OpenWrt"
[ -d "$PKG/root" ] || die "root/ directory not found next to install.sh"
VER=$(cat "$PKG/VERSION" 2>/dev/null || echo "?")

case "$(uname -m)" in
	aarch64 | x86_64 | armv6* | armv7*) ;;
	*) die "unsupported architecture $(uname -m)" ;;
esac
[ -d "$DESTDIR/usr/share/oui" ] || warn "GL 4.x admin panel not detected - the panel tab may not appear"

for t in uci jsonfilter sha256sum tar curl nginx awk sed grep tr head tail cut sort wc df mktemp dirname date logger; do
	command -v "$t" >/dev/null 2>&1 || die "missing tool: $t"
done
[ -S "$FCGI_SOCK" ] || die "fcgiwrap socket $FCGI_SOCK not found (required by the GL admin panel)"
[ -d "$DESTDIR/etc/nginx/gl-conf.d" ] || die "/etc/nginx/gl-conf.d not found - unsupported GL firmware"

ovl=$(df -k /overlay 2>/dev/null | awk 'NR==2{print $4}')
[ -n "$ovl" ] || ovl=$(df -k / | awk 'NR==2{print $4}')
if [ "${ovl:-0}" -lt 61440 ]; then
	warn "low free space on overlay (${ovl} KB) - NetBird needs about 40-60 MB"
	[ "${ovl:-0}" -lt 46080 ] && die "not enough free space"
fi

# --- remove insecure leftovers of devNalyx/netbird-openwrt
if [ -f "$DESTDIR/www/cgi-bin/netbird-api" ] || [ -f "$DESTDIR/etc/nginx/gl-conf.d/netbird.conf" ]; then
	say "removing the unauthenticated API of a previous devNalyx installation"
	rm -f "$DESTDIR/www/cgi-bin/netbird-api" "$DESTDIR/www/cgi-bin/netbird-ui" \
		"$DESTDIR/etc/nginx/gl-conf.d/netbird.conf" \
		"$DESTDIR/usr/libexec/netbird-toggle-exit.sh" "$DESTDIR/usr/libexec/netbird-setup-api.sh" \
		"$DESTDIR/usr/libexec/netbird-watchdog.sh" "$DESTDIR/usr/libexec/netbird-cleanup.sh" \
		"$DESTDIR/usr/share/oui/menu.d/netbirdview.json" "$DESTDIR/www/views/gl-sdk4-ui-netbirdview.common.js.gz"
	rm -rf "$DESTDIR/www/netbird"
	if crontab -l 2>/dev/null | grep -q netbird-watchdog; then
		crontab -l | grep -v netbird-watchdog | crontab -
	fi
	if [ -f "$DESTDIR/etc/config/netbird" ] && uci -q get netbird.@connection[0].api_token >/dev/null; then
		warn "a NetBird API token was stored in /etc/config/netbird - removing the file. REVOKE that token in the NetBird dashboard!"
		rm -f "$DESTDIR/etc/config/netbird"
	fi
	if grep -q 'netbird' "$DESTDIR/etc/rc.local" 2>/dev/null; then
		warn "/etc/rc.local contains NetBird entries - review them manually (the installer does not touch them)"
	fi
fi
if [ -x "$DESTDIR/etc/init.d/gl-netbird" ]; then
	say "disabling the gl-netbird service (forum guide) - replaced by netbird-gl"
	"$DESTDIR/etc/init.d/gl-netbird" stop 2>/dev/null || true
	"$DESTDIR/etc/init.d/gl-netbird" disable 2>/dev/null || true
fi

# --- upgrade of an earlier netbird-gl build
OLD_SWITCH_MODE=$(uci -q get netbird_gl.main.switch_mode 2>/dev/null || true)
rm -f "$DESTDIR/etc/gl-switch.d/netbird.sh"
rm -rf "$DESTDIR/usr/lib/netbird-gl/www"

# --- files
if [ -x "$DESTDIR/etc/init.d/netbird-gl" ]; then "$DESTDIR/etc/init.d/netbird-gl" stop 2>/dev/null || true; fi
KEEP_CFG=0
if [ -f "$DESTDIR/etc/config/netbird_gl" ]; then
	KEEP_CFG=1
	cp "$DESTDIR/etc/config/netbird_gl" /tmp/netbird_gl.keep
fi
say "copying files"
cp -a "$PKG/root/." "$DESTDIR/"
if [ $KEEP_CFG = 1 ]; then
	mv /tmp/netbird_gl.keep "$DESTDIR/etc/config/netbird_gl"
	uci -q delete netbird_gl.main.switch_mode || true
	uci -q delete netbird_gl.main.web_backend || true
	uci commit netbird_gl
	say "existing configuration kept"
fi
chmod 755 "$DESTDIR/usr/sbin/netbird-gl" "$DESTDIR/usr/lib/netbird-gl/api.cgi" \
	"$DESTDIR/usr/lib/netbird-gl/fw-reload.sh" "$DESTDIR/usr/lib/netbird-gl/uninstall.sh" \
	"$DESTDIR/etc/init.d/netbird" "$DESTDIR/etc/init.d/netbird-gl" \
	"$DESTDIR/etc/gl-switch.d/NetBird-VPN.sh" "$DESTDIR/etc/gl-switch.d/NetBird-Exit-Node.sh"
chmod 600 "$DESTDIR/etc/config/netbird_gl"
chmod 700 "$DESTDIR/usr/lib/netbird-gl"
mkdir -p "$DESTDIR/etc/netbird" && chmod 700 "$DESTDIR/etc/netbird"
cp "$PKG/nginx/netbird-gl.conf" "$DESTDIR/etc/nginx/gl-conf.d/netbird-gl.conf"
chmod 644 "$DESTDIR/etc/nginx/gl-conf.d/netbird-gl.conf"
mkdir -p "$DESTDIR/www/views"
cp "$PKG/panel/gl-sdk4-ui-netbirdgl.common.js.gz" "$DESTDIR/www/views/"

# toggle button: map the function of the previous build to the new entries
if [ "$(uci -q get switch-button.@main[0].func 2>/dev/null)" = netbird ]; then
	if [ "$OLD_SWITCH_MODE" = exit ]; then f=NetBird-Exit-Node; else f=NetBird-VPN; fi
	uci set "switch-button.@main[0].func=$f"
	uci commit switch-button
	say "toggle button function changed from 'netbird' to '$f'"
fi

"$DESTDIR/usr/sbin/netbird-gl" selftest || die "this router's BusyBox lacks features netbird-gl needs (see above)"

# --- NetBird binary
if [ $NO_BIN = 0 ]; then
	if [ -n "$NB_TAR" ]; then
		[ -f "$NB_TAR" ] || die "file not found: $NB_TAR"
		[ -f "$NB_SUMS" ] || die "use --checksums together with --netbird-tar"
		f=$(basename "$NB_TAR")
		exp=$(awk -v f="$f" '$2==f {print $1; exit}' "$NB_SUMS")
		got=$(sha256sum "$NB_TAR" | awk '{print $1}')
		if [ -z "$exp" ] || [ "$exp" != "$got" ]; then die "SHA256 mismatch for $f"; fi
		d=$(mktemp -d /tmp/nbgl-inst.XXXXXX)
		tar -xzf "$NB_TAR" -C "$d" netbird
		cp "$d/netbird" "$DESTDIR/usr/bin/netbird"
		chmod 755 "$DESTDIR/usr/bin/netbird"
		rm -rf "$d"
		say "installed NetBird from file: $("$DESTDIR/usr/bin/netbird" version)"
	elif [ -x "$DESTDIR/usr/bin/netbird" ]; then
		say "NetBird already installed: $("$DESTDIR/usr/bin/netbird" version 2>/dev/null)"
	else
		say "downloading the latest NetBird release from GitHub (SHA256 verified)"
		"$DESTDIR/usr/sbin/netbird-gl" update latest || die "NetBird download failed (check internet access and the router clock)"
		say "installed NetBird $("$DESTDIR/usr/bin/netbird" version)"
	fi
fi

# --- firewall, services
say "configuring the 'netbird' firewall zone"
"$DESTDIR/usr/sbin/netbird-gl" fw-setup
"$DESTDIR/etc/init.d/netbird" enable
"$DESTDIR/etc/init.d/netbird-gl" enable
"$DESTDIR/etc/init.d/netbird" start
"$DESTDIR/etc/init.d/netbird-gl" start
# "/etc/init.d/nginx reload" does not re-read the configuration on GL firmware
# (procd keeps the unchanged instance), so signal the running master directly.
if nginx -t >/dev/null 2>&1; then
	nginx -s reload || die "nginx reload failed"
else
	rm -f "$DESTDIR/etc/nginx/gl-conf.d/netbird-gl.conf"
	die "nginx rejected the configuration - netbird-gl.conf removed"
fi

# --- self-test
IP=$("$DESTDIR/usr/sbin/netbird-gl" lan-ip)
FAILED=0
if [ "${NBGL_SKIP_SELFTEST:-0}" != 1 ]; then
	PORT=$(grep -E '^ *listen [0-9]+;' /etc/nginx/conf.d/gl.conf 2>/dev/null | grep -oE '[0-9]+' | head -n1)
	BASE="http://127.0.0.1:${PORT:-80}"
	# nginx workers pick up the new configuration within a few seconds
	i=0
	while :; do
		hdr=$(curl -s -D - -o /dev/null "$BASE/netbird-gl/" 2>/dev/null | tr -d '\r')
		printf '%s\n' "$hdr" | grep -qi "frame-ancestors 'self'" && break
		i=$((i + 1))
		[ $i -ge 10 ] && break
		sleep 1
	done
	if printf '%s\n' "$hdr" | head -n1 | grep -q ' 200' && printf '%s\n' "$hdr" | grep -qi "frame-ancestors 'self'"; then
		say "self-test: web page OK"
	else
		warn "self-test: /netbird-gl/ is not served correctly: $(printf '%s\n' "$hdr" | head -n1)"
		FAILED=1
	fi
	body=$(curl -s -H 'X-NBGL: 1' "$BASE/netbird-gl/api?a=status" 2>/dev/null)
	case "$body" in
		*'"error":"auth_'*) say "self-test: API OK (rejects unauthenticated requests)" ;;
		*) warn "self-test: unexpected API answer: $(printf '%s' "$body" | head -c 200)"; FAILED=1 ;;
	esac
	if [ "$(uci -q get netbird_gl.main.auth)" != password ]; then
		if "$DESTDIR/usr/sbin/netbird-gl" auth-selftest; then
			say "self-test: GL session authentication OK"
		else
			warn "self-test: GL session authentication cannot be verified on this firmware."
			warn "switching to password mode - set the password now with: netbird-gl set-password"
			uci set netbird_gl.main.auth=password
			uci commit netbird_gl
		fi
	fi
fi

cat <<EOF

=====================================================================
 netbird-gl $VER installed.
 Panel:      APPLICATIONS -> NetBird   (http://$IP/netbird-gl/)
 Next step:  paste a setup key in "Enroll this router".
 Toggle:     SYSTEM -> Toggle Button Settings -> NetBird-VPN or NetBird-Exit-Node
 Upgrade:    unpack a newer package and run install.sh again
 Uninstall:  sh /usr/lib/netbird-gl/uninstall.sh [--purge]
=====================================================================
EOF
[ $FAILED = 0 ] || die "self-test failed - see the messages above"
