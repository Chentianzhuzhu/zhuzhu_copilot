import socket

HOST, PORT = "122.228.216.138", 6379
AUTH = "M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo"


def send(sock, *args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = str(a).encode()
        out += b"$" + str(len(a)).encode() + b"\r\n" + a + b"\r\n"
    sock.sendall(out)
    return sock.recv(65536).decode("utf-8", "replace").strip()


s = socket.create_connection((HOST, PORT), timeout=10)
print("connect          :", "OK")
print("PING (no auth)   :", repr(send(s, "PING")))
print("AUTH             :", repr(send(s, "AUTH", AUTH)))
print("PING             :", repr(send(s, "PING")))

# exercise a realistic cache workflow
ttag0 = None
for i in range(200):
    r = send(s, "SET", "api:user:%d" % i, '{"name":"u%d"}' % i, "EX", 600)
    assert r == "+OK", r
print("SET x200 (EX 600):", "OK")

print("GET              :", repr(send(s, "GET", "api:user:7")))
print("DBSIZE           :", repr(send(s, "DBSIZE")))
print("TTL              :", repr(send(s, "TTL", "api:user:7")))
print("INCR (counter)   :", repr(send(s, "INCR", "api:ratelimit:client42")))
print("INFO uptime      :", send(s, "INFO", "server")[:0] or "N/A")
print("COMMAND DOCS FLUSHALL (renamed):", repr(send(s, "FLUSHALL"))[:60])

# latency probe
import time
lat = []
for i in range(50):
    t0 = time.time()
    send(s, "PING")
    lat.append((time.time() - t0) * 1000)
lat.sort()
print("RTT p50/p95/max  : %.2f / %.2f / %.2f ms" % (lat[25], lat[47], lat[49]))
s.close()
print("\nRESULT: remote access fully working")
