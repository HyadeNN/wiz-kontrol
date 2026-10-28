"""WiZ yerel UDP protokolü: komut gönderme, ağda ampul bulma ve arka plan gönderici."""
import json
import select
import socket
import threading
import time

PORT = 38899


def wiz_request(ip, method, params=None, timeout=0.5, retries=3):
    """Ampule bir komut gönderir, yanıtı (dict) ya da None döner."""
    msg = json.dumps({"method": method, "params": params or {}}).encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        for _ in range(retries):
            try:
                s.sendto(msg, (ip, PORT))
                while True:
                    data, addr = s.recvfrom(4096)
                    if addr[0] != ip:
                        continue
                    resp = json.loads(data)
                    if resp.get("method") == method:
                        return resp
            except socket.timeout:
                continue
            except (OSError, ValueError):
                break
    return None


def local_ipv4s():
    ips = set()
    try:
        ips.update(ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127."))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return ips


def discover(duration=2.5):
    """Ağdaki WiZ ampulleri bulur: {mac: {"ip":..., "pilot":...}}."""
    msg = json.dumps({"method": "getPilot", "params": {}}).encode()
    socks = []
    for lip in local_ipv4s():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((lip, 0))
            s.setblocking(False)
            socks.append((s, lip))
        except OSError:
            pass
    found = {}
    if not socks:
        return found
    end, next_send = time.time() + duration, 0
    while time.time() < end:
        if time.time() >= next_send:
            for s, lip in socks:
                for dst in ("255.255.255.255", lip.rsplit(".", 1)[0] + ".255"):
                    try:
                        s.sendto(msg, (dst, PORT))
                    except OSError:
                        pass
            next_send = time.time() + 0.6
        readable, _, _ = select.select([s for s, _ in socks], [], [], 0.1)
        for s in readable:
            try:
                data, addr = s.recvfrom(4096)
                res = json.loads(data).get("result", {})
            except (OSError, ValueError):
                continue
            if "mac" in res:
                found[res["mac"]] = {"ip": addr[0], "pilot": res}
    for s, _ in socks:
        s.close()
    return found


class Sender(threading.Thread):
    """Komutları arka planda gönderir; aynı ampule biriken komutlardan yalnızca sonuncusu gider."""

    def __init__(self, on_result):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.pending = {}
        self.event = threading.Event()
        self.on_result = on_result

    def submit(self, ip, params):
        with self.lock:
            self.pending[ip] = params
        self.event.set()

    def run(self):
        while True:
            self.event.wait()
            self.event.clear()
            while True:
                with self.lock:
                    if not self.pending:
                        break
                    ip, params = self.pending.popitem()
                self.on_result(ip, params, wiz_request(ip, "setPilot", params, timeout=0.35))
