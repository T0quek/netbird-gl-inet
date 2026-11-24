#!/bin/sh
# netbird-gl - CGI API for the web UI (GL nginx + fcgiwrap).
# Requires the X-NBGL header and a same-origin request (CSRF), a GL admin
# session or the netbird-gl password; all input is whitelist-validated.

: "${NBGL_CLI:=/usr/sbin/netbird-gl}"
NBGL_NO_MAIN=1
# shellcheck source=../../sbin/netbird-gl
. "$NBGL_CLI"

EXTRA_HDR=""
respond() { # $1 status, $2 body
	printf 'Status: %s\r\n' "$1"
	printf 'Content-Type: application/json; charset=utf-8\r\n'
	printf 'Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n'
	printf 'X-Frame-Options: SAMEORIGIN\r\nReferrer-Policy: no-referrer\r\n'
	[ -n "$EXTRA_HDR" ] && printf '%s\r\n' "$EXTRA_HDR"
	printf '\r\n%s\n' "$2"
	exit 0
}
fail() { respond "$1" "{\"ok\":false,\"error\":$(json_str "$2")}"; }
ok() { respond "200 OK" "{\"ok\":true${1:+,$1}}"; }

# ------------------------------------------------------ decoding ------
# urldec() lives in common.sh (pure sed table)
form_has() { printf '%s\n' "$BODY" | tr '&' '\n' | grep -q "^$1="; }
form_get() {
	printf '%s\n' "$BODY" | tr '&' '\n' | while IFS= read -r _p; do
		case "$_p" in "$1="*) urldec "${_p#*=}"; break ;; esac
	done
}
cookie_get() {
	printf '%s\n' "${HTTP_COOKIE:-}" | tr ';' '\n' | sed 's/^ *//' | while IFS= read -r _c; do
		case "$_c" in "$1="*) printf '%s' "${_c#*=}"; break ;; esac
	done
}

# ------------------------------------------------------ CSRF / Origin --
[ "${HTTP_X_NBGL:-}" = 1 ] || fail "403 Forbidden" csrf
case "${HTTP_SEC_FETCH_SITE:-}" in '' | same-origin | none) ;; *) fail "403 Forbidden" csrf ;; esac
if [ -n "${HTTP_ORIGIN:-}" ]; then
	case "$HTTP_ORIGIN" in
		"http://${HTTP_HOST:-}" | "https://${HTTP_HOST:-}") ;;
		*) fail "403 Forbidden" origin ;;
	esac
fi
case "${REQUEST_METHOD:-}" in GET | POST) ;; *) fail "405 Method Not Allowed" method ;; esac

