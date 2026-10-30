import argparse
import json
from pathlib import Path
import signal
import sys


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def parser():
    p = argparse.ArgumentParser(description="离线 PDF 转 EPUB；无账号、无云端推理")
    sub = p.add_subparsers(dest="command", required=True)
    for cmd in ("convert", "resume", "export", "rebuild-layout"):
        s = sub.add_parser(cmd, help="复用任务已有识别结果重新整理版面，不加载 OCR 或 GPU 模型" if cmd == "rebuild-layout" else None)
        s.add_argument("input" if cmd == "convert" else "job")
        if cmd == "convert":
            s.add_argument("--job", required=True)
            s.add_argument("--mode", choices=["auto", "text", "ocr"], default="auto")
            s.add_argument("--dpi", type=int, default=180)
        if cmd != "export":
            s.add_argument("--password-file")
            s.add_argument("--cancel-file")
        if cmd in ("convert", "resume"):
            s.add_argument("--resources")
            s.add_argument("--ocr-device", choices=["cpu", "directml", "cuda"],
                           default=("directml" if sys.platform == "win32" else "cpu") if cmd == "convert" else None,
                           help="OCR 设备；Windows 新任务默认 DirectML，Mac 默认 CPU。Windows NVIDIA 用户可选 CUDA；resume 省略则沿用任务设置，旧任务仍用 CPU")
            s.add_argument("--gpu-fallback", action=argparse.BooleanOptionalAction,
                           default=True if cmd == "convert" else None,
                           help="GPU 不可用或失败时整页回退 CPU；关闭后需 OCR 的页明确失败")
            s.add_argument("--gpu-runtime", help="所选 GPU 后端的本地运行包目录；转换不自动下载")
        if cmd == "resume":
            s.add_argument("--retry-pages")
        if cmd == "rebuild-layout":
            s.add_argument("--pages", help="需要重新整理的原 PDF 页码，以逗号分隔；省略则处理全部成功识别的页面")
        s.add_argument("--output", required=cmd == "export")
        s.add_argument("--title")
        s.add_argument("--author")
        s.add_argument("--language", default="zh-CN")
        s.add_argument("--font-size", type=float, default=1.0)
        s.add_argument("--line-height", type=float, default=1.8)
        s.add_argument("--retain-page-margins", action=argparse.BooleanOptionalAction, default=False,
                       help="保留页眉、页脚和原页码（需 Vivliostyle 兼容阅读器，默认关闭；每阅读页采用起始来源页的页眉页脚及该来源的原页码）")
        s.add_argument("--include-page-info", action=argparse.BooleanOptionalAction, default=None,
                       help=argparse.SUPPRESS)
        s.add_argument("--allow-partial", action="store_true")
        s.add_argument("--epubcheck")
    s = sub.add_parser("inspect")
    s.add_argument("input")
    s.add_argument("--password-file")
    s = sub.add_parser("status")
    s.add_argument("job")
    s = sub.add_parser("exclude")
    s.add_argument("job")
    s.add_argument("--pages", required=True)
    s = sub.add_parser("edit")
    s.add_argument("job")
    s.add_argument("block")
    s.add_argument("--text-file", required=True)
    s.add_argument("--kind", choices=["paragraph", "heading", "footnote"])
    s.add_argument("--level", type=int)
    s = sub.add_parser("visibility", help="设置单个内容块是否进入正文，不删除原稿")
    s.add_argument("job")
    s.add_argument("block")
    s.add_argument("--value", choices=["auto", "keep", "exclude"], required=True)
    s = sub.add_parser("resources")
    s.add_argument("action", choices=["check", "prepare-installed", "import", "export", "download"])
    s.add_argument("--dir")
    s.add_argument("--pack")
    s = sub.add_parser("gpu-resources", help="主动准备 Windows DirectML 或可选 CUDA 运行包；CPU 不需要")
    s.add_argument("action", choices=["check", "import", "export", "download"])
    s.add_argument("--dir")
    s.add_argument("--pack")
    s.add_argument("--backend", choices=["directml", "cuda"], default="directml",
                   help="运行包后端，默认 DirectML；两种运行包分别存放，不可互换")
    return p


