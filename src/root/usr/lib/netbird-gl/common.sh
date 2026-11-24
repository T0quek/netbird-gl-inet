#!/bin/sh
# netbird-gl - shared library (POSIX sh / BusyBox ash)
# Every path can be overridden through the environment (used by the test suite).

: "${NBGL_VERSION:=1.0.0}"
: "${NB_BIN:=/usr/bin/netbird}"
: "${NB_SOCK:=/var/run/netbird.sock}"
: "${NB_LOG:=/var/log/netbird/client.log}"
: "${NB_INIT:=/etc/init.d/netbird}"
: "${NBGL_CFG:=netbird_gl}"
: "${NBGL_RUN:=/var/run/netbird-gl}"
: "${NBGL_TMP:=/tmp}"
: "${NBGL_GH_API:=https://api.github.com/repos/netbirdio/netbird/releases/latest}"
: "${NBGL_GH_LATEST:=https://github.com/netbirdio/netbird/releases/latest}"
: "${NBGL_GH_DL:=https://github.com/netbirdio/netbird/releases/download}"
: "${NBGL_GL_CONF:=/etc/nginx/conf.d/gl.conf}"
: "${NBGL_SWITCH_CFG:=switch-button}"
: "${NBGL_LIB:=/usr/lib/netbird-gl}"
: "${NBGL_DNS_ADDR:=127.0.0.1:5053}"
: "${NBGL_DNSMASQ_INIT:=/etc/init.d/dnsmasq}"
: "${NBGL_ETC_RESOLV:=/etc/resolv.conf}"
: "${NBGL_CLI:=/usr/sbin/netbird-gl}"
: "${NBGL_RESOLV_OWN:=/tmp/resolv.conf.netbird}"
: "${NBGL_RESOLV_AUTO:=/tmp/resolv.conf.d/resolv.conf.auto}"

umask 077
mkdir -p "$NBGL_RUN" 2>/dev/null

log() { logger -t netbird-gl "$*" 2>/dev/null; }
now() { date +%s; }

# ---------------------------------------------------------------- UCI ----
cfg_get() { uci -q get "$NBGL_CFG.$1" 2>/dev/null; }
cfg_def() { _v=$(cfg_get "$1"); if [ -n "$_v" ]; then echo "$_v"; else echo "$2"; fi; }
# write only when the value changes (saves flash wear)
cfg_set() {
	[ "$(cfg_get "$1")" = "$2" ] && return 0
	uci -q set "$NBGL_CFG.$1=$2"
}
cfg_commit() { uci -q commit "$NBGL_CFG"; }

# ---------------------------------------------------------- validation ---
# All checks use grep -E (musl regex) on a single line - no shell bracket
# patterns, which differ between BusyBox builds.
nl='
'
re_ok() { # $1 extended regex, $2 value
	case "$2" in *"$nl"*) return 1 ;; esac
	printf '%s\n' "$2" | grep -Eq "$1"
}
valid_url() {
	[ ${#1} -le 200 ] || return 1
	re_ok '^https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$' "$1" || return 1
	case "$1" in *..*) return 1 ;; esac
	_port=$(printf '%s\n' "$1" | sed -n 's|^https://[^/:]*:\([0-9]*\).*$|\1|p')
	[ -z "$_port" ] || { [ "$_port" -ge 1 ] && [ "$_port" -le 65535 ]; }
}
valid_setup_key() { re_ok '^[A-Za-z0-9-]{8,128}$' "$1"; }
# NetBird network IDs are free-form names, e.g. "Exit Node (Homelab)"
valid_netid() { re_ok '^[^-[:cntrl:]][^[:cntrl:]]{0,63}$' "$1"; }
valid_domain() { re_ok '^[A-Za-z0-9_]([A-Za-z0-9_.-]{0,251}[A-Za-z0-9])?$' "$1"; }
valid_bool() { case "$1" in 0 | 1) return 0 ;; esac; return 1; }
valid_int() { # $1 value, $2 min, $3 max
	re_ok '^[0-9]{1,6}$' "$1" || return 1
	[ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}
valid_version() { re_ok '^[0-9]{1,4}[.][0-9]{1,4}[.][0-9]{1,4}$' "$1"; }
valid_sha256() { re_ok '^[0-9a-f]{64}$' "$1"; }
valid_alnum() { re_ok "^[A-Za-z0-9]{$2,$3}\$" "$1"; }  # $1 value, $2 min, $3 max length

# application/x-www-form-urlencoded value -> text (printable ASCII only)
urldec() { printf '%s\n' "$1" | sed -f "$NBGL_LIB/urldec.sed"; }

# exit 0 when version $1 > $2 (x.y.z)
version_gt() {
	awk -v a="$1" -v b="$2" 'BEGIN{
		split(a,x,"."); split(b,y,".");
		for(i=1;i<=3;i++){ if((x[i]+0)>(y[i]+0)) exit 0; if((x[i]+0)<(y[i]+0)) exit 1 }
		exit 1 }'
}

