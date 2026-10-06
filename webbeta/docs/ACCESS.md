# ACCESS.md — Book Flow Chart Web Beta

SHADOW / RESEARCH ONLY. This service is **localhost/LAN only** by design. It is not, and must
never become, a publicly reachable service. There is no TLS, no rate limiting, no real accounts
system (invite tokens are a shared-secret list, explicitly not a substitute for one) -- none of
this is hardened for the open internet.

## Localhost (default, always safe)

```
cd OFI_Production/webbeta
./run.sh            # or ./run.sh --live
```

Binds `127.0.0.1` only. Open `http://127.0.0.1:8800/?token=<token>` in a browser on the same
machine. This is the only mode that requires no thought about exposure.

## LAN (same network, explicit opt-in required)

The server refuses to bind any non-loopback host unless you pass `--lan` to `server/app.py`
directly (there is no `run.sh` shortcut for this -- it's a deliberate extra step):

```
python3 -m server.app --host 0.0.0.0 --port 8800 --lan
```

This makes the service reachable from **any other device on the same LAN/Wi-Fi**, not just your
own devices. Only do this on a network you trust (e.g. your home network, not a shared/public
one), and only while you're actively using it -- stop the server when done. This is still not
authentication-hardened; anyone on the LAN who obtains the invite token can connect.

## Remote self-access (you, from elsewhere) -- use a tunnel, never a port-forward

If you want to reach your own instance from outside your LAN (e.g. from a laptop while traveling),
the safe pattern is: **the server keeps binding `127.0.0.1` (the default, no `--lan` needed) and
you reach it through a private tunnel**, not by exposing the port to the internet.

Recommended, roughly in order of setup effort:

- **Tailscale** (easiest): install on this machine and on the client device, join the same
  tailnet, then browse to `http://<this-machine's-tailscale-name>:8800/?token=...`. No port
  forwarding, no public exposure -- Tailscale's mesh VPN handles the private routing.
- **WireGuard**: if you already run your own WireGuard setup, add this machine to it and reach
  `http://<its-wireguard-IP>:8800/...` the same way. More setup than Tailscale, but no third-party
  coordination service.
- **SSH local port forward** (`ssh -L 8800:127.0.0.1:8800 user@this-machine`, then browse to
  `http://127.0.0.1:8800/...` on the CLIENT side): works if you already have SSH access to this
  machine from wherever you are. No new services to install, but requires SSH reachability to this
  machine in the first place (itself usually arranged via one of the above, or an existing setup).

## Do not port-forward this box

**Do not configure your router to forward a port to this machine for this service.** A port
forward makes the FastAPI server directly reachable from the entire public internet, with none of
the protections a production-grade public service would need (no TLS termination, no rate
limiting/DoS protection, no accounts system beyond a shared invite-token string, no security
review against internet-facing attack traffic). This is true even if you believe the invite token
is hard to guess -- port-forwarding widens the blast radius from "your LAN" or "your private
tunnel" to "anyone scanning the internet," which is a categorically different exposure this
project was never built or reviewed for.

## Live mode specifically

Live mode reads real production cache paths, read-only -- the exact same access pattern the
desktop chart already uses (see `W2LIVE_REPORT.md` Part E gate (g) for the read-only audit).
Nothing about the access/tunneling guidance above changes for live vs. replay mode; the exposure
surface (this HTTP+WebSocket server) is identical either way.
