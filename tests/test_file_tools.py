import json

import pytest

from selfai.tools import ToolContext, ToolRegistry, build_registry
from selfai.tools.files import delete_file, list_files, read_file, resolve_path, write_file

from .conftest import run


def make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing `text`."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def test_write_read_list_delete(settings):
    ctx = ToolContext(settings)
    msg = run(write_file(ctx, "reports/today.md", "# Hello\n"))
    assert "reports/today.md" in msg
    run(write_file(ctx, "reports/today.md", "more\n", append=True))
    assert run(read_file(ctx, "reports/today.md")) == "# Hello\nmore\n"

    listing = run(list_files(ctx))
    assert "[dir]  reports/" in listing
    assert "reports/today.md" in run(list_files(ctx, "reports"))
    assert "reports/today.md" in run(list_files(ctx, ".", recursive=True))

    run(delete_file(ctx, "reports"))
    assert not (settings.workspace_dir / "reports").exists()


def test_paths_cannot_escape_workspace(settings):
    ws = settings.workspace_dir
    assert resolve_path(ws, "/etc/passwd") == (ws / "etc/passwd").resolve()
    for bad in ("../secret.txt", "a/../../secret.txt", "..\\..\\x"):
        with pytest.raises(Exception, match="outside the workspace"):
            resolve_path(ws, bad)


def test_symlink_escape_is_blocked(settings, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (settings.workspace_dir / "link").symlink_to(outside)
    with pytest.raises(Exception, match="outside the workspace"):
        resolve_path(settings.workspace_dir, "link/file.txt")


def test_read_file_pages_large_files(settings):
    ctx = ToolContext(settings)
    run(write_file(ctx, "big.txt", "x" * 50))
    first = run(read_file(ctx, "big.txt", max_chars=20))
    assert first.startswith("x" * 20)
    assert "start=20" in first
    assert run(read_file(ctx, "big.txt", start=40, max_chars=20)) == "x" * 10


def test_read_file_extracts_pdf_text(settings):
    (settings.workspace_dir / "doc.pdf").write_bytes(make_pdf("Quarterly revenue grew"))
    text = run(read_file(ToolContext(settings), "doc.pdf"))
    assert "Quarterly revenue grew" in text


def test_read_file_binary(settings):
    (settings.workspace_dir / "img.bin").write_bytes(b"\xff\xfe\x00\x81" * 10)
    assert "binary file" in run(read_file(ToolContext(settings), "img.bin"))


def test_registry_reports_errors_to_the_model(settings):
    registry = build_registry()
    ctx = ToolContext(settings)
    ok, out = run(registry.execute("nope", "{}", ctx))
    assert not ok and "unknown tool" in out
    ok, out = run(registry.execute("write_file", "{not json", ctx))
    assert not ok and "not valid JSON" in out
    ok, out = run(registry.execute("write_file", json.dumps({"path": "a.txt"}), ctx))
    assert not ok and "bad arguments" in out
    ok, out = run(registry.execute("read_file", json.dumps({"path": "../x"}), ctx))
    assert not ok and "outside the workspace" in out
    ok, out = run(registry.execute("write_file", json.dumps({"path": "a.txt", "content": "hi"}), ctx))
    assert ok


def test_registry_truncates_huge_results(settings):
    settings.max_tool_result_chars = 100

    async def big(ctx):
        return "y" * 500

    from selfai.tools import Tool

    registry = ToolRegistry()
    registry.register(Tool("big", "", {"type": "object", "properties": {}}, big))
    ok, out = run(registry.execute("big", "", ToolContext(settings)))
    assert ok and out.startswith("y" * 100) and "400 more characters" in out


def test_tool_schemas_are_valid_function_definitions():
    names = set()
    for schema in build_registry().schemas():
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        names.add(fn["name"])
    assert {"web_search", "fetch_url", "download_file", "write_file", "read_file", "create_task"} <= names
