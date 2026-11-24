#!/bin/sh
# Builds the netbird-gl package:
#   1. lint (shellcheck, node --check)
#   2. tests against the sources
#   3. production package (minified scripts, HTML/CSS/JS, gzipped panel view)
#   4. tests against the built package
#   5. dist/netbird-gl-<version>.tar.gz + SHA256SUMS
# Requires: busybox, shellcheck, python3, zip-capable python, node + "npm ci --prefix tools"
set -eu
cd "$(dirname "$0")"
VER=$(cat VERSION)
B=build/netbird-gl
T=tools/node_modules/.bin

echo "== lint"
shellcheck -s sh -e SC3043,SC2034,SC2015,SC1091,SC3013,SC2012,SC2119,SC2120 \
	src/install.sh src/root/usr/sbin/netbird-gl src/root/usr/lib/netbird-gl/*.sh \
	src/root/usr/lib/netbird-gl/api.cgi src/root/etc/gl-switch.d/*.sh tests/gl-busybox/*
shellcheck -s sh -e SC2034 src/root/etc/init.d/netbird src/root/etc/init.d/netbird-gl
node --check src/root/www/netbird-gl/app.js
grep -q "NBGL_VERSION:=$VER}" src/root/usr/lib/netbird-gl/common.sh || { echo "VERSION != common.sh"; exit 1; }

echo "== tests (sources)"
python3 tests/test_netbird_gl.py >"${TMPDIR:-/tmp}/netbird-gl-test.log" 2>&1 || { cat "${TMPDIR:-/tmp}/netbird-gl-test.log"; exit 1; }
tail -n 3 "${TMPDIR:-/tmp}/netbird-gl-test.log"

echo "== assembling package"
rm -rf build
mkdir -p "$B" dist
cp -a src/root "$B/root"
cp -a src/nginx "$B/nginx"
cp src/install.sh "$B/install.sh"
cp VERSION "$B/VERSION"
mkdir -p "$B/panel"
gzip -9n <src/panel/gl-sdk4-ui-netbirdgl.common.js >"$B/panel/gl-sdk4-ui-netbirdgl.common.js.gz"

# shell minification: drops full-line comments, indentation and empty lines
# (shebang kept; no here-document in the scripts has lines starting with '#')
shmin() {
	awk 'NR==1 && /^#!/ { print; next }
	     { sub(/^[ \t]+/, "") }
	     /^#/ { next }
	     /^$/ { next }
	     { print }' "$1" >"$1.min" && mv "$1.min" "$1"
}
for f in "$B/install.sh" "$B/root/usr/sbin/netbird-gl" "$B/root/usr/lib/netbird-gl/"*.sh \
	"$B/root/usr/lib/netbird-gl/api.cgi" "$B"/root/etc/gl-switch.d/*.sh \
	"$B/root/etc/init.d/netbird" "$B/root/etc/init.d/netbird-gl"; do
	shmin "$f"
	sh -n "$f"
	chmod 755 "$f"
done
for f in "$B"/nginx/*.conf; do sed -i '/^[[:space:]]*#/d' "$f"; done

W="$B/root/www/netbird-gl"
"$T/terser" "$W/app.js" -c -m --comments false -o "$W/app.js.min" && mv "$W/app.js.min" "$W/app.js"
node -e "const c=require('./tools/node_modules/csso');const fs=require('fs');fs.writeFileSync(process.argv[1],c.minify(fs.readFileSync(process.argv[1],'utf8')).css)" "$W/app.css"
"$T/html-minifier-terser" --collapse-whitespace --remove-comments --remove-redundant-attributes \
	--remove-script-type-attributes --remove-style-link-type-attributes -o "$W/index.html.min" "$W/index.html"
mv "$W/index.html.min" "$W/index.html"
node --check "$W/app.js"

echo "== tests (built package)"
NBGL_SRC="$PWD/$B" NBGL_PKG="$PWD/$B" python3 tests/test_netbird_gl.py >"${TMPDIR:-/tmp}/netbird-gl-test.log" 2>&1 || { cat "${TMPDIR:-/tmp}/netbird-gl-test.log"; exit 1; }
tail -n 3 "${TMPDIR:-/tmp}/netbird-gl-test.log"

echo "== archive"
find "$B" -type f -name '*.min' -delete
OUT="dist/netbird-gl-$VER.tar.gz"
tar --owner=0 --group=0 --numeric-owner --sort=name -C build -cf - netbird-gl | gzip -9n >"$OUT"
ZIP="dist/netbird-gl-$VER.zip"
rm -f "$ZIP"
python3 - "$ZIP" build netbird-gl <<'PYZIP'
import os, sys, zipfile
out, base, top = sys.argv[1:]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, dirs, files in os.walk(os.path.join(base, top)):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            i = zipfile.ZipInfo(os.path.relpath(p, base), (2026, 1, 1, 0, 0, 0))
            i.external_attr = (os.stat(p).st_mode & 0o777 | 0o100000) << 16
            i.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(i, open(p, "rb").read())
PYZIP
(cd dist && sha256sum "netbird-gl-$VER.tar.gz" "netbird-gl-$VER.zip" >SHA256SUMS)
echo "sources: $(du -sk --apparent-size src | cut -f1) KB"
echo "package: $(du -sk --apparent-size "$B" | cut -f1) KB unpacked, $(wc -c <"$OUT") B archive"
cat dist/SHA256SUMS
