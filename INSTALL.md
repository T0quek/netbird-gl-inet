# Installing netbird-gl

Tested on the GL.iNet Beryl 7 (GL-MT3600BE), firmware 4.9.1.

## Before you start

- GL firmware **4.9.1 or newer**, which fixes the GoodCloud vulnerability.
- Internet access on the router, because the installer downloads NetBird from GitHub.
- A NetBird **setup key**: *Setup Keys → Create Setup Key*. Assigning it a group
  such as `routers` makes access policies easier.
- `netbird-gl-1.0.0.tar.gz` from the GitHub release on your computer. Its
  checksum is in `SHA256SUMS`.

Replace `192.168.8.1` below with your router's LAN address.

## 1. Copy the package to the router

```sh
scp -O netbird-gl-1.0.0.tar.gz root@192.168.8.1:/tmp/
```

The `-O` flag is needed because the router's SSH server (dropbear) has no SFTP.
The SSH password is the admin panel password.

## 2. Install

```sh
ssh root@192.168.8.1
cd /tmp && tar xzf netbird-gl-1.0.0.tar.gz && sh netbird-gl/install.sh
```

The installer does the following:

1. Downloads the latest NetBird release for the router's CPU and verifies its SHA256.
2. Creates the `netbird` firewall zone. Inbound connections to the router from
   NetBird are rejected by default.
3. Starts the `netbird` and `netbird-gl` services.
4. Adds the **APPLICATIONS → NetBird** tab and the two toggle button entries.
5. Removes insecure leftovers of devNalyx/netbird-openwrt, if it finds any.
6. Finishes with a self-test of the page, the API and the GL session check. If the
   session check is not supported, it switches to password mode and asks you to
   run `netbird-gl set-password`.

**Offline install:** download `netbird_<version>_linux_arm64.tar.gz` and
`netbird_<version>_checksums.txt` from the NetBird GitHub releases, copy both to
`/tmp`, then run:

```sh
sh netbird-gl/install.sh --netbird-tar /tmp/netbird_0.83.2_linux_arm64.tar.gz --checksums /tmp/netbird_0.83.2_checksums.txt
```

## 3. Enroll the router

In the GL panel open **APPLICATIONS → NetBird**. A setup wizard starts:

1. **Server:** choose NetBird Cloud, or Self-hosted and enter your server address
   (e.g. `https://netbird.example.com:443`).
2. **Setup key:** paste the key.
3. **Options:** peer routes, NetBird DNS, guest network, toggle button function.
4. **Connect:** the router enrolls and the dashboard opens.

To move the router to another account or server later, use **Advanced → Re-enroll…**.

## 4. Exit node and toggle button

1. **Share an exit node in the NetBird dashboard.** In *Networks* (or *Network
   Routes*), make a peer, for example a server at home, an exit node. Its
   distribution groups must include the router's group.
2. **Select it on the router.** In the **Exit node** card pick it from the list;
   the switch in the card header turns it on and off.
3. **Assign the toggle button** in the **Toggle button** card, or in
   **SYSTEM → Toggle Button Settings**:
   - **NetBird-VPN** – the switch turns the NetBird connection on and off,
   - **NetBird-Exit-Node** – the switch turns the selected exit node on and off.
4. **Check it works.** From a laptop connected to the router, open
   `https://ipinfo.io`. It should show the exit node's public IP.

## Upgrading netbird-gl

You do not need to uninstall first. Unpack the newer package and run
`sh netbird-gl/install.sh` again. Configuration, NetBird enrollment and the
downloaded NetBird binary are kept, and files that are no longer used are
removed.

## NetBird updates

- **Checking:** the router checks for a new NetBird release once a day and shows
  it in the panel with an **Update now** button.
- **Automatic install:** if you don't update manually, the new release is
  installed automatically after **7 days**. The delay is configurable
  (immediately, or 1–30 days) and the feature can be turned off. The delay
  counts from the day the release was first seen, so days when the router was
  off count too.
- **Safety:** every update is verified with SHA256. If the new version does not
  start, the previous one is restored automatically.
- **GL firmware upgrades:** with "Keep Settings", netbird-gl, the enrollment and
  the configuration are kept. The NetBird binary is not, but the agent downloads
  it again automatically.

## DNS

- **NetBird domains everywhere:** with *Use NetBird DNS* on, the NetBird peer
  domain and the match domains of your NetBird nameservers resolve for every
  device behind the router.
- **`.local` names:** if your nameserver has no match domains, the domains in
  *Extra NetBird DNS domains* are forwarded too. The default is `local`, which
  makes e.g. `truenas.local` resolve.
- **GL's DNS settings stay in charge** of everything else. "Automatic", manual
  servers and AdGuard Home all keep working.

## Kill switch

With **Advanced → Kill switch** on, devices behind the router can only send
traffic into the NetBird tunnel. They have internet only while NetBird is
connected **and** the exit node is on. In every other case internet is
blocked, including when you turn NetBird off yourself. The router itself keeps
its connection so NetBird can reconnect.

## Troubleshooting

- **"Your GL admin panel session has expired" keeps appearing.** Switch to a
  dedicated password:

  ```sh
  uci set netbird_gl.main.auth=password && uci commit netbird_gl
  netbird-gl set-password
  ```

- **A name does not resolve:** `netbird-gl dns-check truenas.local` shows what
  is forwarded where and tries the lookup. `netbird-gl dns-reset` removes
  netbird-gl's DNS changes.
- **No internet for LAN devices:** check whether the kill switch is on. The
  Connection card shows a red line while it blocks traffic.
- **Status and logs over SSH:** `netbird-gl status`, `netbird status -d`,
  `logread -e netbird`.

## Uninstall

```sh
sh /usr/lib/netbird-gl/uninstall.sh            # keeps the NetBird binary and enrollment
sh /usr/lib/netbird-gl/uninstall.sh --purge    # removes everything
```
