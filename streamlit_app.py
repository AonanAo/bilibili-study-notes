"""Streamlit 入口：生成、展示并下载学习笔记。"""

from __future__ import annotations

import re

import streamlit as st

import web_service


BROWSER_OPTIONS = (None, "chrome", "edge", "firefox", "safari")
BROWSER_LABELS = {
    None: "不使用 Cookie",
    "chrome": "Chrome",
    "edge": "Edge",
    "firefox": "Firefox",
    "safari": "Safari",
}


def _format_description_markdown(description: str) -> str:
    """保留简介换行，同时避免普通文本被 Markdown 识别成标题。"""

    formatted_lines: list[str] = []
    for line in description.splitlines():
        # B 站简介常用连续等号或横线作分隔符，Markdown 会把上一行放大成标题。
        if re.fullmatch(r"\s*(=+|-+|_+|\*+)\s*", line):
            leading_spaces = line[: len(line) - len(line.lstrip())]
            line = f"{leading_spaces}\\{line.lstrip()}"
        # 简介里的井号是普通文本，不应成为页面标题。
        line = re.sub(r"^(\s*)(#{1,6})(\s+)", r"\1\\\2\3", line)
        formatted_lines.append(line)
    return "  \n".join(formatted_lines)


def render_video_info(video_info: web_service.VideoCollection) -> None:
    """展示一个视频或课程的基本信息和全部分 P。"""

    st.subheader(video_info.title)
    st.write(f"**BV号：** {video_info.bvid}")

    if video_info.description:
        st.markdown("#### 视频简介")
        st.markdown(_format_description_markdown(video_info.description))
    else:
        st.caption("该视频没有提供简介。")

    st.markdown(f"#### 分P列表（共 {len(video_info.parts)} P）")
    for part in video_info.parts:
        st.write(f"**P{part.page_number}**　{part.title}")


def _format_note_mode(mode: web_service.NoteMode | None) -> str:
    """生成笔记模式下拉框中的中文标签。"""

    if mode is None:
        return "默认学习笔记（兼容旧版本）"
    return f"{mode.name}（{mode.key}）"


def _format_browser(browser: str | None) -> str:
    """显示网页 Cookie 浏览器选择的中文标签。"""

    return BROWSER_LABELS[browser]


def _render_download_button(
    part: web_service.WebPartResult,
    *,
    key: str,
) -> None:
    """为已经读取成功的 Markdown 笔记显示下载按钮。"""

    if part.markdown is None or part.filename is None:
        return
    st.download_button(
        "下载 Markdown",
        data=part.markdown,
        file_name=part.filename,
        mime="text/markdown",
        key=key,
    )


def render_generation_result(result: web_service.WebGenerationResult) -> None:
    """展示单 P、多 P 以及合集总结的结构化处理结果。"""

    st.markdown("### 生成结果")

    if not result.is_multi_part:
        part = result.parts[0]
        if part.succeeded:
            st.success(f"P{part.page_number} {part.title}：生成成功")
            st.markdown(part.markdown)
            _render_download_button(part, key="download_single_part")
        elif part.error_type == "no_subtitle":
            st.warning(f"P{part.page_number} {part.title}：{part.error}")
        else:
            st.error(f"P{part.page_number} {part.title}：生成失败：{part.error}")
        return

    st.write(
        f"成功 {result.succeeded_count} P　｜　"
        f"无字幕 {result.no_subtitle_count} P　｜　"
        f"生成失败 {result.failed_count} P"
    )

    successful_parts = tuple(part for part in result.parts if part.succeeded)
    no_subtitle_parts = tuple(
        part for part in result.parts if part.error_type == "no_subtitle"
    )
    failed_parts = tuple(
        part for part in result.parts if part.error_type == "processing_failed"
    )

    st.markdown("#### 成功分P")
    if not successful_parts:
        st.caption("本次没有成功生成的分P笔记。")
    for part in successful_parts:
        with st.expander(f"P{part.page_number} {part.title}"):
            st.markdown(part.markdown)
            _render_download_button(
                part,
                key=f"download_part_{part.page_number}",
            )

    st.markdown("#### 无字幕分P")
    if not no_subtitle_parts:
        st.caption("本次没有无字幕分P。")
    for part in no_subtitle_parts:
        st.warning(f"P{part.page_number} {part.title}：{part.error}")

    st.markdown("#### 生成失败分P")
    if not failed_parts:
        st.caption("本次没有生成失败分P。")
    for part in failed_parts:
        st.error(f"P{part.page_number} {part.title}：{part.error}")

    st.markdown("#### 合集总结")
    if not result.collection_summary_requested:
        st.info("本次已按设置跳过合集总结")
    elif result.summary_markdown is not None:
        st.success("summary.md 生成成功")
        st.markdown(result.summary_markdown)
        st.download_button(
            "下载 summary.md",
            data=result.summary_markdown,
            file_name=result.summary_filename or "summary.md",
            mime="text/markdown",
            key="download_summary",
        )
    elif result.summary_error is not None:
        st.error(f"summary.md 生成失败或无法读取：{result.summary_error}")
        if successful_parts:
            st.caption("成功生成的分P笔记已保留，仍可查看和下载。")
    else:
        st.caption("本次没有生成合集总结。")


