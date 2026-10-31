"""Command-line client for bridge.py; uses only the Python standard library."""
import argparse
import json
import socket
import uuid


def request(operation, args=None, confirm=False, host="127.0.0.1", port=47831, timeout=35):
    if host != "127.0.0.1":
        raise ValueError("client permits loopback only")
    payload = {"version": 1, "id": str(uuid.uuid4()), "operation": operation,
               "args": args or {}, "confirm": bool(confirm)}
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "bridge request failed"))
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["inspect.objects", "inspect.armature", "inspect.weights",
                                                 "inspect.actions", "inspect.pose", "mutate.duplicate_rig",
                                                 "mutate.restore"])
    parser.add_argument("--args", default="{}", help="operation arguments as JSON")
    parser.add_argument("--confirm", action="store_true", help="required for mutate.* operations")
    parser.add_argument("--port", type=int, default=47831)
    opts = parser.parse_args()
    print(json.dumps(request(opts.operation, json.loads(opts.args), opts.confirm, port=opts.port), indent=2))


if __name__ == "__main__":
    main()
