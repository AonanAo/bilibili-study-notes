"""命令行入口：读取 B 站字幕并生成学习笔记。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bilibili import (
    BilibiliError,
    extract_bvid,
    fetch_video_subtitle,
    get_video_parts,
)
from llm import LLMError, generate_study_notes
from pipeline import process_multi_part_video
from selection import PartSelectionError, select_video_parts


OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


def save_study_notes(markdown: str, bvid: str, output_dir: Path = OUTPUT_DIR) -> Path:
    """将 Markdown 学习笔记保存到 outputs 目录。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{bvid}_study_notes.md"
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="根据 B 站视频字幕生成 AI 学习笔记")
    parser.add_argument(
        "url",
        nargs="?",
        help="B 站视频链接或 BV 号",
    )
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="从已登录 B 站的浏览器读取 Cookie，例如 chrome、edge 或 firefox",
    )
    parser.add_argument(
        "--parts",
        metavar="SELECTION",
        help='只处理指定分 P，例如 "1"、"1,3,5" 或 "1,3,5-8"',
    )
    return parser


def main() -> int:
    """执行程序，并返回适合命令行的退出码。"""

    args = build_parser().parse_args()
    # 没有传命令行参数时，允许初学者直接运行后粘贴链接。
    interactive_mode = args.url is None
    video_url = args.url or input("请输入 B 站视频链接或 BV 号：").strip()

    try:
        # 先单独解析并显示 BV 号，方便排查链接解析问题。
        bvid = extract_bvid(video_url)
        print(f"调试：实际提取到的 BV 号：{bvid}")
        collection = get_video_parts(
            video_url,
            cookies_from_browser=args.cookies_from_browser,
        )
    except BilibiliError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

    if collection.is_multi_part:
        print(f"课程：{collection.title}")
        print(f"共 {len(collection.parts)} P：")
        for part in collection.parts:
            print(f"P{part.page_number} {part.title}")

        selection = args.parts
        if interactive_mode and selection is None:
            selection = input(
                '请选择需要生成笔记的分 P（例如 1,3,5-8，直接回车处理全部）：'
            ).strip()

        try:
            selected_parts = select_video_parts(collection.parts, selection)
        except PartSelectionError as error:
            print(f"错误：{error}", file=sys.stderr)
            return 1

        selected_labels = ", ".join(
            f"P{part.page_number}" for part in selected_parts
        )
        print(f"本次处理：{selected_labels}")
        try:
            report = process_multi_part_video(
                collection,
                output_root=OUTPUT_DIR,
                selected_parts=selected_parts,
                cookies_from_browser=args.cookies_from_browser,
                on_event=print,
            )
        except OSError as error:
            print(f"错误：创建多 P 输出目录失败：{error}", file=sys.stderr)
            return 1

        print(
            f"多 P 处理完成：成功 {report.succeeded_count} P，"
            f"失败 {report.failed_count} P。"
        )
        if report.failed_count:
            print("失败分 P 已记录在课程总结的“处理状态”中。")
        if report.summary_path is None:
            print(f"错误：{report.summary_error}", file=sys.stderr)
            return 1
        print(f"多 P 学习笔记目录：{report.output_dir}")
        return 0

    # 单 P 视频继续走原有流程，输出文件名和使用方式都不变。
    try:
        result = fetch_video_subtitle(
            video_url,
            cookies_from_browser=args.cookies_from_browser,
        )
    except BilibiliError as error:
        # 错误写到标准错误流，便于脚本调用时识别失败。
        print(f"错误：{error}", file=sys.stderr)
        return 1

    print(f"视频标题：{result.title}")
    print(f"字幕获取成功：{len(result.subtitle_text)} 个字符")
    print("正在调用 DeepSeek 生成学习笔记……")

    try:
        notes = generate_study_notes(
            result.subtitle_text,
            video_title=result.title,
            video_description=result.description,
        )
        output_path = save_study_notes(notes, result.bvid)
    except LLMError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"错误：保存学习笔记失败：{error}", file=sys.stderr)
        return 1

    print(f"学习笔记已保存：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
