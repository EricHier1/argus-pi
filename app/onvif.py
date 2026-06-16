"""Minimal dependency-free ONVIF client for finding IP cameras and their RTSP
URLs: WS-Discovery over UDP multicast (same subnet), and a direct "ask the camera
by IP" path (works across subnets) that handles the common case of a camera whose
clock is wrong — ONVIF rejects a login token whose timestamp is too skewed, so we
sign with the camera's own clock."""
import base64
import datetime
import hashlib
import re
import secrets
import socket
import urllib.parse
import urllib.request

_DISCOVER = ("239.255.255.250", 3702)


def discover(timeout=3):
    """WS-Discovery probe -> [{ip, xaddr}] of ONVIF devices on the local segment.
    Multicast usually stays within the subnet, so cameras behind a router won't
    appear here — use probe_ip() for those."""
    msg = ('<?xml version="1.0"?><e:Envelope '
           'xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
           'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
           'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
           'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header>'
           f'<w:MessageID>uuid:{secrets.token_hex(8)}</w:MessageID>'
           '<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
           '<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
           '</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>'
           '</d:Probe></e:Body></e:Envelope>')
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(timeout)
    found = {}
    try:
        s.sendto(msg.encode(), _DISCOVER)
        import time
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                break
            text = data.decode("utf-8", "ignore")
            x = re.search(r"XAddrs>\s*(http[^<\s]+)", text)
            found.setdefault(addr[0], {"ip": addr[0], "xaddr": x.group(1) if x else ""})
    except Exception:
        pass
    finally:
        s.close()
    return list(found.values())


def _post(url, header, body, timeout=6):
    env = ('<?xml version="1.0"?><s:Envelope '
           'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
           'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
           'xmlns:tt="http://www.onvif.org/ver10/schema" '
           'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
           f'<s:Header>{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>')
    req = urllib.request.Request(url, data=env.encode(),
                                 headers={"Content-Type": "application/soap+xml"})
    try:
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        try:
            return e.read().decode("utf-8", "ignore")
        except Exception:
            return ""
    except Exception:
        return ""


def _num(xml, tag):
    m = re.search(r"<[A-Za-z0-9]*:?" + tag + r">(\d+)</", xml)
    return int(m.group(1)) if m else None


def camera_created(ip, port=80):
    """ONVIF WS-Security timestamps must be close to the camera's own clock, which
    is often wrong — so ask the camera for its time and sign with that."""
    r = _post(f"http://{ip}:{port}/onvif/device_service", "", "<tds:GetSystemDateAndTime/>")
    blk = re.search(r"UTCDateTime>(.*?)</[A-Za-z0-9]*:?UTCDateTime", r, re.S)
    src = blk.group(1) if blk else r
    p = [_num(src, t) for t in ("Year", "Month", "Day", "Hour", "Minute", "Second")]
    if all(v is not None for v in p):
        return "%04d-%02d-%02dT%02d:%02d:%02dZ" % tuple(p)
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wsse(user, password, created):
    """A single-use WS-Security UsernameToken (PasswordDigest) SOAP header."""
    n = secrets.token_bytes(16)
    d = base64.b64encode(hashlib.sha1(n + created.encode() + password.encode()).digest()).decode()
    return ('<Security s:mustUnderstand="1" '
            'xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            f"<UsernameToken><Username>{user}</Username>"
            '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{d}</Password><Nonce>{base64.b64encode(n).decode()}</Nonce>"
            '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            f"{created}</Created></UsernameToken></Security>")


def probe_ip(ip, user="", password="", port=80):
    """Ask an ONVIF camera (by IP) for its RTSP stream URLs. Returns a list of
    URLs with credentials embedded (percent-encoded), best/highest first, or []."""
    # sign with the camera's own clock (many cameras have a wrong date)
    created = camera_created(ip, port)

    def tok():
        return wsse(user, password, created)

    media = f"http://{ip}:{port}/onvif/media_service"
    prof = _post(media, tok(), "<trt:GetProfiles/>")
    toks = list(dict.fromkeys(re.findall(r'[tT]oken="([^"]+)"', prof)))
    urls = []
    for t in toks:
        su = _post(media, tok(),
                   "<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
                   "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
                   f"<trt:ProfileToken>{t}</trt:ProfileToken></trt:GetStreamUri>")
        m = re.search(r"rtsp://[^<\s]+", su)
        if not m:
            continue
        url = m.group(0)
        if user and "@" not in url.split("://", 1)[1].split("/", 1)[0]:
            creds = urllib.parse.quote(user, safe="") + ":" + urllib.parse.quote(password, safe="")
            url = url.replace("rtsp://", f"rtsp://{creds}@", 1)
        if url not in urls:
            urls.append(url)
    return urls
