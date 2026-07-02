#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "XHS-Downloader"
WORKER = ROOT_DIR / "xhs_worker.py"
VENV_PYTHON_CANDIDATES = (
    APP_DIR / ".venv" / "bin" / "python",
    APP_DIR / ".venv" / "Scripts" / "python.exe",
)
OUTPUT_DIR = Path.home() / "Downloads" / "XHS-Downloads"
URL_PATTERN = re.compile(
    r"(?P<url>https?://(?:www\.)?(?:xhslink\.com|xiaohongshu\.com)"
    r"[^\s，。！？、；;：:\]\[）)(<>\"']+)",
    re.IGNORECASE,
)
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group("url").rstrip(".,)")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def clean_log(line: str) -> str:
    return ANSI_PATTERN.sub("", line).rstrip()


def set_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(updates)


def append_log(job_id: str, line: str) -> None:
    if not line:
        return
    with JOBS_LOCK:
        job = JOBS.setdefault(job_id, {})
        logs = job.setdefault("logs", [])
        logs.append(clean_log(line))
        if len(logs) > 800:
            del logs[:200]


def run_worker(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not APP_DIR.exists():
        raise RuntimeError("XHS-Downloader 目录不存在")
    if not WORKER.exists():
        raise RuntimeError("xhs_worker.py 不存在")

    venv_python = next((path for path in VENV_PYTHON_CANDIDATES if path.exists()), None)
    command = (
        [str(venv_python), str(WORKER), action]
        if venv_python
        else ["uv", "run", "--no-dev", "python", str(WORKER), action]
    )

    timeout = 35 if action == "parse" else None
    try:
        process = subprocess.run(
            command,
            cwd=APP_DIR,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("解析超时，请换一个最新分享链接后再试") from error

    stdout = process.stdout.strip()
    stderr = process.stderr.strip()
    result: dict[str, Any] | None = None
    if stdout:
        try:
            result = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Worker 输出不是 JSON：{error}\n{stdout}") from error
    if process.returncode != 0:
        message = (result or {}).get("error") or stderr or "Worker 执行失败"
        raise RuntimeError(message)
    if result is None:
        raise RuntimeError("Worker 没有返回结果")
    if stderr:
        result.setdefault("logs", []).extend(
            line for line in stderr.splitlines() if line.strip()
        )
    return result


def run_download_job(job_id: str, payload: dict[str, Any]) -> None:
    set_job(job_id, status="running", startedAt=time.time())
    try:
        result = run_worker("download", payload)
    except Exception as error:
        append_log(job_id, f"下载失败：{error}")
        set_job(job_id, status="failed", error=str(error), finishedAt=time.time())
        return

    summary = summarize_download_logs(result.get("logs", []))
    append_log(
        job_id,
        "共处理 {all} 个文件，成功 {success} 个，失败 {fail} 个，跳过 {skip} 个".format(
            **summary
        ),
    )
    append_log(job_id, f"下载完成：{result.get('outputDir', str(OUTPUT_DIR))}")
    set_job(
        job_id,
        status="done",
        result=result,
        outputDir=result.get("outputDir", str(OUTPUT_DIR)),
        finishedAt=time.time(),
    )


def summarize_download_logs(logs: list[str]) -> dict[str, int]:
    success = sum(1 for line in logs if "下载成功" in line)
    skip = sum(1 for line in logs if "文件已存在，跳过下载" in line)
    fail = sum(1 for line in logs if "下载失败" in line or "格式判断失败" in line)
    return {
        "all": success + fail + skip,
        "success": success,
        "fail": fail,
        "skip": skip,
    }


def json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


def media_response(handler: BaseHTTPRequestHandler, url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    allowed_hosts = (
        "xhscdn.com",
        "xiaohongshu.com",
        "rednote.com",
    )
    if parsed.scheme not in {"http", "https"} or not any(
        parsed.netloc.endswith(host) for host in allowed_hosts
    ):
        json_response(handler, {"error": "不支持的媒体地址"}, 400)
        return

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
    except Exception as error:
        json_response(handler, {"error": f"媒体加载失败：{error}"}, 502)
        return

    handler.send_response(200)
    handler.send_header("Content-Type", infer_content_type(body, content_type))
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def infer_content_type(body: bytes, fallback: str) -> str:
    if fallback and fallback != "application/octet-stream":
        return fallback
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    if b"ftypavif" in body[:32]:
        return "image/avif"
    if b"ftypheic" in body[:32] or b"ftypheix" in body[:32]:
        return "image/heic"
    if b"ftyp" in body[:16]:
        return "video/mp4"
    return fallback or "application/octet-stream"


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>小红书采集</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #fff5f8;
      --panel: #ffffff;
      --soft: #fff9fb;
      --text: #18202a;
      --muted: #697386;
      --line: #f1d7e1;
      --brand: #f0447b;
      --brand2: #ff4638;
      --brand-dark: #c91f56;
      --ok: #198a5a;
      --warn: #9a5b00;
      --shadow: 0 18px 44px rgba(117, 41, 72, 0.12);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }

    .topbar {
      height: 62px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 28px;
      background: rgba(255, 255, 255, 0.88);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(12px);
      position: sticky;
      top: 0;
      z-index: 3;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--brand);
      font-size: 20px;
      font-weight: 800;
    }

    .mark {
      width: 28px;
      height: 28px;
      border-radius: 7px;
      color: #fff;
      display: grid;
      place-items: center;
      background: linear-gradient(135deg, var(--brand), var(--brand2));
      font-size: 15px;
    }

    .path {
      max-width: min(48vw, 620px);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--muted);
      font-size: 13px;
    }

    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 36px auto;
      display: grid;
      gap: 18px;
    }

    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
      align-items: center;
      gap: 22px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .input-panel {
      padding: 28px;
    }

    .hero-copy {
      padding: 18px 10px;
    }

    h1 {
      margin: 0 0 14px;
      font-size: clamp(30px, 5vw, 42px);
      line-height: 1.12;
      color: var(--brand);
      letter-spacing: 0;
    }

    .hero-copy p {
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.75;
    }

    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      color: var(--brand);
      font-weight: 700;
      font-size: 14px;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    textarea {
      width: 100%;
      min-height: 92px;
      resize: vertical;
      border: 2px solid #ffd9e7;
      border-radius: 8px;
      padding: 14px 16px;
      font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      outline: none;
      background: #fff;
    }

    textarea:focus, input:focus {
      border-color: var(--brand);
      box-shadow: 0 0 0 3px rgba(240, 68, 123, 0.12);
    }

    input {
      min-width: 0;
      height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 12px;
      font-size: 14px;
      outline: none;
      background: #fff;
    }

    .toolbar, .result-actions, .media-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }

    .toolbar { margin-top: 18px; }

    button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 16px;
      display: inline-flex;
      gap: 8px;
      align-items: center;
      justify-content: center;
      background: #fff;
      color: var(--text);
      font-weight: 700;
      cursor: pointer;
    }

    button.primary {
      min-width: 190px;
      border-color: transparent;
      background: linear-gradient(90deg, var(--brand), var(--brand2));
      color: #fff;
    }

    button.primary:hover { filter: brightness(0.96); }
    button:disabled { opacity: 0.55; cursor: not-allowed; }

    .icon {
      width: 17px;
      height: 17px;
      flex: 0 0 auto;
    }

    .status {
      margin-left: auto;
      color: var(--muted);
      font-size: 13px;
    }

    .results {
      display: grid;
      gap: 16px;
    }

    .note-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .note-body {
      padding: 24px 28px;
      display: grid;
      gap: 16px;
    }

    .note-title {
      margin: 0;
      font-size: 22px;
      line-height: 1.35;
      letter-spacing: 0;
    }

    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 9px;
      color: var(--muted);
      background: var(--soft);
      font-size: 12px;
      font-weight: 700;
    }

    .description {
      margin: 0;
      color: #202936;
      white-space: pre-wrap;
      line-height: 1.75;
      font-size: 16px;
      overflow: hidden;
    }

    .description.collapsed {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;
    }

    .description-toggle {
      min-height: 28px;
      width: fit-content;
      border: 0;
      padding: 0;
      background: transparent;
      color: var(--brand-dark);
      font-size: 14px;
      font-weight: 800;
    }

    .description-toggle[hidden] {
      display: none;
    }

    .section-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 8px;
      border-top: 1px solid var(--line);
      padding-top: 16px;
      font-weight: 800;
    }

    .media-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 12px;
    }

    .media-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
    }

    .thumb {
      aspect-ratio: 4 / 3;
      width: 100%;
      display: grid;
      place-items: center;
      background: #f8eaf0;
      overflow: hidden;
    }

    .thumb img, .thumb video {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }

    .video-fallback {
      color: var(--brand);
      font-weight: 800;
    }

    .media-info {
      padding: 10px;
      display: grid;
      gap: 8px;
    }

    label.check {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      font-weight: 750;
    }

    .empty {
      padding: 22px;
      color: var(--muted);
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.65);
    }

    .log {
      min-height: 120px;
      max-height: 260px;
      overflow: auto;
      margin: 0;
      padding: 14px;
      background: #111827;
      color: #e5e7eb;
      font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      white-space: pre-wrap;
    }

    @media (max-width: 900px) {
      .topbar { padding: 0 14px; }
      .path { display: none; }
      main { width: calc(100vw - 20px); margin-top: 14px; }
      .hero { grid-template-columns: 1fr; }
      .hero-copy { padding: 0; }
      .input-panel, .note-body { padding: 16px; }
      .status { width: 100%; margin-left: 0; }
      button.primary { width: 100%; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand"><span class="mark">书</span><span>小红书采集</span></div>
    <div class="path" id="outputPath"></div>
  </header>

  <main>
    <section class="hero">
      <div class="panel input-panel">
        <textarea id="shareText" spellcheck="false" placeholder="复制小红书链接或整段口令到这里"></textarea>
        <div class="toolbar">
          <button class="primary" id="parseBtn" title="开始解析">开始解析</button>
          <button id="pasteBtn" title="从剪贴板粘贴">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 4h8"/><path d="M9 2h6v4H9z"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>
            粘贴
          </button>
          <button id="clearBtn" title="清空">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 16h10l1-16"/></svg>
            清空
          </button>
          <button id="openBtn" title="打开下载文件夹">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7h6l2 2h10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
            打开下载文件夹
          </button>
          <span class="status" id="status">就绪</span>
        </div>
      </div>
      <div class="hero-copy">
        <h1>小红书内容解析下载</h1>
        <p>粘贴链接或分享口令，先解析出标题、正文和图片/视频，再选择全部或部分内容下载到系统下载文件夹。</p>
        <div class="badges">
          <span class="badge">✓ 无需第三方站点</span>
          <span class="badge">✓ 支持图文/视频</span>
          <span class="badge">✓ 可选部分下载</span>
        </div>
      </div>
    </section>

    <section class="results" id="results">
      <div class="empty">解析后的笔记内容会显示在这里</div>
    </section>

    <section class="panel">
      <pre class="log" id="log"></pre>
    </section>
  </main>

  <script>
    const shareText = document.getElementById('shareText');
    const parseBtn = document.getElementById('parseBtn');
    const resultsBox = document.getElementById('results');
    const log = document.getElementById('log');
    const statusEl = document.getElementById('status');
    const outputPath = document.getElementById('outputPath');
    let parsedNotes = [];
    let pollTimer = null;

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[ch]));
    }

    async function postJson(path, data = {}) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '请求失败');
      return payload;
    }

    function setStatus(text) {
      statusEl.textContent = text;
    }

    function appendLog(lines) {
      const next = Array.isArray(lines) ? lines : [lines];
      const filtered = next.filter(Boolean);
      if (filtered.length === 0) return;
      log.textContent = [log.textContent, ...filtered].filter(Boolean).join('\n');
      log.scrollTop = log.scrollHeight;
    }

    function mediaProxyUrl(url) {
      return `/api/media?url=${encodeURIComponent(url)}`;
    }

    function imagePreviewUrl(url) {
      try {
        const parsed = new URL(url);
        if (parsed.hostname.endsWith('xhscdn.com')) {
          return mediaProxyUrl(`https://ci.xiaohongshu.com${parsed.pathname}?imageView2/format/jpeg`);
        }
      } catch (_error) {
        return mediaProxyUrl(url);
      }
      return mediaProxyUrl(url);
    }

    function mediaMarkup(noteIndex, note) {
      if (!note.media || note.media.length === 0) {
        return '<div class="empty">没有获取到图片或视频地址</div>';
      }
      return `<div class="media-grid">${note.media.map(media => {
        const mediaId = `note-${noteIndex}-media-${media.index}`;
        const isVideo = media.kind === 'video';
        const thumb = isVideo
          ? `<video src="${escapeHtml(mediaProxyUrl(media.url))}" muted playsinline preload="metadata"></video>`
          : `<img src="${escapeHtml(imagePreviewUrl(media.url))}" alt="图片 ${media.index}" loading="lazy" />`;
        return `
          <article class="media-card">
            <div class="thumb">${thumb || '<span class="video-fallback">视频</span>'}</div>
            <div class="media-info">
              <label class="check">
                <input type="checkbox" class="media-check" data-note="${noteIndex}" value="${media.index}" checked />
                ${isVideo ? '视频' : '图片'} ${media.index}
              </label>
              ${media.liveUrl ? '<span class="pill">Live 图</span>' : ''}
            </div>
          </article>
        `;
      }).join('')}</div>`;
    }

    function renderNotes(notes) {
      parsedNotes = notes || [];
      if (parsedNotes.length === 0) {
        resultsBox.innerHTML = '<div class="empty">解析后的笔记内容会显示在这里</div>';
        return;
      }
      resultsBox.innerHTML = parsedNotes.map((note, noteIndex) => `
        <article class="note-card" data-note-card="${noteIndex}">
          <div class="note-body">
            <h2 class="note-title">${escapeHtml(note.title)}</h2>
            <div class="meta">
              <span class="pill">${escapeHtml(note.type)}</span>
              ${note.author ? `<span class="pill">作者：${escapeHtml(note.author)}</span>` : ''}
              ${note.publishedAt ? `<span class="pill">发布：${escapeHtml(note.publishedAt)}</span>` : ''}
              <span class="pill">${note.media.length} 个媒体</span>
            </div>
            <p class="description collapsed" id="description-${noteIndex}">${escapeHtml(note.description || '无正文')}</p>
            <button class="description-toggle" data-action="toggle-description" data-note="${noteIndex}" hidden>查看全文 ▾</button>
            <div class="section-title">
              <span>媒体内容</span>
              <div class="media-actions">
                <button data-action="select-all" data-note="${noteIndex}">全选</button>
                <button data-action="invert" data-note="${noteIndex}">反选</button>
                <button data-action="download-selected" data-note="${noteIndex}">下载所选</button>
                <button class="primary" data-action="download-all" data-note="${noteIndex}">下载全部</button>
              </div>
            </div>
            ${mediaMarkup(noteIndex, note)}
          </div>
        </article>
      `).join('');
      requestAnimationFrame(updateDescriptionToggles);
    }

    function updateDescriptionToggles() {
      parsedNotes.forEach((_note, noteIndex) => {
        const description = document.getElementById(`description-${noteIndex}`);
        const toggle = document.querySelector(`.description-toggle[data-note="${noteIndex}"]`);
        if (!description || !toggle) return;
        const wasCollapsed = description.classList.contains('collapsed');
        description.classList.add('collapsed');
        toggle.hidden = description.scrollHeight <= description.clientHeight + 1;
        if (!wasCollapsed) {
          description.classList.remove('collapsed');
          toggle.textContent = '收起 ▴';
        }
      });
    }

    function selectedIndexes(noteIndex) {
      return Array.from(document.querySelectorAll(`.media-check[data-note="${noteIndex}"]:checked`))
        .map(input => Number(input.value))
        .filter(Number.isFinite);
    }

    async function parseContent() {
      const text = shareText.value.trim();
      if (!text) {
        setStatus('请先粘贴链接或口令');
        return;
      }
      setStatus('解析中');
      parseBtn.disabled = true;
      log.textContent = '';
      try {
        const payload = await postJson('/api/parse', { text });
        renderNotes(payload.notes);
        appendLog(payload.logs || [`解析完成：${payload.notes.length} 条笔记`]);
        setStatus('解析完成');
      } catch (error) {
        setStatus(error.message);
        appendLog(`解析失败：${error.message}`);
      } finally {
        parseBtn.disabled = false;
      }
    }

    async function startDownload(noteIndex, downloadAll) {
      const note = parsedNotes[noteIndex];
      if (!note) return;
      const indexes = downloadAll ? [] : selectedIndexes(noteIndex);
      if (!downloadAll && indexes.length === 0) {
        setStatus('请先选择要下载的内容');
        return;
      }
      setStatus('启动下载');
      appendLog(downloadAll ? `下载全部：${note.title}` : `下载所选：${indexes.join(', ')}`);
      const payload = await postJson('/api/download-selected', {
        url: note.sourceUrl,
        indexes,
        downloadAll
      });
      await pollJob(payload.jobId);
      clearInterval(pollTimer);
      pollTimer = setInterval(() => pollJob(payload.jobId), 1000);
    }

    function renderJob(job) {
      log.textContent = (job.logs || []).join('\n');
      log.scrollTop = log.scrollHeight;
      const label = job.status === 'running' ? '下载中' : job.status === 'done' ? '下载完成' : job.status === 'failed' ? '下载失败' : '排队';
      setStatus(label);
      if (job.status === 'done' || job.status === 'failed') {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function pollJob(jobId) {
      const response = await fetch(`/api/jobs/${jobId}`);
      const job = await response.json();
      renderJob(job);
    }

    resultsBox.addEventListener('click', async event => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const noteIndex = Number(button.dataset.note);
      const checks = Array.from(document.querySelectorAll(`.media-check[data-note="${noteIndex}"]`));
      if (button.dataset.action === 'select-all') {
        checks.forEach(input => input.checked = true);
      } else if (button.dataset.action === 'invert') {
        checks.forEach(input => input.checked = !input.checked);
      } else if (button.dataset.action === 'download-selected') {
        await startDownload(noteIndex, false);
      } else if (button.dataset.action === 'download-all') {
        checks.forEach(input => input.checked = true);
        await startDownload(noteIndex, true);
      } else if (button.dataset.action === 'toggle-description') {
        const description = document.getElementById(`description-${noteIndex}`);
        if (!description) return;
        const collapsed = description.classList.toggle('collapsed');
        button.textContent = collapsed ? '查看全文 ▾' : '收起 ▴';
      }
    });

    parseBtn.addEventListener('click', parseContent);
    shareText.addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        parseContent();
      }
    });

    document.getElementById('pasteBtn').addEventListener('click', async () => {
      const text = await navigator.clipboard.readText();
      shareText.value = text;
      setStatus('已粘贴');
    });

    document.getElementById('clearBtn').addEventListener('click', () => {
      shareText.value = '';
      log.textContent = '';
      parsedNotes = [];
      setStatus('就绪');
      renderNotes([]);
    });

    document.getElementById('openBtn').addEventListener('click', async () => {
      await postJson('/api/open-output');
    });

    window.addEventListener('DOMContentLoaded', async () => {
      const config = await fetch('/api/config').then(r => r.json());
      outputPath.textContent = config.outputDir;
    });
  </script>
