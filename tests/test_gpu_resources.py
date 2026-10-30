"""Executable runtime import must reject tampering without replacing good files.

Small inert files exercise the archive trust boundary; actual CUDA inference and
official binary preparation are separately verified on Windows hardware.
"""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from local_epub import gpu_resources as gpu


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    payload = {"python.exe": b"inert executable fixture", "python312._pth": b"python312.zip\n.\npackages\n",
               "packages/engine.bin": b"inert library fixture", "licenses/NOTICE.txt": b"Fixture only"}
    manifest = {"format_version": 1, "id": "test-only-cuda-runtime", "platform": "win32-x64",
                "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "purpose": "test"}
                          for name, data in payload.items()}}
    monkeypatch.setattr(gpu, "_expected", lambda backend="cuda": manifest)
    def pack(name="runtime.zip", changes=None, extras=None):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            for member, data in {**payload, **(changes or {})}.items():
                archive.writestr(member, data)
            archive.writestr("manifest.json", json.dumps(manifest))
            for member, data in (extras or {}).items():
                archive.writestr(member, data)
        return path
    return payload, manifest, pack


def test_offline_roundtrip_and_tamper_preserves_installed_runtime(runtime, tmp_path):
    _, _, pack = runtime
    target = tmp_path / "installed"
    assert gpu.import_gpu_pack(pack(), target)["ok"]
    (target / ".DS_Store").write_bytes(b"Finder desktop metadata")
    assert gpu.check_gpu_runtime(target)["ok"]
    old = (target / "python.exe").read_bytes()
    with pytest.raises(ValueError, match="大小|SHA-256"):
        gpu.import_gpu_pack(pack("tampered.zip", {"python.exe": b"malicious changed binary"}), target)
    assert (target / "python.exe").read_bytes() == old
    output = tmp_path / "export.zip"
    assert gpu.export_gpu_pack(target, output)["sha256"] == gpu.digest(output)
    with zipfile.ZipFile(output) as archive:
        assert ".DS_Store" not in archive.namelist()
    assert gpu.import_gpu_pack(output, tmp_path / "second")["ok"]


def test_package_cannot_declare_its_own_untrusted_binary_hash(runtime, tmp_path):
    payload, manifest, _ = runtime
    manifest = json.loads(json.dumps(manifest))
    bad = b"malicious binary"
    manifest["files"]["python.exe"].update(bytes=len(bad), sha256=hashlib.sha256(bad).hexdigest())
    path = tmp_path / "self-signed.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in {**payload, "python.exe": bad}.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="固定清单"):
        gpu.import_gpu_pack(path, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


def test_cuda_package_cannot_be_imported_as_directml(runtime, tmp_path, monkeypatch):
    _, cuda, pack = runtime
    directml = {**cuda, "id": "test-only-directml-runtime"}
    monkeypatch.setattr(gpu, "_expected", lambda backend="cuda": cuda if backend == "cuda" else directml)
    source = pack()
    with pytest.raises(ValueError, match="固定清单"):
        gpu.import_gpu_pack(source, tmp_path / "wrong-backend", backend="directml")
    assert not (tmp_path / "wrong-backend").exists()
    assert gpu.import_gpu_pack(source, tmp_path / "correct-backend", backend="cuda")["ok"]


def test_zip_traversal_case_collision_and_extra_code_are_rejected(runtime, tmp_path):
    _, _, pack = runtime
    for index, name in enumerate(("../outside.txt", "packages/ENGINE.bin", "packages/sitecustomize.py")):
        target = tmp_path / f"destination-{index}"
        with pytest.raises(ValueError):
            gpu.import_gpu_pack(pack(f"bad-{index}.zip", extras={name: b"untrusted"}), target)
        assert not target.exists()
    assert not (tmp_path / "outside.txt").exists()


def test_import_never_overwrites_unrelated_directory(runtime, tmp_path):
    _, _, pack = runtime
    target = tmp_path / "documents"
    target.mkdir()
    private = target / "book.pdf"
    private.write_bytes(b"user document")
    with pytest.raises(ValueError, match="目标非空"):
        gpu.import_gpu_pack(pack(), target)
    assert private.read_bytes() == b"user document"


def test_missing_or_changed_files_and_unexpected_bytecode_are_reported(runtime, tmp_path):
    _, _, pack = runtime
    target = tmp_path / "runtime"
    gpu.import_gpu_pack(pack(), target)
    (target / "packages/engine.bin").unlink()
    (target / "python.exe").write_bytes(b"modified")
    state = gpu.check_gpu_runtime(target)
    assert not state["ok"]
    assert "packages/engine.bin" in state["missing"]
    assert any("python.exe" in e for e in state["errors"])
    (target / "packages/extra.py").write_text("print('untrusted')")
    assert any("extra.py" in e for e in gpu.check_gpu_runtime(target)["errors"])


def test_explicit_download_reconstructs_pinned_offline_payload(runtime, tmp_path, monkeypatch):
    payload, manifest, _ = runtime
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        # A redistributable runtime selection must not copy SDK headers/tools.
        archive.writestr("include/cuda.h", b"not part of application runtime")
    raw = blob.getvalue()
    component = {"name": "inert fixture", "url": "https://example.invalid/pinned.zip", "bytes": len(raw),
                 "sha256": hashlib.sha256(raw).hexdigest(), "files": [{"source": n, "target": n} for n in payload]}
    monkeypatch.setattr(gpu, "_catalog", lambda backend="cuda": {"id": manifest["id"], "components": [component]})
    calls = []
    class Response(io.BytesIO):
        def geturl(self):
            return component["url"]
    def urlopen(request, timeout):
        calls.append(request.full_url)
        assert request.data is None
        return Response(raw)
    monkeypatch.setattr(gpu.urllib.request, "urlopen", urlopen)
    assert calls == []
    target = tmp_path / "downloaded"
    gpu.download_gpu_runtime(target)
    assert calls == [component["url"]]
    assert gpu.check_gpu_runtime(target)["ok"]
    assert not (target / "include").exists()
