import json
import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_upstream import FakeUpstream  # noqa: E402

from copilot_shim.rewrite import Policy  # noqa: E402
from copilot_shim.server import serve  # noqa: E402

FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "prefill_requests.json").read_text())


@pytest.fixture
def upstream():
    with FakeUpstream() as up:
        yield up


@pytest.fixture
def make_shim(upstream):
    servers = []

    def _make(**policy_kwargs):
        server = serve("127.0.0.1", 0, upstream.url, Policy(**policy_kwargs))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _make
    for s in servers:
        s.shutdown()
        s.server_close()
