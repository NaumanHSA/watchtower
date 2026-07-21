import os
import socket
from typing import Dict, List

def _primary_ip() -> str:
    # get the IP for the default route (works in Docker)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def _all_ips() -> List[str]:
    # no external deps: best-effort via getaddrinfo
    ips = set()
    try:
        host = socket.gethostname()
        for res in socket.getaddrinfo(host, None):
            addr = res[4][0]
            # filter IPv6 link-local/noise if you want
            ips.add(addr)
    except Exception:
        pass
    ips.add(_primary_ip())
    return sorted(ips)

def collect_host_info() -> Dict:
    return {
        "hostname": socket.gethostname(),
        "primary_ip": _primary_ip(),
        "ips": _all_ips(),
        "container": {
            "hostname_env": os.environ.get("HOSTNAME"),
            "pod_name": os.environ.get("HOSTNAME", None),
        },
        "runtime": {
            "python": os.environ.get("PYTHON_VERSION"),
        }
    }
