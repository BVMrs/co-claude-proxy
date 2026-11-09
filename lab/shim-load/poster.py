"""Background big-body sender for E3, in its own process so it shares no GIL with the
stream readers. Sends <bodyfile> as a non-streaming request from <threads> threads
for <secs> seconds, pausing <pause> s between requests per thread; prints the count.
Usage: python3 poster.py <port> <bodyfile> <threads> <pause> <secs>"""
import socket
import sys
import threading
import time

port, path, n, pause, secs = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
raw = open(path, "rb").read()
head = (f"POST /v1/{'messages'} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n").encode()
end = time.monotonic() + secs
count = [0]


def loop():
    while time.monotonic() < end:
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(head + raw)
        while s.recv(65536):
            pass
        s.close()
        count[0] += 1
        if pause:
            time.sleep(pause)


ts = [threading.Thread(target=loop) for _ in range(n)]
for t in ts:
    t.start()
for t in ts:
    t.join()
print(count[0])
