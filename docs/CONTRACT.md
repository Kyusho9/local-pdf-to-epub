# 模块契约 v1

Python 包 src/local_epub。通用数据均为 JSON 可序列化字典；页面编号从 1 起，bbox 为 PDF 点单位、左上原点。

## backend.py（解析）

`inspect_pdf(path, password=None) -> dict` 返回 page_count, metadata:{title,author}, toc:[{title,level,page}], permissions。
`extract_page(path, page_number, output_dir, options, resource_dir, password=None) -> dict` 返回 {number,width,height,status:'success',method:'text'|'ocr'|'mixed',blocks:[],issues:[],raw:{...}}。
options = {mode:'auto'|'text'|'ocr',dpi:180}。
blocks 每项 {id:'p1-b1',kind:'paragraph'|'heading'|'image'|'table'|'footnote',text:'',level:1,sources:[{page:1,bbox:[x0,y0,x1,y1]}],confidence:null|{value:0.9,source:'RapidOCR recognition'},asset:null|'assets/p1-1.png',rows:null|[[...]],issues:[]}。所有块应有稳定 id。保留原文，不润色。资源只从 resource_dir 读取，不准自动下载。
`render_page(path,page_number,destination,password=None,dpi=100)` 输出 PNG。

## 主控 pipeline.py（root）

`create_job(source, job_dir, options=None, password=None) -> dict`
`run_job(job_dir, resource_dir, password=None, retry_pages=None, progress=None, cancel=None) -> dict` progress(dict)，cancel()->bool。
`load_job(job_dir) -> dict`
`save_edit(job_dir, block_id, text, kind=None, level=None)`
`set_export_override(job_dir, block_id, value)`：value为auto/keep/exclude；决定保存在edits.visibility并绑定自动稿签名。原稿变化则报告export_conflicts，安全保留新内容。
`export_job(job_dir, output, title=None, author=None, language='zh-CN', allow_partial=False, font_size=1.0, line_height=1.8, epubcheck=None) -> dict`
任务 job.json: {schema_version,id,source:{path,sha256},options,metadata:{title,author},toc:[],pages:[{number,status,error,cache_key}],status,issues:[]}。
页面自动稿 pages/0001.json；原始稿 raw/0001.json；人工修改 edits.json；合成结果 document.json {metadata,toc,pages,blocks,issues,missing_pages,edit_conflicts}。export 返回 {output,report,...}；source.pdf 在任务内快照，原件不变。
`build_document(job_dir) -> dict` 合成自动稿与修订。

0.1.1合成结果还包含document_stage_version与export_conflicts。blocks保持完整，新增decoration（kind/reason/evidence_pages）、export_override、exclude_from_export；过滤只用于导出和重排预览，不改raw或pages缓存。

## resources.py（root）

`default_resource_dir() -> Path`；`check_resources(path) -> dict` {ok,missing:[],errors:[],manifest:dict|null}；`import_pack(zip_path,dest) -> dict`；`download_pack(dest,progress=None) -> dict` 用户主动调用才允许联网；`prepare_from_installed(dest)` 开发准备；`export_pack(source,zip_path)`。
模型路径 det.onnx, rec.onnx, cls.onnx 及 manifest.json。CPUExecutionProvider 唯一基线。

## CLI / 进程协议（root）

`python -m local_epub convert INPUT --job DIR --resources DIR --output FILE --mode auto --password-file FILE --allow-partial`
`python -m local_epub resume JOB --resources DIR --retry-pages 2,3 --output FILE --cancel-file FILE`
`python -m local_epub export JOB --output FILE [--title ... --author ... --font-size 1 --line-height 1.8 --allow-partial]`
JSONL stdout：{event:'progress',stage:'parse',page:1,total:3,message:'...'} / {event:'done',...} / {event:'error',message:'...'}。取消通过 cancel 文件，页间检查且可强制终止；正在处理页重启时变为 pending。
冻结程序通过同目录的 `LocalPDFtoEPUB-CLI` 后台程序调用 CLI；普通启动GUI，gui-test为显式验收入口。`visibility JOB BLOCK --value auto|keep|exclude`可设置单块的正文保留/排除。

## gui.py（GUI agent）

main() 启动；Window 类提供选择/拖拽 PDF、资源检查、输出、启动 QProcess、取消、打开任务、原页图、块列表编辑和导出。GUI 主线程不得加载 OCR；用上述公共接口。预览 QTextBrowser 只渲染本地内容。冻结程序 worker 命令需检测 sys.frozen。