def render_generation_form(video_info: web_service.VideoCollection) -> None:
    """收集分 P、笔记模式和额外要求，并交给网页适配层。"""

    st.markdown("### 生成学习笔记")
    with st.form("notes_generation_form"):
        part_selection = None
        generate_collection_summary = False
        if video_info.is_multi_part:
            part_selection = st.text_input(
                "选择分P",
                placeholder="例如：1；1,3,5；或 1,3,5-8；留空处理全部",
                help="选择规则与命令行 --parts 完全一致。",
            )
            generate_collection_summary = st.checkbox(
                "生成额外的合集总结",
                value=False,
            )

        note_mode = st.selectbox(
            "笔记模式",
            options=(None, *web_service.get_note_mode_options()),
            format_func=_format_note_mode,
        )
        extra_instruction = st.text_area(
            "额外学习要求（可选）",
            placeholder="例如：请重点解释代码实现和设计原因。",
        )
        generate_submitted = st.form_submit_button("生成学习笔记", type="primary")

    if generate_submitted:
        st.session_state.generation_result = None
        generation_status = st.status("正在准备生成学习笔记……", expanded=True)

        def show_event(message: str) -> None:
            # 页面只显示 pipeline 发来的文字，不解析或依赖事件格式。
            generation_status.write(message)

        try:
            selected_parts = web_service.select_parts(video_info, part_selection)
            st.session_state.generation_result = web_service.generate_notes(
                video_info,
                selected_parts=selected_parts,
                note_mode=note_mode.key if note_mode is not None else None,
                extra_instruction=extra_instruction.strip() or None,
                generate_collection_summary=generate_collection_summary,
                cookies_from_browser=st.session_state.cookies_from_browser,
                on_event=show_event,
            )
        except web_service.PartSelectionError as error:
            generation_status.update(label="分P选择失败", state="error")
            st.error(f"分P选择错误：{error}")
        except (web_service.BilibiliError, web_service.LLMError, OSError) as error:
            generation_status.update(label="生成流程失败", state="error")
            st.error(f"生成失败：{error}")
        else:
            generation_status.update(label="生成流程已完成", state="complete")

    if st.session_state.generation_result is not None:
        render_generation_result(st.session_state.generation_result)


st.set_page_config(
    page_title="B站视频AI学习笔记",
    page_icon="📚",
)

st.title("B站视频AI学习笔记")
st.caption("输入B站视频链接或BV号，选择分P和笔记模式后生成学习笔记。")

if "video_info" not in st.session_state:
    st.session_state.video_info = None
if "generation_result" not in st.session_state:
    st.session_state.generation_result = None
if "cookies_from_browser" not in st.session_state:
    st.session_state.cookies_from_browser = None

with st.form("video_info_form"):
    video_input = st.text_input(
        "B站视频链接或BV号",
        placeholder="例如：https://www.bilibili.com/video/BVxxxx 或 BVxxxx",
    )
    cookies_from_browser = st.selectbox(
        "B站登录浏览器（可选）",
        options=BROWSER_OPTIONS,
        format_func=_format_browser,
        help=(
            "如果视频字幕需要登录，请选择已经登录 B站的浏览器。"
            "Cookie 只由本机 yt-dlp 读取，不会保存或发送给 DeepSeek。"
        ),
    )
    submitted = st.form_submit_button("解析视频", type="primary")

if submitted:
    # 每次开始解析时先清空旧结果，避免失败后仍显示上一个视频。
    st.session_state.video_info = None
    st.session_state.generation_result = None
    st.session_state.cookies_from_browser = cookies_from_browser
    try:
        with st.spinner("正在解析视频信息……"):
            st.session_state.video_info = web_service.load_video_info(
                video_input,
                cookies_from_browser=cookies_from_browser,
            )
    except web_service.BilibiliError as error:
        st.error(f"解析失败：{error}")
    else:
        st.success("视频信息解析成功。")

if st.session_state.video_info is not None:
    render_video_info(st.session_state.video_info)
    render_generation_form(st.session_state.video_info)