ACTION=""
case "${QUERY_STRING:-}" in a=*) ACTION=${QUERY_STRING#a=}; ACTION=${ACTION%%&*} ;; esac
[ -z "$ACTION" ] || re_ok '^[a-z_]{1,32}$' "$ACTION" || fail "400 Bad Request" action

BODY=""
if [ "$REQUEST_METHOD" = POST ]; then
	valid_int "${CONTENT_LENGTH:-0}" 0 8192 || fail "413 Payload Too Large" body
	[ "${CONTENT_LENGTH:-0}" -gt 0 ] && BODY=$(head -c "$CONTENT_LENGTH")
fi

# ------------------------------------------------------ authentication -
auth_gl() {
	_sid=$(cookie_get Admin-Token)
	valid_alnum "$_sid" 16 128 || return 1
	rpc_trustworthy || return 2
	_k=$(printf '%s' "$_sid" | sha256sum | cut -c1-40)
	_f="$NBGL_RUN/auth/$_k"
	if [ -f "$_f" ] && [ $(($(now) - $(cat "$_f"))) -lt 60 ]; then return 0; fi
	rpc_sid_ok "$_sid" || return 1
	mkdir -p "$NBGL_RUN/auth"
	# shellcheck disable=SC2012
	[ "$(ls "$NBGL_RUN/auth" | wc -l)" -gt 50 ] && rm -f "$NBGL_RUN/auth/"*
	now >"$_f"
	return 0
}
auth_pw() {
	_t=$(cookie_get NBGL)
	re_ok '^[0-9a-f]{48}$' "$_t" || return 1
	_f="$NBGL_RUN/sessions/$_t"
	[ -f "$_f" ] || return 1
	[ "$(cat "$_f")" -gt "$(now)" ] || { rm -f "$_f"; return 1; }
	echo $(($(now) + 3600)) >"$_f"
	return 0
}
pw_login() {
	_pf="$NBGL_RUN/pw_fail"
	_n=0; _ft=0
	[ -f "$_pf" ] && read -r _n _ft <"$_pf"
	if [ "${_n:-0}" -ge 5 ] && [ $(($(now) - ${_ft:-0})) -lt 300 ]; then fail "429 Too Many Requests" locked; fi
	_salt=$(cfg_get main.pw_salt)
	_hash=$(cfg_get main.pw_hash)
	if [ -z "$_salt" ] || [ -z "$_hash" ]; then fail "503 Service Unavailable" no_password; fi
	_pw=$(form_get password)
	if [ ${#_pw} -lt 1 ] || [ ${#_pw} -gt 128 ] || [ "$(pw_hash "$_salt" "$_pw")" != "$_hash" ]; then
		[ $(($(now) - ${_ft:-0})) -ge 300 ] && _n=0
		echo "$((${_n:-0} + 1)) $(now)" >"$_pf"
		sleep 2
		fail "401 Unauthorized" bad_password
	fi
	rm -f "$_pf"
	mkdir -p "$NBGL_RUN/sessions"
	_t=$(rand_hex 24)
	[ ${#_t} -eq 48 ] || fail "500 Internal Server Error" random
	echo $(($(now) + 3600)) >"$NBGL_RUN/sessions/$_t"
	_sec=""
	[ "${HTTP_X_FORWARDED_PROTO:-}" = https ] && _sec="; Secure"
	EXTRA_HDR="Set-Cookie: NBGL=$_t; Path=/netbird-gl/; HttpOnly; SameSite=Strict$_sec"
	ok
}

AUTH_MODE=$(cfg_def main.auth gl)
if [ "$AUTH_MODE" = password ]; then
	if [ "$ACTION" = login ] && [ "$REQUEST_METHOD" = POST ]; then pw_login; fi
	auth_pw || fail "401 Unauthorized" auth_password
	if [ "$ACTION" = logout ]; then
		rm -f "$NBGL_RUN/sessions/$(cookie_get NBGL)"
		EXTRA_HDR="Set-Cookie: NBGL=; Path=/netbird-gl/; Max-Age=0; HttpOnly; SameSite=Strict"
		ok
	fi
else
	auth_gl
	case $? in
		0) ;;
		2) fail "503 Service Unavailable" auth_gl_unavailable ;;
		*) fail "401 Unauthorized" auth_gl ;;
	esac
fi

# ------------------------------------------------------ actions --------
if [ "$REQUEST_METHOD" = GET ]; then
	case "$ACTION" in
		status | '') respond "200 OK" "$(status_json)" ;;
		*) fail "400 Bad Request" action ;;
	esac
fi

case "$ACTION" in
	connect) set_vpn 1; ok ;;
	disconnect) set_vpn 0; ok ;;
	restart)
		[ -x "$NB_INIT" ] && "$NB_INIT" restart >/dev/null 2>&1
		ok ;;
	exit_on)
		set_exit 1
		case $? in
			0) ok ;;
			3) fail "409 Conflict" accept_routes_off ;;
			*) fail "409 Conflict" no_exit_node ;;
		esac ;;
	exit_off) set_exit 0; ok ;;
	settings)
		set --
		for _f in switch_func switch_invert exit_node exit_node_idx accept_routes dns_extra use_dns allow_inbound \
			guest_access auto_check auto_update update_delay_days kill_switch; do
			form_has "$_f" && set -- "$@" "$_f=$(form_get "$_f")"
		done
		[ $# -gt 0 ] || fail "400 Bad Request" empty
		BAD_FIELD=""
		settings_apply "$@" || fail "400 Bad Request" "invalid_$BAD_FIELD"
		ok ;;
	enroll)
		_u=$(form_get management_url)
		_k=$(form_get setup_key)
		if ! valid_url "$_u"; then
			log "enroll: rejected management URL '$(printf '%s' "$_u" | tr -cd 'A-Za-z0-9.:/_~%-' | head -c 120)'"
			fail "400 Bad Request" invalid_management_url
		fi
		valid_setup_key "$_k" || fail "400 Bad Request" invalid_setup_key
		nb_present || fail "409 Conflict" no_binary
		_kf=$(mktemp "$NBGL_RUN/sk.XXXXXX") || fail "500 Internal Server Error" tmp
		printf '%s' "$_k" >"$_kf"
		enroll "$_u" "$_kf"
		_r=$?
		rm -f "$_kf"
		[ $_r -eq 0 ] || fail "502 Bad Gateway" enroll_failed
		ok ;;
	update_check)
		upd_check force || fail "502 Bad Gateway" check_failed
		ok "\"latest\":$(json_str "$(cfg_get update.latest)")" ;;
	update_now)
		[ -f "$NBGL_RUN/job" ] && fail "409 Conflict" busy
		if command -v setsid >/dev/null 2>&1; then
			setsid "$NBGL_CLI" update latest </dev/null >/dev/null 2>&1 &
		else
			"$NBGL_CLI" update latest </dev/null >/dev/null 2>&1 &
		fi
		ok ;;
	*) fail "400 Bad Request" action ;;
esac
