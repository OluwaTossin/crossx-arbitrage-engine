"""Read-only HTTP regression against an explicitly isolated Grafana container."""

import os
import time
from urllib.parse import urlparse

import httpx
import pytest


@pytest.mark.integration
def test_grafana_anonymous_viewer_and_admin_denial():
    url = os.environ.get("CROSSX_TEST_GRAFANA_URL")
    if not url:
        pytest.skip("set CROSSX_TEST_GRAFANA_URL to a disposable local Grafana")
    assert urlparse(url).hostname in ("localhost", "127.0.0.1")
    with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
        deadline = time.monotonic() + 60
        while True:
            try:
                dashboard = client.get("/api/dashboards/uid/crossx-phase1", timeout=1)
                if dashboard.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                pytest.fail("isolated Grafana not ready within 60 seconds")
            time.sleep(0.5)
        assert dashboard.status_code == 200
        assert dashboard.json()["meta"]["canSave"] is False
        assert dashboard.json()["meta"]["canAdmin"] is False
        assert client.get("/api/admin/settings").status_code in (401, 403)
        assert client.get("/api/admin/settings", auth=("admin", "admin")).status_code in (401, 403)
