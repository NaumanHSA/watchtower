"""Repository-level checks that keep copies and versions from drifting apart."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ["packages/contracts", "controller", "worker", "token-server"]


def test_helm_chart_ships_the_same_mediamtx_config():
    canonical = (ROOT / "deploy/mediamtx/mediamtx.yml").read_bytes()
    chart_copy = (ROOT / "deploy/helm/watchtower/files/mediamtx.yml").read_bytes()
    assert chart_copy == canonical, "copy deploy/mediamtx/mediamtx.yml into deploy/helm/watchtower/files/"


def test_versions_agree():
    versions = {p: tomllib.loads((ROOT / p / "pyproject.toml").read_text())["project"]["version"] for p in PACKAGES}
    assert len(set(versions.values())) == 1, versions
    version = next(iter(versions.values()))
    chart = (ROOT / "deploy/helm/watchtower/Chart.yaml").read_text()
    assert re.search(rf'^appVersion: "{re.escape(version)}"$', chart, re.M), "Chart.yaml appVersion"
    modules = ("controller/watchtower_controller", "worker/watchtower_worker", "token-server/watchtower_token_server")
    for module in modules:
        assert f'__version__ = "{version}"' in (ROOT / module / "__init__.py").read_text(), module