</body>
</html>
"""


class GuiHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/config":
            json_response(
                self,
                {
                    "root": str(ROOT_DIR),
                    "appDir": str(APP_DIR),
                    "outputDir": str(OUTPUT_DIR),
                },
            )
            return

        if parsed.path == "/api/media":
            query = urllib.parse.parse_qs(parsed.query)
            url = query.get("url", [""])[0]
            if not url:
                json_response(self, {"error": "缺少媒体地址"}, 400)
                return
            media_response(self, url)
            return

        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                json_response(self, {"error": "任务不存在"}, 404)
                return
            json_response(self, job)
            return

        json_response(self, {"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            data = read_json(self)
        except json.JSONDecodeError:
            json_response(self, {"error": "JSON 格式错误"}, 400)
            return

        if parsed.path == "/api/extract":
            json_response(self, {"urls": extract_urls(str(data.get("text", "")))})
            return

        if parsed.path == "/api/parse":
            try:
                result = run_worker(
                    "parse",
                    {
                        "text": str(data.get("text", "")),
                        "cookie": str(data.get("cookie", "")),
                    },
                )
            except Exception as error:
                json_response(self, {"error": str(error)}, 400)
                return
            json_response(self, result)
            return

        if parsed.path == "/api/open-output":
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            open_path(OUTPUT_DIR)
            json_response(self, {"ok": True})
            return

        if parsed.path == "/api/download-selected":
            url = str(data.get("url", ""))
            if not url:
                json_response(self, {"error": "缺少下载链接"}, 400)
                return
            job_id = str(int(time.time() * 1000))
            payload = {
                "url": url,
                "cookie": str(data.get("cookie", "")),
                "indexes": data.get("indexes", []),
                "downloadAll": bool(data.get("downloadAll")),
            }
            set_job(job_id, id=job_id, status="queued", logs=[], payload=payload)
            thread = threading.Thread(
                target=run_download_job,
                args=(job_id, payload),
                daemon=True,
            )
            thread.start()
            json_response(self, {"jobId": job_id})
            return

        json_response(self, {"error": "Not found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    if not APP_DIR.exists():
        raise SystemExit(f"Missing directory: {APP_DIR}")

    server = ThreadingHTTPServer((args.host, args.port), GuiHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"XHS GUI running at {url}")
    print(f"Output: {OUTPUT_DIR}")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
