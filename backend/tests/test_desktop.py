from pathlib import Path

from fastapi.testclient import TestClient

from desktop import create_desktop_app
from graph_core import GraphStore


def _web_build(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>desktop-index</html>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("desktop-asset", encoding="utf-8")
    return root


def test_desktop_app_serves_api_and_spa_from_one_origin(tmp_path: Path):
    store = GraphStore(":memory:")
    with TestClient(create_desktop_app(store, _web_build(tmp_path))) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert "desktop-index" in client.get("/").text
        assert "desktop-index" in client.get("/workflow/example").text
        assert client.get("/assets/app.js").text == "desktop-asset"
        assert client.get("/api/v1/not-a-route").status_code == 404
    store.close()


def test_desktop_app_rejects_missing_web_build(tmp_path: Path):
    store = GraphStore(":memory:")
    try:
        try:
            create_desktop_app(store, tmp_path / "missing")
        except RuntimeError as exc:
            assert "web assets are missing" in str(exc)
        else:
            raise AssertionError("missing web build should fail before startup")
    finally:
        store.close()