# --------------------------------------------------------------- JSON ----
json_esc() {
	printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037' | awk '
		{ gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); gsub(/\t/,"\\t"); gsub(/\r/,"\\r");
		  if (NR>1) printf "\\n"; printf "%s",$0 }'
}
json_str() { printf '"%s"' "$(json_esc "$1")"; }
json_num() { if re_ok '^[0-9]{1,15}$' "$1"; then printf '%s' "$1"; else printf '0'; fi; }
json_bool() { if [ "$1" = 1 ] || [ "$1" = true ]; then printf 'true'; else printf 'false'; fi; }

# ------------------------------------------------------------ locking ----
# atomic mkdir lock with stale-owner detection
lock_take() { # $1 name
	_l="$NBGL_RUN/$1.lock"
	if mkdir "$_l" 2>/dev/null; then echo $$ >"$_l/pid"; return 0; fi
	_p=$(cat "$_l/pid" 2>/dev/null)
	if [ -n "$_p" ] && kill -0 "$_p" 2>/dev/null; then return 1; fi
	rm -rf "$_l"
	mkdir "$_l" 2>/dev/null && echo $$ >"$_l/pid"
}
lock_drop() { rm -rf "$NBGL_RUN/$1.lock"; }

# --------------------------------------------------------------- HTTP ----
# TLS is always verified.  $1 url, $2 output file, $3 timeout
http_get() {
	if command -v curl >/dev/null 2>&1; then
		curl -fsSL --proto '=https' --tlsv1.2 --max-time "${3:-60}" \
			-H 'User-Agent: netbird-gl' -o "$2" "$1"
	elif command -v uclient-fetch >/dev/null 2>&1; then
		uclient-fetch -q -T "${3:-60}" -O "$2" "$1"
	else
		return 127
	fi
}
# Location header of a redirect (fallback when the GitHub API is unavailable)
http_location() {
	command -v curl >/dev/null 2>&1 || return 1
	curl -fsS --proto '=https' --max-time 20 -o /dev/null -w '%{redirect_url}' "$1"
}

