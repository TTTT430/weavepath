from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from api.app import create_app
from api.llm import DisabledLLM
from graph_core import GraphStore
from graph_core.attachments import parse_attachment


def _docx_bytes() -> bytes:
    document = Document()
    document.add_heading("Route memory", level=1)
    document.add_paragraph("Only this conversation may use the private requirement.")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cases"
    sheet.append(["id", "expected"])
    sheet.append(["case-1", "positive"])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _pptx_bytes() -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2))
    box.text_frame.text = "Experiment acceptance criterion"
    stream = io.BytesIO()
    presentation.save(stream)
    return stream.getvalue()


def test_office_parsers_preserve_human_source_locators(tmp_path: Path):
    samples = [
        ("notes.docx", _docx_bytes(), "python-docx", "document", "private requirement"),
        ("cases.xlsx", _xlsx_bytes(), "openpyxl", "sheet Cases", "case-1"),
        ("brief.pptx", _pptx_bytes(), "python-pptx", "slide 1", "acceptance criterion"),
    ]
    for name, content, expected_parser, expected_locator, expected_text in samples:
        path = tmp_path / name
        path.write_bytes(content)
        parser, chunks = parse_attachment(path, name, "application/octet-stream")
        assert parser == expected_parser
        assert expected_locator in chunks[0]["locator"]
        assert expected_text in " ".join(chunk["content"] for chunk in chunks)


def test_disk_assets_route_listing_and_bound_citations_are_isolated(tmp_path: Path):
    database = tmp_path / "workspace.db"
    store = GraphStore(database)
    app = create_app(store, DisabledLLM())
    with TestClient(app) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Files", "rootTitle": "A", "rootInstanceId": "A",
        }).json()
        workflow_id = graph["workflowId"]
        client.post(
            f"/api/v1/workflows/{workflow_id}/instances/A/fork",
            json={"title": "B", "instanceId": "B"},
        )
        client.post(
            f"/api/v1/workflows/{workflow_id}/instances/A/fork",
            json={"title": "E", "instanceId": "E"},
        )
        uploaded = client.post(
            f"/api/v1/workflows/{workflow_id}/instances/B/attachments"
            "?name=private.docx&mimeType=application%2Fvnd.openxmlformats-officedocument.wordprocessingml.document",
            content=_docx_bytes(),
        )
        assert uploaded.status_code == 201
        attachment = uploaded.json()
        assert attachment["parseStatus"] == "processing"
        attachment = client.get(
            f"/api/v1/workflows/{workflow_id}/instances/B/attachments/"
            f"{attachment['attachmentId']}"
        ).json()
        assert attachment["parseStatus"] == "ready"
        assert attachment["parser"] == "python-docx"
        assert attachment["chunkCount"] >= 1

        object_path = database.parent / "files" / "objects" / attachment["sha256"][:2] / attachment["sha256"]
        assert object_path.is_file()
        row = store._conn.execute(
            "SELECT content_text,storage_key FROM message_attachments WHERE id=?",
            (attachment["attachmentId"],),
        ).fetchone()
        assert row["content_text"] == ""
        assert row["storage_key"] == attachment["sha256"]

        envelope = "[WeavePath attachments v2]\n" + json.dumps({
            "files": [{
                "attachmentId": attachment["attachmentId"],
                "name": attachment["name"],
                "mimeType": attachment["mimeType"],
                "size": attachment["size"],
            }],
            "prompt": "Find the private requirement",
        })
        assert client.post(
            f"/api/v1/workflows/{workflow_id}/instances/B/messages",
            json={"role": "user", "content": envelope},
        ).status_code == 201

        local = client.get(
            f"/api/v1/workflows/{workflow_id}/instances/B/attachments?scope=local"
        ).json()["attachments"]
        assert local[0]["contextSources"][0]["locator"].startswith("document")
        assert local[0]["inherited"] is False
        assert client.get(
            f"/api/v1/workflows/{workflow_id}/instances/E/attachments?scope=route"
        ).json()["attachments"] == []

        client.post(
            f"/api/v1/workflows/{workflow_id}/instances/B/fork",
            json={"title": "C", "instanceId": "C"},
        )
        inherited = client.get(
            f"/api/v1/workflows/{workflow_id}/instances/C/attachments?scope=route"
        ).json()["attachments"]
        assert inherited[0]["attachmentId"] == attachment["attachmentId"]
        assert inherited[0]["inherited"] is True
        detail = client.get(
            f"/api/v1/workflows/{workflow_id}/instances/C/attachments/{attachment['attachmentId']}"
        ).json()
        assert detail["chunks"][0]["preview"]

    store.close()


def test_image_upload_is_retained_with_an_explicit_ocr_status(tmp_path: Path):
    store = GraphStore(":memory:", attachment_root=tmp_path / "files")
    with TestClient(create_app(store, DisabledLLM())) as client:
        graph = client.post("/api/v1/workflows", json={
            "name": "Images", "rootTitle": "A", "rootInstanceId": "A",
        }).json()
        response = client.post(
            f"/api/v1/workflows/{graph['workflowId']}/instances/A/attachments"
            "?name=scan.png&mimeType=image%2Fpng",
            content=b"not-a-real-image-but-still-an-untrusted-asset",
        )
        assert response.status_code == 201
        assert response.json()["parseStatus"] == "processing"
        detail = client.get(
            f"/api/v1/workflows/{graph['workflowId']}/instances/A/attachments/"
            f"{response.json()['attachmentId']}"
        ).json()
        assert detail["parseStatus"] == "failed"
        assert detail["parseErrorCode"] == "attachmentOcrUnavailable"
    store.close()


def test_processing_asset_is_resumed_after_a_service_restart(tmp_path: Path):
    database = tmp_path / "workspace.db"
    store = GraphStore(database)
    workflow = store.create_workflow(
        name="Recovery", root_title="A", root_instance_id="A"
    )
    staged = store.new_attachment_staging_path()
    content = b"durable parser recovery"
    staged.write_bytes(content)
    uploaded = store.create_attachment_from_file(
        workflow["workflowId"], "A", name="recovery.txt", mime_type="text/plain",
        size_bytes=len(content), staged_path=staged,
        sha256=hashlib.sha256(content).hexdigest(), parse_immediately=False,
    )
    assert uploaded["parseStatus"] == "processing"
    store.close()

    reopened = GraphStore(database)
    detail = reopened.get_attachment(
        workflow["workflowId"], "A", uploaded["attachmentId"]
    )
    assert detail["parseStatus"] == "ready"
    assert detail["chunks"][0]["preview"] == "durable parser recovery"
    reopened.close()


def test_startup_sweep_removes_only_unreferenced_objects_and_staging_files(tmp_path: Path):
    database = tmp_path / "workspace.db"
    files = tmp_path / "files"
    orphan_hash = "a" * 64
    orphan = files / "objects" / orphan_hash[:2] / orphan_hash
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    staging = files / "staging" / "abandoned.upload"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"partial")

    store = GraphStore(database, attachment_root=files)
    assert not orphan.exists()
    assert not staging.exists()
    store.close()
