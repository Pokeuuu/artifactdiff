from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from artifactdiff_api.app import create_app
from artifactdiff_api.db import Comparison, utcnow
from artifactdiff_api.settings import Settings


def make_client(tmp_path: Path, **overrides: Any) -> TestClient:
    params: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "isolation": "inline",
        "web_dir": tmp_path / "noweb",
        "rate_limit_per_minute": 0,
        **overrides,
    }
    settings = Settings(**params)
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


def wait(client: TestClient, cid: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = client.get(f"/api/v1/comparisons/{cid}").json()
        if d["job_status"] not in ("queued", "running"):
            return d
        time.sleep(0.05)
    raise AssertionError("comparison did not finish")


def upload(client: TestClient, a: Path, b: Path, **form: str) -> Any:
    with a.open("rb") as fa, b.open("rb") as fb:
        return client.post("/api/v1/compare", files={"old": (a.name, fa), "new": (b.name, fb)}, data=form)


def test_health_and_plugins(client: TestClient) -> None:
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["database"] == "ok" and h["plugins_total"] >= 11
    plugins = client.get("/api/v1/plugins").json()["plugins"]
    assert any(p["name"] == "safetensors" for p in plugins)
    assert client.get("/api/v1/plugins/csv").json()["options_schema"]["properties"]["primary_key"]
    assert client.get("/api/v1/plugins/nope").status_code == 404
    assert client.get("/api/openapi.json").status_code == 200


def test_security_headers(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_full_lifecycle(client: TestClient, tmp_path: Path) -> None:
    from samples import image_pair

    a, b = image_pair(tmp_path)
    r = upload(client, a, b)
    assert r.status_code == 202, r.text
    cid = r.json()["id"]
    d = wait(client, cid)
    assert d["job_status"] == "completed" and d["result_status"] == "different" and d["plugin"] == "image"
    assets = d["result"]["assets"]
    assert assets and all(x["data_base64"] is None and x["url"] for x in assets)
    img = client.get(assets[0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    html = client.get(f"/api/v1/comparisons/{cid}/report?format=html")
    assert html.status_code == 200 and "data:image/png;base64," in html.text
    md = client.get(f"/api/v1/comparisons/{cid}/report?format=markdown")
    assert "artifactdiff-report" in md.text
    js = client.get(f"/api/v1/comparisons/{cid}/report?format=json").json()
    assert js["assets"][0]["data_base64"]
    f = client.get(f"/api/v1/comparisons/{cid}/files/old")
    assert f.status_code == 200 and f.content == a.read_bytes()
    assert f.headers["content-type"] == "application/octet-stream" and "attachment" in f.headers["content-disposition"]
    page = client.get("/api/v1/comparisons?q=new").json()
    assert page["total"] == 1 and page["items"][0]["id"] == cid
    stats = client.get("/api/v1/stats").json()
    assert stats["total_comparisons"] == 1 and stats["plugin_usage"][0]["plugin"] == "image"
    assert sum(p["comparisons"] for p in stats["activity"]) == 1
    assert stats["recent_changes"]
    assert client.delete(f"/api/v1/comparisons/{cid}").status_code == 204
    assert client.get(f"/api/v1/comparisons/{cid}").status_code == 404


def test_validation_errors(client: TestClient, tmp_path: Path) -> None:
    from helpers import write

    a = write(tmp_path / "a.json", '{"a":1}')
    b = write(tmp_path / "b.json", '{"a":2}')
    assert upload(client, a, b, options="not json").status_code == 422
    assert upload(client, a, b, options='["x"]').status_code == 422
    r = upload(client, a, b, options='{"bogus": 1}')
    assert r.status_code == 422 and "Invalid options" in r.json()["detail"]
    assert upload(client, a, b, plugin="nope").status_code == 422
    assert client.post("/api/v1/compare").status_code == 422
    assert client.get("/api/v1/comparisons/../../etc").status_code == 404
    assert client.get("/api/v1/comparisons/" + "z" * 32).status_code == 404


def test_failed_comparison_is_recorded(client: TestClient, tmp_path: Path) -> None:
    from helpers import write

    a = write(tmp_path / "a.json", '{"a":1}')
    b = write(tmp_path / "b.json", '{"a":')
    d = wait(client, upload(client, a, b).json()["id"])
    assert d["job_status"] == "completed" and d["result_status"] == "error" and "not valid JSON" in d["error"]


def test_upload_limit(tmp_path: Path) -> None:
    from helpers import write

    with make_client(tmp_path, max_upload_mb=0.001) as c:
        a = write(tmp_path / "a.bin", b"x" * 10)
        b = write(tmp_path / "b.bin", b"y" * 5000)
        r = upload(c, a, b)
        assert r.status_code == 413
        assert not list((tmp_path / "data" / "comparisons").iterdir()), "rejected uploads must not be kept"


def test_body_limit_middleware_streams(tmp_path: Path) -> None:
    # limit = 2 * max_upload + 1 MiB of multipart slack
    with make_client(tmp_path, max_upload_mb=0.01) as c:
        big = b"x" * (2 * 1024 * 1024)
        hdr = {"content-type": "multipart/form-data; boundary=x"}
        assert c.post("/api/v1/compare", content=big, headers=hdr).status_code == 413  # Content-Length precheck

        def chunks() -> Iterator[bytes]:  # well-formed multipart, no Content-Length: rejected while streaming
            yield (
                b'--x\r\nContent-Disposition: form-data; name="old"; filename="a.bin"\r\n'
                b"Content-Type: application/octet-stream\r\n\r\n"
            )
            for _ in range(32):
                yield b"x" * 65536

        assert c.post("/api/v1/compare", content=chunks(), headers=hdr).status_code == 413


def test_rate_limit(tmp_path: Path) -> None:
    from helpers import write

    settings = Settings(data_dir=tmp_path / "d", isolation="inline", rate_limit_per_minute=2, web_dir=tmp_path / "x")
    with TestClient(create_app(settings)) as c:
        a = write(tmp_path / "a.txt", "a")
        b = write(tmp_path / "b.txt", "b")
        codes = [upload(c, a, b).status_code for _ in range(3)]
        assert codes == [202, 202, 429]


def test_token_auth(tmp_path: Path) -> None:
    with make_client(tmp_path, api_token="s3cret") as c:
        assert c.get("/api/v1/health").status_code == 200  # health stays public
        assert c.get("/api/v1/plugins").status_code == 401
        assert c.get("/api/v1/plugins", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/v1/plugins", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/v1/config", headers={"Authorization": "Bearer s3cret"}).json()["auth_required"]


def test_retention_deletes_inputs_but_keeps_results(client: TestClient, tmp_path: Path) -> None:
    from helpers import write

    a = write(tmp_path / "a.json", '{"a":1}')
    b = write(tmp_path / "b.json", '{"a":2}')
    cid = upload(client, a, b).json()["id"]
    wait(client, cid)
    state = client.app.state.ad  # type: ignore[attr-defined]
    with state.db.session() as s:
        s.get(Comparison, cid).inputs_expire_at = utcnow() - timedelta(minutes=1)
    assert state.jobs.sweep()["inputs_deleted"] == 1
    assert client.get(f"/api/v1/comparisons/{cid}/files/new").status_code == 410
    d = client.get(f"/api/v1/comparisons/{cid}").json()
    assert d["inputs_available"] is False and d["result"]["statistics"]["total_changes"] == 1


def test_clear_history_requires_confirm(client: TestClient, tmp_path: Path) -> None:
    from helpers import write

    a = write(tmp_path / "a.txt", "a")
    b = write(tmp_path / "b.txt", "b")
    wait(client, upload(client, a, b).json()["id"])
    assert client.delete("/api/v1/comparisons").status_code == 400
    assert client.delete("/api/v1/comparisons?confirm=true").status_code == 204
    assert client.get("/api/v1/comparisons").json()["total"] == 0


def test_filename_sanitised(client: TestClient, tmp_path: Path) -> None:
    from helpers import write

    a = write(tmp_path / "a.json", "{}")
    with a.open("rb") as fa, a.open("rb") as fb:
        r = client.post("/api/v1/compare", files={"old": ("../../evil\x00.json", fa), "new": ("C:\\x\\b.json", fb)})
    d = wait(client, r.json()["id"])
    # path components and control characters never survive (httpx percent-encodes the NUL as %00)
    assert d["old_name"] in ("evil.json", "evil%00.json") and d["new_name"] == "b.json"
    stored = list((tmp_path / "data" / "comparisons" / d["id"] / "inputs").iterdir())
    assert sorted(p.name for p in stored) == ["new.json", "old.json"]


def test_process_isolation_and_cancel(tmp_path: Path) -> None:
    from helpers import write

    with make_client(tmp_path, isolation="process") as c:
        a = write(tmp_path / "a.json", json.dumps({"a": 1}))
        b = write(tmp_path / "b.json", json.dumps({"a": 2}))
        d = wait(c, upload(c, a, b).json()["id"], timeout=60)
        assert d["result_status"] == "different"
        cid = upload(c, a, b).json()["id"]
        resp = c.post(f"/api/v1/comparisons/{cid}/cancel")
        assert resp.status_code in (200, 409)  # may already have finished
        d = wait(c, cid, timeout=60)
        assert d["job_status"] in ("cancelled", "completed")


def test_spa_served_when_built(tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>ArtifactDiff</title>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    settings = Settings(data_dir=tmp_path / "d", isolation="inline", web_dir=web)
    with TestClient(create_app(settings)) as c:
        assert "ArtifactDiff" in c.get("/").text
        assert "ArtifactDiff" in c.get("/history/abc").text  # client-side route
        assert c.get("/assets/app.js").status_code == 200
        assert c.get("/api/v1/nope").status_code == 404
        assert c.get("/..%2F..%2Fpyproject.toml").text.startswith("<!doctype")
