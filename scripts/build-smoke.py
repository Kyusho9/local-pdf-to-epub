#!/usr/bin/env python3
"""Exercise the packaged CLI without PYTHONPATH or developer tools on PATH.

This is not a claim of a clean-machine Windows test. On macOS --deny-network
uses the real OS sandbox on the bundled process and all its descendants.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--deny-network", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "packaged-smoke")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.resources = args.resources.resolve()
    executable = args.executable or (ROOT / "dist" / "LocalPDFtoEPUB.app" / "Contents" / "MacOS" / "LocalPDFtoEPUB-CLI" if platform.system() == "Darwin" else ROOT / "dist" / "LocalPDFtoEPUB" / "LocalPDFtoEPUB-CLI.exe")
    executable = executable.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH")}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin" if os.name != "nt" else os.path.join(os.environ["SystemRoot"], "System32")
    prefix = []
    if args.deny_network:
        if platform.system() != "Darwin":
            raise SystemExit("--deny-network 当前仅实现 macOS sandbox-exec；Windows 请配置进程防火墙规则。")
        prefix = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"]
    results = []
    commands = [["--help"]]
    for source in sorted((ROOT / "tests" / "fixtures").glob("*.pdf")):
        commands.append(["convert", str(source), "--job", str(args.output / (source.stem + ".job")), "--resources", str(args.resources.resolve()), "--output", str(args.output / (source.stem + ".epub"))])
    for number, argv in enumerate(commands):
        command = [*prefix, str(executable), *argv]
        completed = subprocess.run(command, env=env, cwd=args.output, capture_output=True, text=True, encoding="utf-8", timeout=240)
        log_name = f"{number:02d}.log"
        (args.output / log_name).write_text(completed.stdout + "\nSTDERR:\n" + completed.stderr, encoding="utf-8")
        results.append({"arguments": argv, "returncode": completed.returncode, "log": log_name})
        if completed.returncode:
            print(completed.stdout)
            print(completed.stderr)
            break
    report = {"platform": platform.platform(), "executable": str(executable), "network": "OS sandbox denies all network" if args.deny_network else "not OS blocked in this run", "developer_paths_removed": True, "results": results, "passed": len(results) == len(commands) and all(item["returncode"] == 0 for item in results)}
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
