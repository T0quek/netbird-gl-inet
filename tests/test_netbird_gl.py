#!/usr/bin/env python3
"""netbird-gl test suite. Scripts run under BusyBox ash (as on the router) with
mocks for uci, netbird, jsonfilter, curl and init.d.  Run: python3 tests/test_netbird_gl.py"""
import gzip, hashlib, io, json, os, shutil, stat, subprocess, sys, tarfile, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("NBGL_SRC", os.path.join(HERE, "..", "src"))
ROOT = os.path.join(SRC, "root")
MOCKS = os.path.join(HERE, "mocks")
# applets missing from the GL BusyBox build shadow the host tools
GL_GAPS = os.path.join(HERE, "gl-busybox")
# every other tool comes from BusyBox, exactly as on the router
BB_DIR = os.path.join(tempfile.gettempdir(), "nbgl-busybox-applets")
if not os.path.isdir(BB_DIR):
    os.makedirs(BB_DIR)
    _bb = shutil.which("busybox")
    for _a in subprocess.run([_bb, "--list"], capture_output=True, text=True).stdout.split():
        if _a not in ("busybox",):
            try:
                os.symlink(_bb, os.path.join(BB_DIR, _a))
            except FileExistsError:
                pass
CLI = os.path.join(ROOT, "usr/sbin/netbird-gl")
CGI = os.path.join(ROOT, "usr/lib/netbird-gl/api.cgi")
ASH = ["busybox", "ash"]
GOOD_KEY = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
SID = "validsid0123456789abcdef"