def main(argv=None):
    # JSONL is a UTF-8 protocol shared with the GUI, independent of Windows
    # code pages or the shell's PYTHONIOENCODING setting.
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if configure is not None:
            configure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args(argv)
    from .resources import default_resource_dir
    try:
        if args.command == "gpu-resources":
            from .gpu_resources import (default_gpu_runtime_dir, check_gpu_runtime,
                                        import_gpu_pack, export_gpu_pack, download_gpu_runtime)
            dest = args.dir or default_gpu_runtime_dir(backend=args.backend)
            if args.action in ("import", "export") and not args.pack:
                raise ValueError("需要 --pack ZIP路径")
            progress = lambda message: emit({"event": "progress", "stage": "gpu_resources", "message": message})
            result = {"check": lambda: check_gpu_runtime(dest, backend=args.backend),
                      "import": lambda: import_gpu_pack(args.pack, dest, progress=progress, backend=args.backend),
                      "export": lambda: export_gpu_pack(dest, args.pack, backend=args.backend),
                      "download": lambda: download_gpu_runtime(dest, progress=progress, backend=args.backend)}[args.action]()
            emit({"event": "done", **result})
            return 0 if result.get("ok", True) else 2
        if args.command == "resources":
            from .resources import check_resources, prepare_from_installed, import_pack, export_pack, download_pack
            dest = args.dir or default_resource_dir()
            if args.action in ("import", "export") and not args.pack:
                raise ValueError("需要 --pack ZIP路径")
            result = {"check": lambda: check_resources(dest), "prepare-installed": lambda: prepare_from_installed(dest), "import": lambda: import_pack(args.pack, dest), "export": lambda: export_pack(dest, args.pack), "download": lambda: download_pack(dest, lambda m: emit({"event": "progress", "message": m}))}[args.action]()
            emit({"event": "done", **result})
            return 0 if result.get("ok", True) else 2
        from .offline import enforce_offline
        enforce_offline()
        if args.command in ("convert", "resume", "export", "rebuild-layout"):
            if args.include_page_info:
                raise ValueError("原页信息附录已移除；不再支持 --include-page-info。需要真实页边内容时，请使用 --retain-page-margins 并在 Vivliostyle 兼容阅读器打开。")
            from .page_margins import require_margin_support
            require_margin_support(args.retain_page_margins)
        from .pipeline import create_job, run_job, load_job, build_document, export_job, save_edit, exclude_pages, set_export_override
        password = Path(args.password_file).read_text(encoding="utf-8").rstrip("\r\n") if getattr(args, "password_file", None) else None
        if args.command == "inspect":
            from .backend import inspect_pdf
            emit({"event": "done", **inspect_pdf(args.input, password)})
            return 0
        if args.command == "status":
            emit({"event": "done", "job": load_job(args.job), "document": build_document(args.job)})
            return 0
        if args.command == "edit":
            save_edit(args.job, args.block, Path(args.text_file).read_text(encoding="utf-8"), args.kind, args.level)
            emit({"event": "done", "message": "修订已保存"})
            return 0
        if args.command == "visibility":
            set_export_override(args.job, args.block, args.value)
            emit({"event": "done", "message": "正文处理决定已保存；原始内容保留", "value": args.value})
            return 0
        if args.command == "exclude":
            exclude_pages(args.job, [int(n) for n in args.pages.split(",")])
            emit({"event": "done", "message": "页面已标记主动排除，导出将注明缺页"})
            return 0
        cancelled = [False]
        def on_signal(*_):
            cancelled[0] = True
        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        def cancel():
            return cancelled[0] or bool(getattr(args, "cancel_file", None) and Path(args.cancel_file).exists())
        if args.command == "convert":
            create_job(args.input, args.job, {"mode": args.mode, "dpi": args.dpi,
                       "ocr_device": args.ocr_device, "gpu_fallback": args.gpu_fallback}, password)
        elif args.command == "resume" and (args.ocr_device is not None or args.gpu_fallback is not None):
            from .pipeline import update_job_device
            update_job_device(args.job, ocr_device=args.ocr_device, gpu_fallback=args.gpu_fallback)
        job = None
        if args.command == "rebuild-layout":
            from .pipeline import rebuild_layout
            pages = [int(n) for n in args.pages.split(",")] if args.pages else None
            job = rebuild_layout(args.job, password=password, pages=pages, progress=emit, cancel=cancel)
        elif args.command in ("convert", "resume"):
            retries = [int(n) for n in args.retry_pages.split(",")] if getattr(args, "retry_pages", None) else None
            job = run_job(args.job, args.resources or default_resource_dir(), password=password,
                          retry_pages=retries, progress=emit, cancel=cancel, gpu_runtime_dir=args.gpu_runtime)
        if job:
            if job["status"] == "cancelled":
                emit({"event": "done", "status": "cancelled", "message": "任务已取消，检查点已保存", "job": args.job,
                      "device": job.get("device"), "execution_summary": job.get("execution_summary")})
                return 130
        result = {}
        if args.output:
            result = export_job(args.job, args.output, title=args.title, author=args.author, language=args.language, allow_partial=args.allow_partial, font_size=args.font_size, line_height=args.line_height, epubcheck=args.epubcheck, retain_page_margins=args.retain_page_margins)
        emit({"event": "done", "status": job["status"] if job else ("complete" if result.get("complete") else "incomplete"),
              "job": args.job, **({"device": job.get("device"), "execution_summary": job.get("execution_summary")} if job else {}), **result})
        if result.get("format", {}).get("epubcheck", {}).get("status") == "failed":
            return 3
        return 0 if (not job or job["status"] == "complete") and result.get("complete", True) else 2
    except Exception as e:
        emit({"event": "error", "message": f"{type(e).__name__}: {e}"})
        return 1

if __name__ == "__main__":
    sys.exit(main())