# --------------------------------------------------------- GL router -----
# HTTP port of the GL admin panel (same detection as GL's own scripts)
gl_http_port() {
	_p=$(grep -E '^ *listen [0-9]+;' "$NBGL_GL_CONF" 2>/dev/null | grep -oE '[0-9]+' | head -n1)
	echo "${_p:-80}"
}
gl_rpc_url() {
	[ -n "${NBGL_RPC_URL:-}" ] && { echo "$NBGL_RPC_URL"; return; }
	_p=$(gl_http_port)
	if [ "$_p" = 80 ]; then echo "http://127.0.0.1/rpc"; else echo "http://127.0.0.1:$_p/rpc"; fi
}
lan_ip() {
	_ip=$(uci -q get network.lan.ipaddr 2>/dev/null)
	_ip=${_ip%%/*}
	echo "${_ip:-192.168.8.1}"
}

# ------------------------------------------------------------ NetBird ----
arch_name() {
	case "${NBGL_ARCH:-$(uname -m)}" in
		aarch64 | arm64) echo arm64 ;;
		x86_64 | amd64) echo amd64 ;;
		armv6* | armv7* | armhf) echo armv6 ;;
		*) return 1 ;;
	esac
}
with_timeout() { # $1 seconds, rest: command (binaries only, not shell functions)
	_t=$1; shift
	if command -v timeout >/dev/null 2>&1; then timeout "$_t" "$@"; else "$@"; fi
}
nb() { "$NB_BIN" --daemon-addr "unix://$NB_SOCK" "$@"; }
nbt() { # $1 time limit (s), rest: netbird arguments
	_to=$1; shift
	with_timeout "$_to" "$NB_BIN" --daemon-addr "unix://$NB_SOCK" "$@"
}
nb_present() { [ -x "$NB_BIN" ]; }
nb_running() { [ -S "$NB_SOCK" ]; }
nb_version() {
	nb_present || return 1
	"$NB_BIN" version 2>/dev/null | head -n1 | tr -cd '0-9.'
}
nb_status_json() { nb_running && nbt 10 status --json 2>/dev/null; }
# Idle | Connecting | Connected | NeedsLogin | LoginFailed | SessionExpired | Stopped
nb_daemon_status() {
	_j=$(nb_status_json) || { echo Stopped; return; }
	_s=$(printf '%s' "$_j" | jsonfilter -e '@.daemonStatus' 2>/dev/null)
	echo "${_s:-Unknown}"
}

# Parses "netbird networks list" -> lines: id<TAB>network<TAB>selected(1/0)
nb_networks() {
	nb_running || return 0
	nbt 10 networks list 2>/dev/null | tr -d '\r' | awk '
		function flush(){ if(id!="") printf "%s\t%s\t%d\n", id, net, sel }
		/^ *- ID: /     { flush(); id=$0; sub(/^ *- ID: /,"",id); net=""; sel=0; next }
		/^ *Network: /  { net=$0; sub(/^ *Network: /,"",net); next }
		/^ *Domains: /  { net=$0; sub(/^ *Domains: /,"dns:",net); next }
		/^ *Status: /   { sel=($0 ~ /Status: Selected *$/)?1:0; next }
		END { flush() }'
}
is_exit_net() { [ "$1" = "0.0.0.0/0" ] || [ "$1" = "::/0" ]; }

# flags supported by "netbird up" (cached) - client version detection
nb_has_flag() {
	_c="$NBGL_RUN/upflags"
	# shellcheck disable=SC3013
	if [ ! -s "$_c" ] || [ "$NB_BIN" -nt "$_c" ]; then
		"$NB_BIN" up --help 2>/dev/null | grep -o -- '--[a-z0-9-]*' | sort -u >"$_c"
	fi
	grep -qx -- "--$1" "$_c"
}

# Bring the connection up using the UCI settings.  $1 (optional) setup key file
nb_up() {
	_key=${1:-}
	_mgmt=$(cfg_def main.management_url https://api.netbird.io:443)
	valid_url "$_mgmt" || { log "invalid management_url"; return 2; }
	set -- up --management-url "$_mgmt"
	if [ "$(cfg_def main.accept_routes 1)" = 1 ]; then
		set -- "$@" --disable-client-routes=false
	else
		set -- "$@" --disable-client-routes=true
	fi
	if [ "$(cfg_def main.use_dns 1)" = 1 ]; then
		set -- "$@" --disable-dns=false
		# fixed local resolver address, forwarded to by dnsmasq (see dns_sync)
		nb_has_flag dns-resolver-address && set -- "$@" --dns-resolver-address "$NBGL_DNS_ADDR"
	else
		set -- "$@" --disable-dns=true
	fi
	# the router is neither a routing peer nor an SSH server
	nb_has_flag disable-server-routes && set -- "$@" --disable-server-routes=true
	nb_has_flag allow-server-ssh && set -- "$@" --allow-server-ssh=false
	if nb_has_flag block-inbound; then
		if [ "$(cfg_def main.allow_inbound 0)" = 1 ]; then
			set -- "$@" --block-inbound=false
		else
			set -- "$@" --block-inbound=true
		fi
	fi
	[ -n "$_key" ] && set -- "$@" --setup-key-file "$_key"
	nbt 60 "$@" >"$NBGL_RUN/last_up.log" 2>&1
}
nb_down() { nbt 30 down >/dev/null 2>&1; }
