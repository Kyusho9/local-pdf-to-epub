"""Source release safeguards use invented metadata, never private documents."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "source_package_privacy_test", Path(__file__).resolve().parents[1] / "scripts/package-source.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def test_private_paths_rejected_even_when_explicitly_requested(tmp_path):
    for name in ("artifacts/user-scan-example/result.json", "artifacts/private-review/result.log",
                 "artifacts/checks/footer.private.json", "artifacts/checks/existing-cache/result.json"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        assert packager.excluded(Path(name))
        with pytest.raises(ValueError, match="Private material"):
            packager.evidence_file(tmp_path, name)


def test_renamed_snapshot_directory_and_plain_evidence_are_distinguished(tmp_path):
    folder = tmp_path / "artifacts" / "arbitrary-name"
    folder.mkdir(parents=True)
    evidence = folder / "result.json"
    evidence.write_text('{"status":"passed"}', encoding="utf-8-sig")
    assert packager.evidence_file(tmp_path, evidence.relative_to(tmp_path).as_posix()) == evidence
    (folder / "source.pdf").write_bytes(b"synthetic marker only")
    assert packager.task_directory(folder)
    with pytest.raises(ValueError, match="Task data"):
        packager.evidence_file(tmp_path, evidence.relative_to(tmp_path).as_posix())
