"""Wire protocol between a rover body (Webots adapter) and the brain.

The implementation lives in simlab.protocol (shared by every vehicle kind); this module keeps the old import path.
"""
from simlab.protocol import (HOST, MAX_HEADER, MAX_PAYLOAD, PORT, PROTOCOL_VERSION, Reader, cmd_header,  # noqa: F401
                             hello_caps, pack, parse_cmd)
