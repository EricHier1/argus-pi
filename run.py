"""Entry point. Run:  .venv/bin/python run.py

Exposure is set by ARGUS_BIND (see app/config.py):
  lan        home mode — reachable on your wifi (self-signed cert, browser warns once)
  local      this computer only
  tailscale  bound to the Tailscale IP only — trusted cert, works over cell, no wifi exposure

Examples:
  ARGUS_BIND=tailscale .venv/bin/python run.py
  ARGUS_BIND=local .venv/bin/python run.py
"""
import socket

import uvicorn

from app import config, tls


def lan_ip():
    """Best-effort local network IP of this machine (no traffic is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def resolve():
    """Return (host, display_url, ssl_args, note) for the chosen bind mode."""
    bind = config.BIND
    port = config.PORT

    if bind == "tailscale":
        name, ip = tls.tailscale_info()
        if name and ip:
            cert, key = tls.ensure_tailscale_cert(name)
            if cert:
                host = config.HOST or ip   # bind to the Tailscale IP only
                return (host, f"https://{name}:{port}",
                        {"ssl_certfile": cert, "ssl_keyfile": key},
                        "trusted Tailscale cert · works over cell · NOT exposed on wifi")
            print("  Tailscale cert unavailable (enable HTTPS Certificates in the admin "
                  "console); falling back to lan.")
        else:
            print("  Tailscale not connected; falling back to lan.")
        bind = "lan"

    # local / lan (self-signed)
    host = config.HOST or ("127.0.0.1" if bind == "local" else "0.0.0.0")
    if config.HTTPS:
        cert, key = tls.ensure_cert(lan_ip())
        if cert:
            ssl_args = {"ssl_certfile": cert, "ssl_keyfile": key}
            scheme, note = "https", "self-signed cert — accept the browser's one-time warning"
        else:
            ssl_args, scheme, note = {}, "http", "cert generation failed — using http"
    else:
        ssl_args, scheme, note = {}, "http", "http (ARGUS_HTTPS=0)"

    shown_ip = "localhost" if bind == "local" else (lan_ip() or "localhost")
    return host, f"{scheme}://{shown_ip}:{port}", ssl_args, note


if __name__ == "__main__":
    host, url, ssl_args, note = resolve()
    print("Argus")
    print(f"  Mode:    {config.BIND}")
    print(f"  Open:    {url}")
    print(f"  Note:    {note}")
    print(f"  Cameras: {', '.join(config.SOURCES)}")
    uvicorn.run("app.server:app", host=host, port=config.PORT, reload=False, **ssl_args)
