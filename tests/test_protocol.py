"""Protocol tests against a fake bulb listening on localhost."""
import json
import socket
import threading
import time

import pytest

from wizkontrol import protocol


class FakeBulb(threading.Thread):
    def __init__(self, drop_first=0):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.state = {"mac": "aabbccddeeff", "state": True, "temp": 2700, "dimming": 50}
        self.received = []
        self.drop_first = drop_first

    def run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                return
            msg = json.loads(data)
            self.received.append(msg)
            if self.drop_first:
                self.drop_first -= 1
                continue
            if msg["method"] == "setPilot":
                self.state.update(msg["params"])
                resp = {"method": "setPilot", "env": "pro", "result": {"success": True}}
            else:
                resp = {"method": msg["method"], "env": "pro", "result": dict(self.state)}
            self.sock.sendto(json.dumps(resp).encode(), addr)


@pytest.fixture
def make_bulb(monkeypatch):
    bulbs = []

    def factory(**kw):
        b = FakeBulb(**kw)
        monkeypatch.setattr(protocol, "PORT", b.port)
        b.start()
        bulbs.append(b)
        return b

    yield factory
    for b in bulbs:
        b.sock.close()


def test_get_pilot(make_bulb):
    make_bulb()
    resp = protocol.wiz_request("127.0.0.1", "getPilot")
    assert resp["result"]["temp"] == 2700


def test_set_pilot_changes_state(make_bulb):
    bulb = make_bulb()
    resp = protocol.wiz_request("127.0.0.1", "setPilot", {"sceneId": 5, "dimming": 30})
    assert resp["result"]["success"]
    assert bulb.state["sceneId"] == 5 and bulb.state["dimming"] == 30


def test_retries_when_packets_are_dropped(make_bulb):
    bulb = make_bulb(drop_first=2)
    resp = protocol.wiz_request("127.0.0.1", "getPilot", timeout=0.2, retries=3)
    assert resp is not None and len(bulb.received) == 3


def test_returns_none_when_bulb_is_silent(make_bulb):
    make_bulb(drop_first=99)
    assert protocol.wiz_request("127.0.0.1", "getPilot", timeout=0.1, retries=2) is None


def test_sender_delivers_latest_command(make_bulb):
    bulb = make_bulb()
    results = []
    sender = protocol.Sender(lambda ip, params, resp: results.append(resp))
    sender.start()
    for d in range(10, 60, 10):
        sender.submit("127.0.0.1", {"dimming": d})
    deadline = time.time() + 3
    while time.time() < deadline and (bulb.state["dimming"] != 50 or sender.pending or not results):
        time.sleep(0.02)
    assert bulb.state["dimming"] == 50
    assert results and all(r is not None for r in results)
