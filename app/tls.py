"""Self-signed TLS certificate for local HTTPS.

Generates a cert covering localhost, 127.0.0.1, and the machine's current LAN IP
(so phones on the same wi-fi can connect). It's self-signed, so browsers show a
one-time "not private" warning you accept manually — there's no public CA for a
device on your home network. Cert + key live in data/certs/ (gitignored)."""
import json
import shutil
import subprocess

from . import config

CERT_DIR = config.DATA_DIR / "certs"
CERT = CERT_DIR / "cert.pem"
KEY = CERT_DIR / "key.pem"
MARKER = CERT_DIR / ".ip"   # records the LAN IP the cert was built for
TS_CERT = CERT_DIR / "ts.crt"
TS_KEY = CERT_DIR / "ts.key"


def _tailscale_bin():
    return (shutil.which("tailscale")
            or "/Applications/Tailscale.app/Contents/MacOS/Tailscale")


def tailscale_info():
    """Return (dns_name, ipv4) for this machine on the tailnet, or (None, None)
    if Tailscale isn't running."""
    try:
        out = subprocess.run([_tailscale_bin(), "status", "--json"],
                             capture_output=True, text=True, timeout=8)
        d = json.loads(out.stdout or "{}")
        self_ = d.get("Self", {})
        if d.get("BackendState") != "Running" or not self_.get("Online", True):
            pass  # still try; status text can lag
        name = (self_.get("DNSName") or "").rstrip(".")
        ip4 = next((i for i in (self_.get("TailscaleIPs") or []) if ":" not in i), None)
        if name and ip4:
            return name, ip4
    except Exception:
        pass
    return None, None


def ensure_tailscale_cert(name):
    """Mint/renew a trusted Tailscale TLS cert for `name`. Requires HTTPS
    Certificates enabled on the tailnet. `tailscale cert` is idempotent — it
    returns the cached cert quickly when still valid and renews when near expiry —
    so we always call it, and fall back to an existing file on transient failure.
    Returns (certfile, keyfile) or (None, None)."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([_tailscale_bin(), "cert",
                        "--cert-file", str(TS_CERT), "--key-file", str(TS_KEY), name],
                       check=True, capture_output=True, timeout=60)
        return str(TS_CERT), str(TS_KEY)
    except Exception:
        if TS_CERT.exists() and TS_KEY.exists():
            return str(TS_CERT), str(TS_KEY)   # reuse on transient failure (e.g. offline)
        return None, None


def ensure_cert(ip=None):
    """Return (certfile, keyfile) as strings, regenerating if missing or if the
    LAN IP changed. Returns (None, None) if generation fails."""
    cur = ip or ""
    if CERT.exists() and KEY.exists() and MARKER.exists() and MARKER.read_text().strip() == cur:
        return str(CERT), str(KEY)

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    sans = ["DNS:localhost", "IP:127.0.0.1"]
    if ip:
        sans.append(f"IP:{ip}")
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(KEY), "-out", str(CERT), "-days", "825",
             "-subj", "/CN=Argus", "-addext", f"subjectAltName={','.join(sans)}"],
            check=True, capture_output=True, timeout=30,
        )
        MARKER.write_text(cur)
        return str(CERT), str(KEY)
    except Exception:
        return None, None
