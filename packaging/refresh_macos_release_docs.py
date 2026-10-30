#!/usr/bin/env python3
"""Refresh release notices, preserve code/symlinks, re-sign and verify a Mac ZIP.

Only Finder/installation metadata, the three named project documents and root
ad-hoc signature may change. Every original member, including AppleDouble files,
is verified before cleanup. The original ZIP is retained in a new work directory.
No downloads or app source changes are performed. Run on macOS with its built-in
ditto/codesign tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import zipfile


DOCUMENTS = ("THIRD_PARTY.md", "THIRD_PARTY_SOURCES.md", "PLATFORMS.md")
APP_NAME = "LocalPDFtoEPUB.app"
SIGNATURE_PATHS = {"Contents/_CodeSignature/CodeResources", "Contents/MacOS/LocalPDFtoEPUB"}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def metadata_path(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (any(p in (".DS_Store", "__MACOSX") or p.startswith("._") for p in parts)
            or (parts[-1] == "direct_url.json" and any(p.endswith(".dist-info") for p in parts)))


def run(args: list[str | Path], *, env=None, timeout=60) -> dict:
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                            env=env, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stdout}{result.stderr}")
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def tree(app: Path) -> dict:
    records = {}
    for path in sorted(app.rglob("*")):
        name = path.relative_to(app).as_posix()
        if path.is_symlink():
            if not path.exists() or not path.resolve().is_relative_to(app.resolve()):
                raise ValueError("Broken or external bundle symlink: " + name)
            records[name] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            records[name] = {"kind": "file", "bytes": path.stat().st_size, "sha256": sha(path)}
    return records


def zip_inventory(path: Path) -> dict:
    """Inventory every non-directory member, including packaging metadata."""
    records = {}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Input ZIP CRC failure")
        seen = set()
        for info in archive.infolist():
            name = info.filename.rstrip("/")
            parts = PurePosixPath(name).parts
            if (not parts or PurePosixPath(name).is_absolute() or ".." in parts
                    or "\\" in name or name in seen):
                raise ValueError("Unsafe or duplicate archive member: " + name)
            seen.add(name)
            if parts[0] != APP_NAME and not metadata_path(name):
                raise ValueError("Unexpected archive root: " + name)
            if info.is_dir():
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                records[name] = {"kind": "symlink", "target": archive.read(info).decode("utf-8")}
            else:
                with archive.open(info) as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                records[name] = {"kind": "file", "bytes": info.file_size, "sha256": digest}
    return records


def app_inventory(records: dict) -> dict:
    prefix = APP_NAME + "/"
    return {name[len(prefix):]: record for name, record in records.items()
            if name.startswith(prefix)}


def extract_verified(path: Path, destination: Path, records: dict) -> None:
    """Extract all data without letting ditto consume/omit AppleDouble members."""
    links = {name for name, record in records.items() if record["kind"] == "symlink"}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename.rstrip("/")
            if any(parent.as_posix() in links for parent in PurePosixPath(name).parents):
                raise ValueError("Archive member is nested below a symlink: " + name)
            target = destination / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif name not in links:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                target.chmod((info.external_attr >> 16) & 0o777 or 0o644)
        for name in sorted(links):
            value = records[name]["target"]
            normalized = os.path.normpath(str(PurePosixPath(name).parent / value))
            if (PurePosixPath(value).is_absolute() or "\\" in value
                    or not normalized.startswith(APP_NAME + "/")):
                raise ValueError("Unsafe bundle symlink target: " + name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(value)
    if tree(destination) != records:
        raise ValueError("Full extraction, including metadata, differs from original ZIP")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="A new or empty working directory")
    parser.add_argument("--docs-dir", type=Path, default=Path(__file__).resolve().parents[1] / "docs")
    parser.add_argument("--reuse-app", type=Path, help="Optional extracted app; must match input ZIP exactly")
    args = parser.parse_args()
    if args.work.exists() and any(args.work.iterdir()):
        raise ValueError("Work directory must be new or empty")
    args.work.mkdir(parents=True, exist_ok=True)
    input_sha = sha(args.archive)
    backup = args.work / "before-docs-refresh.zip"
    shutil.copy2(args.archive, backup)
    if sha(backup) != input_sha:
        raise ValueError("Backup digest mismatch")
    all_original = zip_inventory(backup)
    original = app_inventory(all_original)
    extract = args.work / "updated"
    extract.mkdir()
    app = extract / APP_NAME
    ditto = ["/usr/bin/ditto", "--norsrc", "--noextattr", "--noqtn", "--noacl"]
    if args.reuse_app:
        if tree(args.reuse_app) != original:
            raise ValueError("Reusable app does not exactly match current archive")
    # Always verify a full extraction. ditto --norsrc would silently drop the
    # __MACOSX sidecar files before they could be compared with the input ZIP.
    extract_verified(backup, extract, all_original)
    if tree(app) != original:
        raise ValueError("App extraction/copy changed runtime files or symlinks")
    removed = []
    expected_metadata = {name: record for name, record in all_original.items() if metadata_path(name)}
    for path in sorted(extract.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        name = path.relative_to(extract).as_posix()
        if metadata_path(name):
            if path.is_symlink() or path.is_file():
                removed.append(name)
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
    if set(removed) != set(expected_metadata):
        raise ValueError("Metadata removal did not exactly match the inspected source inventory")
    if tree(extract) != {name: record for name, record in all_original.items()
                         if name not in expected_metadata}:
        raise ValueError("Metadata cleanup changed another archive payload")
    docs = {}
    for name in DOCUMENTS:
        source = args.docs_dir / name
        destination = app / "Contents/Resources/docs" / name
        if destination.is_symlink():
            raise ValueError("Document destination must not be a symlink")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        docs[name] = {"bytes": source.stat().st_size, "sha256": sha(source)}
    run(["/usr/bin/xattr", "-cr", app])
    executable = app / "Contents/MacOS/LocalPDFtoEPUB"
    control = args.work / "main-before-sign"
    shutil.copy2(executable, control)
    signing = run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", app])
    verification = run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
    after = tree(app)
    doc_paths = {"Contents/Resources/docs/" + name for name in DOCUMENTS}
    changed = {name for name in original.keys() & after.keys() if original[name] != after[name]}
    added = set(after) - set(original)
    deleted = set(original) - set(after)
    expected_deleted = set(app_inventory(expected_metadata))
    if (deleted != expected_deleted or not added <= doc_paths
            or not changed <= doc_paths | SIGNATURE_PATHS):
        raise ValueError(f"Unexpected payload changes: changed={changed}, added={added}, deleted={deleted}")
    unsigned_before = args.work / "unsigned-before"
    unsigned_after = args.work / "unsigned-after"
    shutil.copy2(control, unsigned_before)
    shutil.copy2(executable, unsigned_after)
    for path in (unsigned_before, unsigned_after):
        run(["/usr/bin/codesign", "--remove-signature", path])
    if sha(unsigned_before) != sha(unsigned_after):
        raise ValueError("Main executable code changed beyond its signature")
    candidate = args.work / args.archive.name
    run(ditto + ["-c", "-k", "--keepParent", app, candidate])
    with zipfile.ZipFile(candidate) as archive:
        if any(metadata_path(name) for name in archive.namelist()):
            raise ValueError("Installation/Finder metadata remained in candidate")
    if zip_inventory(candidate) != {APP_NAME + "/" + name: record for name, record in after.items()}:
        raise ValueError("ZIP changed a runtime file or symlink")
    check = args.work / "extraction-check"
    check.mkdir()
    run(ditto + ["-x", "-k", candidate, check])
    check_app = check / APP_NAME
    if tree(check_app) != after:
        raise ValueError("Re-extracted package differs from signed app")
    extracted_verification = run(["/usr/bin/codesign", "--verify", "--deep", "--strict", check_app])
    clean_env = {key: value for key, value in os.environ.items()
                 if not key.startswith(("PYTHON", "DYLD_", "VIRTUAL_ENV", "CONDA"))}
    clean_env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    cli = check_app / "Contents/MacOS/LocalPDFtoEPUB-CLI"
    smoke = run([cli, "--help"], env=clean_env)
    if "convert" not in smoke["stdout"] or "resources" not in smoke["stdout"]:
        raise ValueError("CLI help did not expose expected commands")
    for name, record in docs.items():
        if sha(check_app / "Contents/Resources/docs" / name) != record["sha256"]:
            raise ValueError("Final packaged document mismatch: " + name)
    final_sha = sha(candidate)
    report = {"input_sha256": input_sha, "input_app_matches_reuse": bool(args.reuse_app),
              "archive": args.archive.name, "bytes": candidate.stat().st_size, "sha256": final_sha,
              "documents": docs, "removed_metadata": removed, "changed_files": sorted(changed),
              "full_original_members_verified": len(all_original),
              "full_original_files": all_original,
              "removed_metadata_original_records": expected_metadata,
              "added_files": sorted(added), "deleted_files": sorted(deleted),
              "unchanged_regular_files": sum(record["kind"] == "file" and original.get(name) == record
                                             for name, record in after.items()),
              "unchanged_symlinks": sum(record["kind"] == "symlink" for record in after.values()),
              "unsigned_main_sha256": sha(unsigned_before), "code_unchanged": True,
              "signing": signing, "strict_signature": verification,
              "extracted_strict_signature": extracted_verification,
              "clean_path": clean_env["PATH"], "cli_help_smoke": smoke,
              "final_files": after}
    (args.work / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    candidate.replace(args.archive)
    Path(str(args.archive) + ".sha256").write_text(f"{final_sha}  {args.archive.name}\n", encoding="ascii")
    print(json.dumps({key: report[key] for key in
                      ("archive", "bytes", "sha256", "code_unchanged", "unchanged_regular_files",
                       "unchanged_symlinks", "changed_files", "added_files", "documents")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
