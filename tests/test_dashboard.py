"""factory/bin/dashboard: serves factory/ on 127.0.0.1 only, reports one already
running, and refuses a port another program holds."""
import http.server, os, socket, subprocess, sys, threading, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import BASE_ENV, REPO, Checks

check = Checks()
DASH = str(REPO / "factory" / "bin" / "dashboard")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

def get(url):
    try:
        with OPENER.open(url, timeout=3) as r:
            return r.status, r.read()
    except OSError as e:
        return None, str(e).encode()

port = free_port()
server = subprocess.Popen([DASH, "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=BASE_ENV)
try:
    line = server.stdout.readline().strip()
    check("prints its address", line == f"dashboard: http://127.0.0.1:{port}/dashboard.html", line)
    status, body = get(f"http://127.0.0.1:{port}/dashboard.html")
    check("serves the dashboard", status == 200 and b"<title>Software Factory</title>" in body, status)
    status, _ = get(f"http://127.0.0.1:{port}/config.json")
    check("serves the factory folder", status == 200, status)
    r = subprocess.run([DASH, "--port", str(port)], capture_output=True, text=True, env=BASE_ENV, timeout=20)
    check("a second start reports the running one", r.returncode == 0 and "already running" in r.stdout, r.stdout + r.stderr)
    try:  # an address other than loopback, if the machine has one
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); probe.connect(("10.255.255.255", 1))
        ip = probe.getsockname()[0]; probe.close()
    except OSError:
        ip = None
    if ip and not ip.startswith("127."):
        status, _ = get(f"http://{ip}:{port}/dashboard.html")
        check(f"refuses connections on {ip} (localhost only)", status is None, status)
    else:
        print("SKIP localhost-only check: no non-loopback address on this machine")
finally:
    server.terminate(); server.wait()

other = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), http.server.SimpleHTTPRequestHandler)
threading.Thread(target=other.serve_forever, daemon=True).start()
r = subprocess.run([DASH, "--port", str(other.server_address[1])], capture_output=True, text=True, env=BASE_ENV, timeout=20)
check("a port held by another program: clear error", r.returncode == 1 and "taken by something else" in r.stderr, r.stderr)
other.shutdown()
check.finish()
