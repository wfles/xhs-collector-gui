#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "XHS-Downloader"
OUTPUT_ROOT = Path.home() / "Downloads"
OUTPUT_FOLDER = "XHS-Downloads"
URL_PATTERN = re.compile(
    r"(?P<url>https?://(?:www\.)?(?:xhslink\.com|xiaohongshu\.com)"
    r"[^\s，。！？、；;：:\]\[）)(<>\"']+)",
    re.IGNORECASE,
)


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group("url").rstrip(".,)")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def normalize_media(note: dict[str, Any]) -> list[dict[str, Any]]:
    note_type = str(note.get("作品类型") or "")
    urls = list(note.get("下载地址") or [])
    lives = list(note.get("动图地址") or [])

    if note_type == "视频":
        return [
            {
                "index": 1,
                "kind": "video",
                "url": urls[0],
                "liveUrl": None,
            }
        ] if urls else []

    media: list[dict[str, Any]] = []
    for index, url in enumerate(urls, start=1):
        live_url = lives[index - 1] if index - 1 < len(lives) else None
        media.append(
            {
                "index": index,
                "kind": "image",
                "url": url,
                "liveUrl": live_url if live_url and live_url != "NaN" else None,
            }
        )
    return media


def normalize_note(note: dict[str, Any], source_url: str) -> dict[str, Any]:
    return {
        "sourceUrl": source_url,
        "id": note.get("作品ID") or "",
        "title": note.get("作品标题") or "无标题",
        "description": note.get("作品描述") or "",
        "type": note.get("作品类型") or "未知",
        "author": note.get("作者昵称") or "",
        "authorId": note.get("作者ID") or "",
        "publishedAt": note.get("发布时间") or "",
        "updatedAt": note.get("最后更新时间") or "",
        "likedCount": note.get("点赞数量") or "",
        "collectedCount": note.get("收藏数量") or "",
        "commentCount": note.get("评论数量") or "",
        "shareCount": note.get("分享数量") or "",
        "tags": note.get("作品标签") or "",
        "media": normalize_media(note),
        "raw": note,
    }


def build_xhs(cookie: str, *, fast: bool = False):
    sys.path.insert(0, str(APP_DIR))
    from source import XHS

    return XHS(
        work_path=str(OUTPUT_ROOT),
        folder_name=OUTPUT_FOLDER,
        name_format="发布时间 作者昵称 作品标题",
        cookie=cookie or "",
        timeout=8 if fast else 15,
        max_retry=1 if fast else 3,
        record_data=False,
        image_format="AUTO",
        image_download=True,
        video_download=True,
        live_download=True,
        folder_mode=False,
        download_record=False,
        author_archive=False,
        write_mtime=True,
        language="zh_CN",
    )


async def parse(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text") or "")
    cookie = str(payload.get("cookie") or "")
    urls = extract_urls(text)
    if not urls:
        raise ValueError("未识别到小红书链接")

    async with build_xhs(cookie, fast=True) as xhs:
        raw_notes = await xhs.extract(" ".join(urls), download=False)

    notes = [
        normalize_note(note, urls[index] if index < len(urls) else note.get("作品链接", ""))
        for index, note in enumerate(raw_notes)
        if isinstance(note, dict) and note
    ]
    if not notes:
        raise ValueError("解析失败，未获取到笔记内容")
    return {
        "ok": True,
        "urls": urls,
        "notes": notes,
        "outputDir": str(OUTPUT_ROOT / OUTPUT_FOLDER),
    }


async def download(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(payload.get("url") or "")
    cookie = str(payload.get("cookie") or "")
    download_all = bool(payload.get("downloadAll"))
    indexes = payload.get("indexes")
    if not url:
        raise ValueError("缺少下载链接")

    clean_indexes = None
    if not download_all and isinstance(indexes, list):
        clean_indexes = sorted({int(i) for i in indexes if int(i) > 0})
        if not clean_indexes:
            raise ValueError("请先选择要下载的内容")

    async with build_xhs(cookie) as xhs:
        raw_notes = await xhs.extract(url, download=True, index=clean_indexes)

    notes = [
        normalize_note(note, url)
        for note in raw_notes
        if isinstance(note, dict) and note
    ]
    return {
        "ok": True,
        "notes": notes,
        "outputDir": str(OUTPUT_ROOT / OUTPUT_FOLDER),
    }


async def run(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    match action:
        case "parse":
            return await parse(payload)
        case "download":
            return await download(payload)
        case _:
            raise ValueError(f"未知操作：{action}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["parse", "download"])
    args = parser.parse_args()

    try:
        payload = json.loads(sys.stdin.read() or "{}")
        log_buffer = io.StringIO()
        with contextlib.redirect_stdout(log_buffer):
            result = asyncio.run(run(args.action, payload))
        result["logs"] = [
            line for line in log_buffer.getvalue().splitlines() if line.strip()
        ]
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error),
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