class Env:
    def __init__(self):
        self.d = tempfile.mkdtemp(prefix="nbgl-test-")
        for sub in ("uci", "ucisrc", "nbm", "run", "tmp", "bin", "fix", "log", "dnsmasq.d"):
            os.makedirs(os.path.join(self.d, sub))
        shutil.copy(os.path.join(ROOT, "etc/config/netbird_gl"), self.p("ucisrc/netbird_gl"))
        open(self.p("ucisrc/firewall"), "w").write("config defaults\n")
        open(self.p("ucisrc/switch-button"), "w").write("config main\n\toption func 'vpn'\n")
        open(self.p("ucisrc/network"), "w").write("config interface 'lan'\n\toption ipaddr '10.10.0.1'\n")
        # NetBird binary = mock
        self.set_binary(self.p("bin/netbird"), None, ok=True)
        self.sock = self.p("run/netbird.sock")
        self.env = dict(os.environ)
        self.env.update({
            "PATH": GL_GAPS + ":" + MOCKS + ":" + BB_DIR + ":/usr/bin:/bin:/usr/sbin:/sbin",
            "UCI_DIR": self.p("uci"), "UCI_SRC": self.p("ucisrc"), "NBM_DIR": self.p("nbm"),
            "FIX_DIR": self.p("fix"), "NB_SOCK": self.sock, "NB_BIN": self.p("bin/netbird"),
            "NB_LOG": self.p("log/client.log"), "NB_INIT": os.path.join(MOCKS, "init-netbird"),
            "NBGL_RUN": self.p("run/nbgl"), "NBGL_TMP": self.p("tmp"),
            "NBGL_LIB": os.path.join(ROOT, "usr/lib/netbird-gl"), "NBGL_CLI": CLI,
            "NBGL_ARCH": "aarch64", "NBGL_RPC_URL": "http://127.0.0.1/rpc",
            "NBGL_DNSMASQ_DIR": self.p("dnsmasq.d"), "NBGL_DNSMASQ_INIT": os.path.join(MOCKS, "init-dnsmasq"),
            "NBGL_ETC_RESOLV": self.p("etc/resolv.conf"), "NBGL_RESOLV_OWN": self.p("tmp/resolv.conf.netbird"),
            "NBGL_RESOLV_AUTO": self.p("tmp/resolv.conf.d/resolv.conf.auto"),
        })
        # GL layout: /etc/resolv.conf -> /tmp/resolv.conf -> resolv.conf.auto
        for d in ("etc", "tmp/resolv.conf.d"):
            os.makedirs(self.p(d), exist_ok=True)
        open(self.p("tmp/resolv.conf.d/resolv.conf.auto"), "w").write("# Interface wan\nnameserver 192.168.1.1\n")
        os.symlink(self.p("tmp/resolv.conf.d/resolv.conf.auto"), self.p("tmp/resolv.conf"))
        os.symlink(self.p("tmp/resolv.conf"), self.p("etc/resolv.conf"))
        open(self.p("log/client.log"), "w").write("2026-11-23 INFO started\n\x1b[31mred\x1b[0m \"quote\" \\ back\n")

    def p(self, rel):
        return os.path.join(self.d, rel)

    def set_binary(self, path, version, ok=True):
        lines = ["#!/bin/sh"]
        if version:
            lines.append('[ "$1" = version ] && { echo %s; exit 0; }' % version)
        if not ok:
            lines.append('[ "$1" = selftest ] && exit 1')
        lines.append('exec python3 %s "$@"' % os.path.join(MOCKS, "netbird"))
        open(path, "w").write("\n".join(lines) + "\n")
        os.chmod(path, 0o755)

    def nbstate(self, **kw):
        p = self.p("nbm/state.json")
        st = json.load(open(p)) if os.path.exists(p) else {}
        st.update(kw)
        json.dump(st, open(p, "w"))
        return st

    def nb(self):
        # the mock may be rewriting the file right now (background hook)
        for _ in range(20):
            try:
                return json.load(open(self.p("nbm/state.json")))
            except ValueError:
                time.sleep(0.05)
        return json.load(open(self.p("nbm/state.json")))

    def start_daemon(self):
        subprocess.run([os.path.join(MOCKS, "init-netbird"), "start"], env=self.env, check=True)

    def cli(self, *args, stdin=None, check=True):
        r = subprocess.run(ASH + [CLI] + list(args), env=self.env, input=stdin, capture_output=True, text=True, timeout=120)
        if check and r.returncode != 0:
            raise AssertionError("cli %s rc=%d\n%s\n%s" % (args, r.returncode, r.stdout, r.stderr))
        return r

    def uci(self, *args):
        return subprocess.run([os.path.join(MOCKS, "uci"), "-q"] + list(args), env=self.env,
                              capture_output=True, text=True).stdout.strip()

    def sh(self, code):
        """call library functions under ash"""
        script = 'NBGL_NO_MAIN=1; . "%s"; %s' % (CLI, code)
        return subprocess.run(ASH + ["-c", script], env=self.env, capture_output=True, text=True, timeout=120)

    def cgi(self, method="GET", action="status", body="", headers=None, xnbgl=True, cookie=None):
        e = dict(self.env)
        e.update({"REQUEST_METHOD": method, "QUERY_STRING": "a=" + action, "HTTP_HOST": "192.168.8.1",
                  "CONTENT_LENGTH": str(len(body.encode()))})
        if xnbgl:
            e["HTTP_X_NBGL"] = "1"
        e["HTTP_COOKIE"] = cookie if cookie is not None else "foo=bar; Admin-Token=" + SID
        for k, v in (headers or {}).items():
            e[k] = v
        r = subprocess.run(ASH + [CGI], env=e, input=body.encode(), capture_output=True, timeout=120)
        head, _, payload = r.stdout.decode().partition("\r\n\r\n")
        status = int(head.split("\r\n")[0].split()[1])
        hdrs = dict(l.split(": ", 1) for l in head.split("\r\n") if ": " in l)
        return status, hdrs, json.loads(payload)

    def cleanup(self):
        shutil.rmtree(self.d, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.cleanup()


class TestLibrary(Base):
    def t(self, fn, val):
        return self.e.sh('%s "$V" && echo yes || echo no' % fn).stdout.strip() if False else \
            subprocess.run(ASH + ["-c", 'NBGL_NO_MAIN=1; . "%s"; %s "$V" && echo yes || echo no' % (CLI, fn)],
                           env=dict(self.e.env, V=val), capture_output=True, text=True).stdout.strip()

    def test_valid_url(self):
        for u in ("https://api.netbird.io:443", "https://nb.example.com", "https://10.0.0.1:33073/x/y"):
            self.assertEqual(self.t("valid_url", u), "yes", u)
        for u in ("http://api.netbird.io", "https://", "https://a.b:0", "https://a.b:70000", "https://a b",
                  "https://x.y;reboot", "https://x.y$(id)", "https://x.y\nhttps://z", "https://user@x.y",
                  "https://x.y/'", "https://:443", "https://x..y", "ftp://x"):
            self.assertEqual(self.t("valid_url", u), "no", repr(u))

    def test_valid_other(self):
        self.assertEqual(self.t("valid_setup_key", GOOD_KEY), "yes")
        for k in ("short", GOOD_KEY + ";id", GOOD_KEY + "\n", "a" * 200, "$(reboot)xxxx"):
            self.assertEqual(self.t("valid_setup_key", k), "no", k)
        for n in ("home-exit", "Exit Node 1", "id.with:colon", "Exit Node (Homelab)", "x;y"):
            self.assertEqual(self.t("valid_netid", n), "yes", n)
        for n in ("", "-a", "x\ny", "x\ty", "a" * 65):
            self.assertEqual(self.t("valid_netid", n), "no", repr(n))
        for v in ("0.81.4", "10.0.12"):
            self.assertEqual(self.t("valid_version", v), "yes")
        for v in ("0.81", "0.81.4.1", "v0.81.4", "0.81.4-rc1", "../x", ""):
            self.assertEqual(self.t("valid_version", v), "no", v)

    def test_rand_hex_without_od(self):
        out = self.e.sh("rand_hex 16; echo; rand_hex 24").stdout.split()
        self.assertEqual([len(x) for x in out], [32, 48])
        self.assertTrue(all(c in "0123456789abcdef" for c in "".join(out)))
        self.assertNotEqual(out[0], self.e.sh("rand_hex 16").stdout.strip())

    def test_router_selftest(self):
        r = self.e.cli("selftest", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_urldec(self):
        for enc, dec in (("https%3A%2F%2Fnetbird.vpn.example.pl%3A443", "https://netbird.vpn.example.pl:443"),
                         ("P%40ss+w%26rd%7C%5C%27%22%25", "P@ss w&rd|\\'\"%"), ("a%0Ab", "a%0Ab")):
            r = subprocess.run(ASH + ["-c", 'NBGL_NO_MAIN=1; . "%s"; urldec "$V"' % CLI],
                               env=dict(self.e.env, V=enc), capture_output=True, text=True)
            self.assertEqual(r.stdout.rstrip("\n"), dec, enc)

    def test_version_gt(self):
        cases = [("0.81.4", "0.80.9", 0), ("0.80.10", "0.80.9", 0), ("1.0.0", "0.99.99", 0),
                 ("0.80.0", "0.80.0", 1), ("0.79.9", "0.80.0", 1)]
        for a, b, rc in cases:
            r = self.e.sh('version_gt %s %s' % (a, b))
            self.assertEqual(r.returncode, rc, (a, b))

    def test_json_escape(self):
        nasty = 'a"b\\c\nd\te\x01f\x1b[0mg ąę 😀 </script>'
        r = subprocess.run(ASH + ["-c", 'NBGL_NO_MAIN=1; . "%s"; json_str "$V"' % CLI],
                           env=dict(self.e.env, V=nasty), capture_output=True, text=True)
        val = json.loads(r.stdout)
        self.assertEqual(val, 'a"b\\c\nd\tef[0mg ąę 😀 </script>')

    def test_networks_parser(self):
        self.e.start_daemon()
        self.e.nbstate(networks=[{"id": "home-exit", "net": "0.0.0.0/0", "sel": True},
                                 {"id": "Exit Node 2", "net": "0.0.0.0/0", "sel": False},
                                 {"id": "lan", "net": "192.168.1.0/24", "sel": True}])
        out = self.e.sh("nb_networks").stdout
        self.assertEqual(out.splitlines(), ["home-exit\t0.0.0.0/0\t1", "Exit Node 2\t0.0.0.0/0\t0",
                                            "lan\t192.168.1.0/24\t1"])


class TestCgiSecurity(Base):
    def setUp(self):
        super().setUp()
        self.e.start_daemon()

    def test_requires_custom_header(self):
        s, _, d = self.e.cgi(xnbgl=False)
        self.assertEqual((s, d["error"]), (403, "csrf"))

    def test_rejects_cross_site(self):
        s, _, d = self.e.cgi(method="POST", action="disconnect", headers={"HTTP_ORIGIN": "https://evil.example"})
        self.assertEqual((s, d["error"]), (403, "origin"))
        s, _, d = self.e.cgi(headers={"HTTP_SEC_FETCH_SITE": "cross-site"})
        self.assertEqual((s, d["error"]), (403, "csrf"))
        s, _, d = self.e.cgi(headers={"HTTP_ORIGIN": "http://192.168.8.1"})
        self.assertEqual(s, 200)

    def test_requires_gl_session(self):
        for c in ("", "Admin-Token=wrongsid0123456789abcd", "Admin-Token=" + SID + "%3B", "Admin-Token=$(reboot)aaaaaaaaaaaa"):
            s, _, d = self.e.cgi(cookie=c)
            self.assertEqual((s, d["error"]), (401, "auth_gl"), c)
        s, h, d = self.e.cgi()
        self.assertEqual(s, 200)
        self.assertTrue(d["ok"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Cache-Control"], "no-store")

    def test_gl_auth_fails_closed_when_rpc_accepts_anything(self):
        open(self.e.p("fix/rpc_open"), "w").close()
        s, _, d = self.e.cgi()
        self.assertEqual((s, d["error"]), (503, "auth_gl_unavailable"))
        r = self.e.cli("auth-selftest", check=False)
        self.assertNotEqual(r.returncode, 0)

    def test_auth_selftest_ok(self):
        self.assertEqual(self.e.cli("auth-selftest", check=False).returncode, 0)

    def test_status_json_valid_and_escaped(self):
        s, _, d = self.e.cgi()
        self.assertIn("\\", d["log"])
        self.assertNotIn("\x1b", d["log"])
        self.assertEqual(d["nb"]["peers"]["details"][0]["fqdn"], "<img src=x onerror=alert(1)>.netbird.cloud")
        self.assertEqual(d["switch"]["func"], "vpn")
        self.assertFalse(d["state"]["exit"])

    def test_method_and_action_validation(self):
        e = self.e
        s, _, d = e.cgi(method="DELETE")
        self.assertEqual(s, 405)
        s, _, d = e.cgi(method="POST", action="rm;reboot")
        self.assertEqual((s, d["error"]), (400, "action"))
        s, _, d = e.cgi(method="POST", action="settings", body="x" * 9000)
        self.assertEqual(s, 413)

    def test_settings_validation(self):
        e = self.e
        s, _, d = e.cgi("POST", "settings", "switch_invert=2")
        self.assertEqual((s, d["error"]), (400, "invalid_switch_invert"))
        s, _, d = e.cgi("POST", "settings", "exit_node=-x")
        self.assertEqual((s, d["error"]), (400, "invalid_exit_node"))
        # free-form names are stored literally, never executed
        s, _, d = e.cgi("POST", "settings", "exit_node=x%3B%24%28reboot%29")
        self.assertEqual(s, 200, d)
        self.assertEqual(e.uci("get", "netbird_gl.main.exit_node"), "x;$(reboot)")
        # control characters never get decoded
        s, _, d = e.cgi("POST", "settings", "exit_node=a%0Ab")
        self.assertNotIn("\n", e.uci("get", "netbird_gl.main.exit_node"))
        s, _, d = e.cgi("POST", "settings", "update_delay_days=91")
        self.assertEqual(s, 400)
        s, _, d = e.cgi("POST", "settings", "management_url=https%3A%2F%2Fevil.example")
        self.assertEqual(s, 400, "management_url must not be changeable through settings")
        s, _, d = e.cgi("POST", "settings", "switch_invert=1&exit_node=Home+Exit&update_delay_days=14")
        self.assertEqual(s, 200, d)
        self.assertEqual(e.uci("get", "netbird_gl.main.switch_invert"), "1")
        self.assertEqual(e.uci("get", "netbird_gl.main.exit_node"), "Home Exit")
        self.assertEqual(e.uci("get", "netbird_gl.main.update_delay_days"), "14")
        s, _, d = e.cgi("POST", "settings", "switch_func=rm%20-rf")
        self.assertEqual((s, d["error"]), (400, "invalid_switch_func"))
        s, _, d = e.cgi("POST", "settings", "switch_func=NetBird-Exit-Node")
        self.assertEqual(s, 200, d)
        self.assertEqual(e.uci("get", "switch-button.@main[0].func"), "NetBird-Exit-Node")
        s, _, d = e.cgi("POST", "settings", "switch_func=none")
        self.assertEqual(s, 200, d)
        self.assertEqual(e.uci("get", "switch-button.@main[0].func"), "")

    def test_enroll(self):
        e = self.e
        s, _, d = e.cgi("POST", "enroll", "management_url=https%3A%2F%2Fapi.netbird.io%3A443&setup_key=bad%3Bkey")
        self.assertEqual((s, d["error"]), (400, "invalid_setup_key"))
        s, _, d = e.cgi("POST", "enroll", "management_url=http%3A%2F%2Fx.y&setup_key=" + GOOD_KEY)
        self.assertEqual((s, d["error"]), (400, "invalid_management_url"))
        s, _, d = e.cgi("POST", "enroll", "management_url=https%3A%2F%2Fapi.netbird.io%3A443&setup_key=AAAAAAAA-0000")
        self.assertEqual((s, d["error"]), (502, "enroll_failed"))
        s, _, d = e.cgi("POST", "enroll", "management_url=https%3A%2F%2Fnetbird.vpn.example.pl&setup_key=" + GOOD_KEY)
        self.assertEqual(s, 200, d)
        s, _, d = e.cgi("POST", "enroll", "management_url=https%3A%2F%2Fnb.example.com%3A443&setup_key=" + GOOD_KEY)
        self.assertEqual(s, 200, d)
        st = e.nb()
        self.assertEqual(st["daemonStatus"], "Connected")
        # the key never reaches a command line; the temporary file is removed
        for call in st["calls"]:
            self.assertNotIn(GOOD_KEY, " ".join(call))
        self.assertFalse([f for f in os.listdir(e.p("run/nbgl")) if f.startswith("sk.")])
        up = st["up_args"]
        self.assertIn("--block-inbound=true", up)
        self.assertIn("--disable-server-routes=true", up)
        self.assertIn("--allow-server-ssh=false", up)
        self.assertEqual(up[up.index("--management-url") + 1], "https://nb.example.com:443")


class TestPasswordMode(Base):
    def setUp(self):
        super().setUp()
        self.e.start_daemon()
        self.e.cli("set-password", stdin="correct-horse-battery\n")
        subprocess.run([os.path.join(MOCKS, "uci"), "set", "netbird_gl.main.auth=password"], env=self.e.env)

    def test_login_flow(self):
        e = self.e
        s, _, d = e.cgi(cookie="")
        self.assertEqual((s, d["error"]), (401, "auth_password"))
        s, _, d = e.cgi("POST", "login", "password=wrong", cookie="")
        self.assertEqual((s, d["error"]), (401, "bad_password"))
        s, h, d = e.cgi("POST", "login", "password=correct-horse-battery", cookie="")
        self.assertEqual(s, 200)
        ck = h["Set-Cookie"]
        self.assertIn("HttpOnly", ck)
        self.assertIn("SameSite=Strict", ck)
        tok = ck.split(";")[0]
        s, _, d = e.cgi(cookie=tok)
        self.assertEqual(s, 200)
        s, _, d = e.cgi("POST", "logout", cookie=tok)
        self.assertEqual(s, 200)
        s, _, d = e.cgi(cookie=tok)
        self.assertEqual(s, 401)

    def test_bruteforce_lockout(self):
        e = self.e
        for _ in range(5):
            s, _, _ = e.cgi("POST", "login", "password=nope", cookie="")
            self.assertEqual(s, 401)
        s, _, d = e.cgi("POST", "login", "password=correct-horse-battery", cookie="")
        self.assertEqual((s, d["error"]), (429, "locked"))


class TestControl(Base):
    def setUp(self):
        super().setUp()
        self.e.start_daemon()
        self.e.nbstate(enrolled=True, daemonStatus="Connected",
                       networks=[{"id": "home-exit", "net": "0.0.0.0/0", "sel": False},
                                 {"id": "other-exit", "net": "0.0.0.0/0", "sel": True},
                                 {"id": "lan", "net": "192.168.1.0/24", "sel": True}])

    def sel(self):
        return {n["id"]: n["sel"] for n in self.e.nb()["networks"]}

    def test_dns_forwarding_dropin(self):
        e = self.e
        e.cli("reconcile")
        conf = open(e.p("dnsmasq.d/netbird-gl.conf")).read()
        self.assertIn("server=/netbird.cloud/127.0.0.1#5053", conf)
        self.assertIn("server=/local/127.0.0.1#5053", conf)
        self.assertIn("server=/home.lan/127.0.0.1#5053", conf)
        self.assertIn("rebind-domain-ok=/local/", conf)
        self.assertIn("restart", open(e.p("fix/dnsmasq.log")).read())
        s, _, d = e.cgi()
        self.assertEqual(sorted(d["dns"]["domains"]), ["home.lan", "local", "netbird.cloud"])
        # unchanged -> no extra dnsmasq restart
        os.remove(e.p("fix/dnsmasq.log"))
        e.cli("reconcile")
        self.assertFalse(os.path.exists(e.p("fix/dnsmasq.log")))
        # NetBird DNS off -> drop-in removed, resolver address flag
        e.cli("set", "use_dns=0")
        self.assertFalse(os.path.exists(e.p("dnsmasq.d/netbird-gl.conf")))
        self.assertIn("--disable-dns=true", e.nb()["up_args"])

    def test_dns_listener_missing_reconnects_and_skips_dropin(self):
        e = self.e
        open(e.p("fix/no_listener"), "w").close()
        e.nbstate(calls=[])
        e.cli("reconcile")
        calls = [c[0] for c in e.nb()["calls"] if c]
        self.assertIn("down", calls)
        self.assertIn("up", calls)
        self.assertFalse(os.path.exists(e.p("dnsmasq.d/netbird-gl.conf")))
        self.assertIn("reconnecting", open(e.p("fix/syslog")).read())
        # throttled: no second reconnect within 10 minutes
        e.nbstate(calls=[])
        e.cli("reconcile")
        self.assertNotIn("down", [c[0] for c in e.nb()["calls"] if c])

    def test_dns_primary_group_forwards_extra_domains(self):
        e = self.e
        e.cli("reconcile")
        conf = open(e.p("dnsmasq.d/netbird-gl.conf")).read()
        # primary group (no match domains): "local" is forwarded, no catch-all
        self.assertIn("server=/local/127.0.0.1#5053", conf)
        self.assertIn("server=/*.local/127.0.0.1#5053", conf)
        self.assertNotIn("server=/./", conf)
        s, _, d = e.cgi()
        self.assertIn("local", d["dns"]["domains"])
        self.assertEqual(d["settings"]["dns_extra"], "local")
        e.cli("set", "dns_extra=home.arpa, Local")
        conf = open(e.p("dnsmasq.d/netbird-gl.conf")).read()
        self.assertIn("server=/home.arpa/", conf)
        self.assertEqual(conf.count("server=/local/"), 1)
        self.assertEqual(d["dns"]["domains"].count("local"), 1)
        self.assertNotEqual(e.cli("set", "dns_extra=a/b;reboot", check=False).returncode, 0)
        # no primary group -> extra domains are not forwarded
        e.cli("set", "dns_extra=home.arpa")
        e.nbstate(dnsServers=[{"servers": ["10.10.10.10:53"], "domains": ["corp"], "enabled": True, "error": ""}])
        e.cli("reconcile")
        conf = open(e.p("dnsmasq.d/netbird-gl.conf")).read()
        self.assertIn("server=/corp/", conf)
        self.assertNotIn("home.arpa", conf)

    def test_resolv_guard_protects_gl_upstream_list(self):
        e = self.e
        e.cli("reconcile")
        own = e.p("tmp/resolv.conf.netbird")
        self.assertEqual(os.readlink(e.p("etc/resolv.conf")), own)
        self.assertIn("nameserver 127.0.0.1", open(own).read())
        self.assertIn("192.168.1.1", open(e.p("tmp/resolv.conf.d/resolv.conf.auto")).read())
        # NetBird already overwrote GL's list (through the old symlink) -> repaired from netifd
        open(e.p("tmp/resolv.conf.d/resolv.conf.auto"), "w").write("# Generated by NetBird\nnameserver 127.0.0.1\n")
        e.cli("reconcile")
        auto = open(e.p("tmp/resolv.conf.d/resolv.conf.auto")).read()
        self.assertNotIn("NetBird", auto)
        self.assertIn("nameserver 192.168.1.1", auto)
        self.assertIn("nameserver 192.168.1.2", auto)
        self.assertIn("repaired", open(e.p("fix/syslog")).read())
        e.cli("resolv-unguard")
        self.assertEqual(os.readlink(e.p("etc/resolv.conf")), "/tmp/resolv.conf")
        self.assertFalse(os.path.exists(own))

    def test_kill_switch_only_tunnel_traffic(self):
        e = self.e
        e.cli("set", "kill_switch=1")
        # enabled -> chain in place regardless of the NetBird state
        self.assertTrue(os.path.exists(e.p("fix/iptables.kill")))
        self.assertTrue(os.path.exists(e.p("fix/ip6tables.kill")))
        log = open(e.p("fix/iptables.log")).read()
        self.assertIn("-A NBGL_KILL -o wt0 -j RETURN", log)
        self.assertIn("-A NBGL_KILL -i br-+ -j REJECT", log)
        s, _, d = e.cgi()
        self.assertTrue(d["guard"]["kill"])
        self.assertTrue(d["guard"]["blocking"])      # connected, but no exit node
        self.assertTrue(d["settings"]["kill_switch"])
        e.cli("set", "exit_node=home-exit")
        e.cli("exit", "on")
        s, _, d = e.cgi()
        self.assertFalse(d["guard"]["blocking"])     # all traffic goes through NetBird
        e.cli("down")                                # NetBird off: still blocked
        self.assertTrue(os.path.exists(e.p("fix/iptables.kill")))
        s, _, d = e.cgi()
        self.assertTrue(d["guard"]["blocking"])
        e.cli("set", "kill_switch=0")
        self.assertFalse(os.path.exists(e.p("fix/iptables.kill")))
        self.assertIn("kill switch off", open(e.p("fix/syslog")).read())
        e.cli("guard-off")

    def test_enrolled_is_sticky(self):
        e = self.e
        s, _, d = e.cgi()
        self.assertTrue(d["enrolled"])               # Connected -> remembered
        e.nbstate(daemonStatus="NeedsLogin")         # e.g. management server down
        s, _, d = e.cgi()
        self.assertTrue(d["enrolled"])

    def test_dns_postponed_restart_is_applied_later(self):
        e = self.e
        os.makedirs(e.p("run/nbgl"), exist_ok=True)
        open(e.p("run/nbgl/dns_restart"), "w").write(str(int(time.time())))
        e.cli("reconcile")
        self.assertTrue(os.path.exists(e.p("dnsmasq.d/netbird-gl.conf")))
        self.assertTrue(os.path.exists(e.p("run/nbgl/dns_pending")))
        self.assertFalse(os.path.exists(e.p("fix/dnsmasq.log")))
        open(e.p("run/nbgl/dns_restart"), "w").write("0")
        e.cli("reconcile")
        self.assertFalse(os.path.exists(e.p("run/nbgl/dns_pending")))
        self.assertIn("restart", open(e.p("fix/dnsmasq.log")).read())

    def test_dns_reverts_when_dnsmasq_fails_and_reset(self):
        e = self.e
        open(e.p("fix/dnsmasq_dead"), "w").close()
        e.cli("reconcile")
        self.assertFalse(os.path.exists(e.p("dnsmasq.d/netbird-gl.conf")))
        self.assertIn("reverting", open(e.p("fix/syslog")).read())
        os.remove(e.p("fix/dnsmasq_dead"))
        e.cli("reconcile")
        self.assertFalse(os.path.exists(e.p("dnsmasq.d/netbird-gl.conf")))
        e.cli("dns-reset")
        e.cli("reconcile")
        self.assertIn("server=/local/", open(e.p("dnsmasq.d/netbird-gl.conf")).read())

    def test_dns_check_output(self):
        r = self.e.cli("dns-check")
        self.assertIn("OK: listening on 127.0.0.1:5053", r.stdout)
        self.assertIn("10.10.10.10:53", r.stdout)

    def test_dns_resolver_address_flag(self):
        e = self.e
        e.nbstate(daemonStatus="Idle")
        e.cli("reconcile")
        up = e.nb()["up_args"]
        self.assertEqual(up[up.index("--dns-resolver-address") + 1], "127.0.0.1:5053")

    def test_dns_rejects_bad_domains(self):
        e = self.e
        e.nbstate(dnsServers=[{"servers": ["x"], "domains": ["ok.lan", "bad/dom", "a b", "-x"], "enabled": True, "error": ""}])
        e.cli("reconcile")
        conf = open(e.p("dnsmasq.d/netbird-gl.conf")).read()
        self.assertIn("/ok.lan/", conf)
        self.assertNotIn("bad", conf)
        self.assertNotIn("a b", conf)

    def test_exit_node_by_index_with_any_name(self):
        e = self.e
        e.nbstate(networks=[{"id": "lan", "net": "192.168.1.0/24", "sel": True},
                            {"id": "Exit Node (Homelab)", "net": "0.0.0.0/0", "sel": False},
                            {"id": "Wyjście (mikrus)", "net": "0.0.0.0/0", "sel": False}])
        s, _, d = e.cgi("POST", "settings", "exit_node_idx=2")
        self.assertEqual(s, 200, d)
        self.assertEqual(e.uci("get", "netbird_gl.main.exit_node"), "Wyjście (mikrus)")
        s, _, d = e.cgi("POST", "exit_on")
        self.assertEqual(s, 200, d)
        self.assertTrue(self.sel()["Wyjście (mikrus)"])
        s, _, d = e.cgi("POST", "settings", "exit_node_idx=0")
        self.assertEqual((s, d["error"]), (400, "invalid_exit_node"))
        e.cli("set", "exit_node=Exit Node (Homelab)")
        self.assertTrue(self.sel()["Exit Node (Homelab)"])

    def test_exit_needs_accept_routes(self):
        e = self.e
        e.cli("set", "exit_node=home-exit", "accept_routes=0")
        s, _, d = e.cgi("POST", "exit_on")
        self.assertEqual((s, d["error"]), (409, "accept_routes_off"))

    def test_exit_requires_selection(self):
        r = self.e.cli("exit", "on", check=False)
        self.assertEqual(r.returncode, 2)
        s, _, d = self.e.cgi("POST", "exit_on")
        self.assertEqual((s, d["error"]), (409, "no_exit_node"))

    def test_exit_on_off(self):
        e = self.e
        e.cli("set", "exit_node=home-exit")
        # wylaczony exit -> auto-zastosowany exit node zostaje odznaczony, trasa LAN nietknieta
        self.assertEqual(self.sel(), {"home-exit": False, "other-exit": False, "lan": True})
        e.cli("exit", "on")
        self.assertEqual(self.sel(), {"home-exit": True, "other-exit": False, "lan": True})
        self.assertEqual(e.uci("get", "netbird_gl.state.exit"), "1")
        e.cli("exit", "off")
        self.assertEqual(self.sel(), {"home-exit": False, "other-exit": False, "lan": True})

    def test_switch_vpn(self):
        e = self.e
        e.cli("switch", "vpn", "off")
        self.assertEqual(e.nb()["daemonStatus"], "Idle")
        self.assertEqual(e.uci("get", "netbird_gl.state.vpn"), "0")
        e.cli("switch", "vpn", "on")
        self.assertEqual(e.nb()["daemonStatus"], "Connected")
        e.cli("set", "switch_invert=1")
        e.cli("switch", "vpn", "on")
        self.assertEqual(e.nb()["daemonStatus"], "Idle")

    def test_switch_exit(self):
        e = self.e
        e.cli("set", "exit_node=home-exit")
        e.cli("switch", "exit", "on")
        self.assertTrue(self.sel()["home-exit"])
        e.cli("switch", "exit", "off")
        self.assertFalse(self.sel()["home-exit"])
        self.assertEqual(e.nb()["daemonStatus"], "Connected")

    def test_switch_rejects_bad_args(self):
        self.assertNotEqual(self.e.cli("switch", "bogus", "on", check=False).returncode, 0)
        self.assertNotEqual(self.e.cli("switch", "vpn", "maybe", check=False).returncode, 0)

    def test_switch_exit_hook_script(self):
        e = self.e
        e.cli("set", "exit_node=home-exit")
        hook = os.path.join(ROOT, "etc/gl-switch.d/NetBird-Exit-Node.sh")
        txt = open(hook).read().replace("/usr/sbin/netbird-gl", CLI)
        hp = e.p("hook-exit.sh")
        open(hp, "w").write(txt)
        r = subprocess.run(ASH + [hp, "on"], env=e.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for _ in range(50):
            if self.sel()["home-exit"]:
                break
            time.sleep(0.2)
        self.assertTrue(self.sel()["home-exit"])

    def test_switch_hook_script(self):
        e = self.e
        hook = os.path.join(ROOT, "etc/gl-switch.d/NetBird-VPN.sh")
        txt = open(hook).read().replace("/usr/sbin/netbird-gl", CLI)
        hp = e.p("hook.sh")
        open(hp, "w").write(txt)
        r = subprocess.run(ASH + [hp, "bogus"], env=e.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        r = subprocess.run(ASH + [hp, "off"], env=e.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for _ in range(50):
            if e.nb()["daemonStatus"] == "Idle":
                break
            time.sleep(0.2)
        self.assertEqual(e.nb()["daemonStatus"], "Idle")

    def test_reconcile_needslogin_does_not_up(self):
        e = self.e
        e.nbstate(daemonStatus="NeedsLogin", enrolled=False, calls=[])
        e.cli("reconcile")
        self.assertFalse([c for c in e.nb()["calls"] if c and c[0] == "up" and "--help" not in c])

    def test_reconcile_idle_reconnects_with_hardened_flags(self):
        e = self.e
        e.nbstate(daemonStatus="Idle")
        e.cli("reconcile")
        st = e.nb()
        self.assertEqual(st["daemonStatus"], "Connected")
        self.assertIn("--block-inbound=true", st["up_args"])
        self.assertIn("--disable-client-routes=false", st["up_args"])

    def test_settings_reconnect_on_change(self):
        e = self.e
        e.cli("set", "accept_routes=0", "allow_inbound=1")
        st = e.nb()
        self.assertIn("--disable-client-routes=true", st["up_args"])
        self.assertIn("--block-inbound=false", st["up_args"])
        self.assertEqual(e.uci("get", "firewall.nbgl_zone.input"), "ACCEPT")


class TestAgent(Base):
    def test_agent_starts_reconciles_and_stops(self):
        e = self.e
        e.start_daemon()
        e.nbstate(enrolled=True, daemonStatus="Idle")
        p = subprocess.Popen(ASH + [CLI, "agent"], env=e.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(50):
            if e.nb()["daemonStatus"] == "Connected":
                break
            time.sleep(0.2)
        self.assertEqual(e.nb()["daemonStatus"], "Connected")
        p.terminate()
        p.wait(timeout=10)
        self.assertEqual(p.returncode, 0)
        self.assertIn("agent start", open(e.p("fix/syslog")).read())


class TestUpdate(Base):
    V = "0.81.4"

    def setUp(self):
        super().setUp()
        e = self.e
        e.start_daemon()
        e.nbstate(enrolled=True, daemonStatus="Connected")
        open(e.p("fix/latest"), "w").write(self.V)
        self.make_release(ok=True)

    def make_release(self, ok=True, corrupt=False):
        e = self.e
        nbp = e.p("tmp/netbird-new")
        e.set_binary(nbp, self.V, ok=ok)
        name = "netbird_%s_linux_arm64.tar.gz" % self.V
        tp = e.p("fix/" + name)
        with tarfile.open(tp, "w:gz") as t:
            t.add(nbp, arcname="netbird")
            t.add(nbp, arcname="LICENSE")
        os.remove(nbp)
        h = hashlib.sha256(open(tp, "rb").read()).hexdigest()
        if corrupt:
            h = "0" * 64
        open(e.p("fix/netbird_%s_checksums.txt" % self.V), "w").write(
            "%s  netbird_%s_linux_amd64.tar.gz\n%s  %s\n" % ("f" * 64, self.V, h, name))

    def version(self):
        return subprocess.run([self.e.p("bin/netbird"), "version"], env=self.e.env, capture_output=True, text=True).stdout.strip()

    def test_check_records_latest(self):
        e = self.e
        r = e.cli("update-check")
        self.assertEqual(r.stdout.strip(), self.V)
        self.assertTrue(e.uci("get", "netbird_gl.update.first_seen"))
        s, _, d = e.cgi()
        self.assertTrue(d["update"]["available"])
        self.assertEqual(d["update"]["auto_at"], int(e.uci("get", "netbird_gl.update.first_seen")) + 7 * 86400)

    def test_auto_update_waits_for_delay(self):
        e = self.e
        e.cli("update-cycle")
        self.assertEqual(self.version(), "0.80.0")
        subprocess.run([os.path.join(MOCKS, "uci"), "set", "netbird_gl.update.first_seen=%d" % (time.time() - 8 * 86400)], env=e.env)
        os.remove(e.p("run/nbgl/last_check"))
        e.cli("update-cycle")
        self.assertEqual(self.version(), self.V)
        self.assertEqual(e.uci("get", "netbird_gl.update.installed"), self.V)
        self.assertTrue(os.path.exists(e.sock), "the daemon must run after the update")

    def test_auto_update_immediately(self):
        e = self.e
        e.cli("set", "update_delay_days=0")
        e.cli("update-cycle")
        self.assertEqual(self.version(), self.V)

    def test_auto_update_disabled(self):
        e = self.e
        e.cli("set", "auto_update=0")
        subprocess.run([os.path.join(MOCKS, "uci"), "set", "netbird_gl.update.first_seen=1"], env=e.env)
        e.cli("update-cycle")
        self.assertEqual(self.version(), "0.80.0")

    def test_manual_update(self):
        e = self.e
        e.cli("update", "latest")
        self.assertEqual(self.version(), self.V)

    def test_checksum_mismatch_keeps_old(self):
        e = self.e
        self.make_release(corrupt=True)
        r = e.cli("update", "latest", check=False)
        self.assertEqual(r.returncode, 16)
        self.assertEqual(self.version(), "0.80.0")
        self.assertTrue(os.path.exists(e.sock))
        s, _, d = e.cgi()
        self.assertEqual(d["update"]["error"], "install_failed_16")

    def test_broken_binary_rolls_back(self):
        e = self.e
        self.make_release(ok=False)
        r = e.cli("update", "latest", check=False)
        self.assertEqual(r.returncode, 21)
        self.assertEqual(self.version(), "0.80.0")
        self.assertTrue(os.path.exists(e.sock), "the previous version must be started again")

    def test_offline_check(self):
        e = self.e
        open(e.p("fix/offline"), "w").close()
        r = e.cli("update-check", check=False)
        self.assertNotEqual(r.returncode, 0)

    def test_tls_always_verified(self):
        e = self.e
        e.cli("update", "latest")
        log = open(e.p("fix/curl.log")).read()
        for line in log.splitlines():
            if "github" in line:
                self.assertNotIn(" -k", " " + line)
                self.assertIn("--proto =https", line)

    def test_missing_binary_installed(self):
        e = self.e
        os.remove(e.p("bin/netbird"))
        os.remove(e.sock)
        e.cli("update", "latest")
        self.assertEqual(self.version(), self.V)


class TestInstaller(Base):
    def setUp(self):
        super().setUp()
        e = self.e
        self.dest = e.p("dest")
        for d in ("/etc/nginx/gl-conf.d", "/usr/share/oui", "/www/cgi-bin", "/usr/bin", "/etc/config",
                  "/etc/gl-switch.d", "/usr/lib/netbird-gl/www/cgi-bin"):
            os.makedirs(self.dest + d)
        open(self.dest + "/etc/openwrt_release", "w").write("DISTRIB_ID='OpenWrt'\n")
        # leftovers of devNalyx/netbird-openwrt
        open(self.dest + "/www/cgi-bin/netbird-api", "w").write("#!/bin/sh\n")
        open(self.dest + "/etc/nginx/gl-conf.d/netbird.conf", "w").write("location /netbird/api {}\n")
        open(e.p("ucisrc/netbird"), "w").write("config connection\n\toption api_token 'nbp_secret'\n")
        open(self.dest + "/etc/config/netbird", "w").write("x")
        # leftovers of the first netbird-gl build (uhttpd backend, single 'netbird' switch entry)
        open(self.dest + "/etc/gl-switch.d/netbird.sh", "w").write("#!/bin/sh\n")
        open(self.dest + "/usr/lib/netbird-gl/www/cgi-bin/api", "w").write("#!/bin/sh\n")
        open(self.dest + "/etc/config/netbird_gl", "w").write("config main 'main'\n")
        for kv in ("netbird_gl.main=main", "netbird_gl.main.switch_mode=exit", "netbird_gl.main.web_backend=uhttpd",
                   "netbird_gl.main.exit_node=keepme", "switch-button.@main[0].func=netbird"):
            subprocess.run([os.path.join(MOCKS, "uci"), "set", kv], env=e.env, check=True)
        # GL fcgiwrap socket
        self.sockpath = e.p("run/fcgiwrap.socket")
        subprocess.run(["python3", "-c", "import socket,sys;s=socket.socket(socket.AF_UNIX);s.bind(sys.argv[1])",
                        self.sockpath], check=True)
        self.pkg = os.environ.get("NBGL_PKG")
        if not self.pkg:  # temporary package built from the sources
            self.pkg = e.p("pkg")
            shutil.copytree(SRC, self.pkg, symlinks=True)
            shutil.copy(os.path.join(HERE, "..", "VERSION"), self.pkg + "/VERSION")
            os.makedirs(self.pkg + "/panel", exist_ok=True)
            with open(SRC + "/panel/gl-sdk4-ui-netbirdgl.common.js", "rb") as f, \
                    gzip.open(self.pkg + "/panel/gl-sdk4-ui-netbirdgl.common.js.gz", "wb") as g:
                g.write(f.read())
        self.V = "0.81.4"
        open(e.p("fix/latest"), "w").write(self.V)
        TestUpdate.make_release(self, ok=True)
        self.env = dict(e.env, DESTDIR=self.dest, NBGL_FCGI_SOCK=self.sockpath, NBGL_SKIP_SELFTEST="1")
        for k in ("NB_BIN", "NB_INIT", "NBGL_LIB"):
            self.env.pop(k)
        self.env["NB_INIT"] = os.path.join(MOCKS, "init-netbird")

    def run_install(self, *args):
        return subprocess.run(ASH + [os.path.join(self.pkg, "install.sh")] + list(args), env=self.env,
                              capture_output=True, text=True, timeout=180)

    def test_requires_fcgiwrap(self):
        self.env["NBGL_FCGI_SOCK"] = self.e.p("run/missing.socket")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("fcgiwrap", r.stdout)

    def test_install_upgrade_and_uninstall(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self.dest
        self.assertIn("http://10.10.0.1/netbird-gl/", r.stdout)
        self.assertTrue(os.access(d + "/usr/sbin/netbird-gl", os.X_OK))
        self.assertTrue(os.access(d + "/usr/bin/netbird", os.X_OK))
        for f in ("NetBird-VPN.sh", "NetBird-Exit-Node.sh"):
            self.assertTrue(os.access(d + "/etc/gl-switch.d/" + f, os.X_OK), f)
        conf = open(d + "/etc/nginx/gl-conf.d/netbird-gl.conf").read()
        self.assertIn("location ^~ /netbird-gl/", conf)
        self.assertIn("fastcgi_param SCRIPT_FILENAME /usr/lib/netbird-gl/api.cgi;", conf)
        # previous-build leftovers migrated
        self.assertFalse(os.path.exists(d + "/etc/gl-switch.d/netbird.sh"))
        self.assertFalse(os.path.exists(d + "/usr/lib/netbird-gl/www"))
        self.assertEqual(self.e.uci("get", "switch-button.@main[0].func"), "NetBird-Exit-Node")
        self.assertEqual(self.e.uci("get", "netbird_gl.main.web_backend"), "")
        self.assertEqual(self.e.uci("get", "netbird_gl.main.switch_mode"), "")
        self.assertEqual(self.e.uci("get", "netbird_gl.main.exit_node"), "keepme")
        # devNalyx leftovers removed
        self.assertFalse(os.path.exists(d + "/www/cgi-bin/netbird-api"))
        self.assertFalse(os.path.exists(d + "/etc/nginx/gl-conf.d/netbird.conf"))
        self.assertIn("REVOKE", r.stdout)
        self.assertEqual(self.e.uci("get", "firewall.nbgl_zone.input"), "REJECT")
        self.assertEqual(self.e.uci("get", "firewall.nbgl_lan.dest"), "netbird")
        mode = stat.S_IMODE(os.stat(d + "/etc/config/netbird_gl").st_mode)
        self.assertEqual(mode, 0o600)
        gz = d + "/www/views/gl-sdk4-ui-netbirdgl.common.js.gz"
        self.assertIn(b"/netbird-gl/", gzip.open(gz).read())
        # re-running the installer (upgrade) keeps the configuration
        open(d + "/etc/config/netbird_gl", "a").write("# marker\n")
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("# marker", open(d + "/etc/config/netbird_gl").read())
        self.assertIn("NetBird already installed", r.stdout)
        # uninstall
        r = subprocess.run(ASH + [d + "/usr/lib/netbird-gl/uninstall.sh", "--purge"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for f in ("/usr/sbin/netbird-gl", "/etc/nginx/gl-conf.d/netbird-gl.conf", "/usr/bin/netbird",
                  "/www/netbird-gl/index.html", "/usr/lib/netbird-gl", "/etc/gl-switch.d/NetBird-VPN.sh"):
            self.assertFalse(os.path.exists(d + f), f)
        self.assertEqual(self.e.uci("get", "firewall.nbgl_zone"), "")


class TestWebUi(unittest.TestCase):
    def test_no_inline_script_and_no_innerhtml(self):
        www = os.path.join(ROOT, "www/netbird-gl")
        html = open(os.path.join(www, "index.html")).read().replace("id=", 'id="').replace('id=""', 'id="')
        import re as _re
        html = _re.sub(r'id="([^" >]+)(?=[ >])', r'id="\1"', html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("onclick", html)
        js = open(os.path.join(www, "app.js")).read()
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("eval(", js)
        r = subprocess.run(["node", "--check", os.path.join(www, "app.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        if os.environ.get("NBGL_PKG"):
            self.assertIn("X-NBGL", js)
            return
        self.assertIn("'X-NBGL': '1'", js)
        # wszystkie id uzywane w JS istnieja w HTML
        import re
        ids = set(re.findall(r"\$\('([a-z0-9_-]+)'\)", js))
        ids |= {"s-" + k for k in ("switch_invert", "accept_routes", "use_dns", "allow_inbound",
                                    "guest_access", "auto_check", "auto_update", "update_delay_days")}
        for i in ids:
            self.assertIn('id="%s"' % i, html, i)


if __name__ == "__main__":
    unittest.main(verbosity=2)
