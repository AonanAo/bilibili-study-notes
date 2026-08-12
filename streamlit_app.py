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


def _format_note_template(template: web_service.NoteTemplate) -> str:
    return f"{template.name}（{template.key}）"


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
            st.success(f"P{part.page_number} {part.title}：总体笔记生成成功")
            st.markdown(part.markdown)
            _render_download_button(part, key="download_single_part")
        elif part.error_type == "no_subtitle":
            st.warning(f"P{part.page_number} {part.title}：{part.error}")
        else:
            st.error(f"P{part.page_number} {part.title}：生成失败：{part.error}")

        if part.succeeded and result.segmented_notes_requested:
            st.markdown("#### 分段笔记")
            if result.segmented_markdown is not None:
                st.success("语义分段笔记生成成功")
                st.markdown(result.segmented_markdown)
                st.download_button(
                    "下载分段笔记 Markdown",
                    data=result.segmented_markdown,
                    file_name=(
                        result.segmented_filename or "segmented_notes.md"
                    ),
                    mime="text/markdown",
                    key="download_segmented_notes",
                )
            else:
                st.error(result.segmented_error or "分段笔记生成失败。")
                st.caption("总体笔记已保留，仍可查看和下载。")
        if part.succeeded and result.secondary_template is not None:
            st.markdown("#### 第二份总体笔记")
            if result.secondary_markdown is not None:
                label = result.secondary_template.name
                if result.secondary_template.customized:
                    label += "（已自定义）"
                st.success(f"{label}生成成功")
                st.markdown(result.secondary_markdown)
                st.download_button(
                    "下载第二份总体笔记 Markdown",
                    data=result.secondary_markdown,
                    file_name=result.secondary_filename or "study_notes_B.md",
                    mime="text/markdown",
                    key="download_secondary_notes",
                )
            else:
                st.error(result.secondary_error or "第二份总体笔记生成失败。")
                st.caption("第一份总体笔记已保留，仍可查看和下载。")
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
        generate_segmented_notes = False
        generate_secondary_notes = False
        selected_template = None
        secondary_template = None
        secondary_section_keys = None
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
        else:
            templates = web_service.get_note_template_options_for_web()
            template = st.selectbox(
                "总体笔记预设",
                options=templates,
                format_func=_format_note_template,
            )
            default_template = web_service.resolve_note_template(template.key)
            section_default = st.session_state.pop(
                "summary_section_keys",
                default_template.section_keys,
            )
            section_keys = st.multiselect(
                "总体笔记章节（可增删）",
                options=tuple(web_service.NOTE_SECTION_LIBRARY),
                default=section_default,
                format_func=lambda key: web_service.NOTE_SECTION_LIBRARY[key].title,
                help="只能选择系统章节库中的章节；输出顺序按系统顺序排列。",
            )
            template_error = None
            try:
                selected_template = web_service.resolve_note_template(
                    template.key,
                    section_keys=section_keys,
                )
            except web_service.NoteModeError as error:
                template_error = str(error)
                st.error(template_error)
            else:
                if selected_template.customized:
                    st.caption("当前章节配置：已自定义")
            generate_secondary_notes = st.checkbox(
                "同时生成第二份总体笔记",
                value=False,
                help="第二份笔记单独调用并保存为 _B 文件。",
            )
            if generate_secondary_notes:
                secondary_choice = st.selectbox(
                    "第二份总体笔记预设",
                    options=templates,
                    index=1 if template.key == templates[0].key else 0,
                    format_func=_format_note_template,
                )
                secondary_default = web_service.resolve_note_template(secondary_choice.key)
                secondary_section_keys = st.multiselect(
                    "第二份总体笔记章节（可增删）",
                    options=tuple(web_service.NOTE_SECTION_LIBRARY),
                    default=secondary_default.section_keys,
                    format_func=lambda key: web_service.NOTE_SECTION_LIBRARY[key].title,
                    key="secondary_summary_sections",
                )
            else:
                secondary_choice = None
            generate_segmented_notes = st.checkbox(
                "生成按内容语义分段的笔记",
                value=False,
                help="总体笔记仍会生成；另用一次调用规划分段、一次调用生成全部分段内容。",
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

        estimated_parts = video_info.parts
        estimate_available = True
        if video_info.is_multi_part:
            try:
                estimated_parts = web_service.select_parts(video_info, part_selection)
            except web_service.PartSelectionError:
                estimate_available = False
        if estimate_available:
            if not video_info.is_multi_part and template_error is None:
                if generate_secondary_notes:
                    secondary_template = web_service.resolve_note_template(
                        secondary_choice.key,
                        section_keys=secondary_section_keys,
                    )
            estimated_calls = web_service.estimate_deepseek_calls(
                video_info,
                selected_parts=estimated_parts,
                generate_collection_summary=generate_collection_summary,
                generate_segmented_notes=generate_segmented_notes,
                generate_secondary_notes=generate_secondary_notes,
            )
            st.info(f"预计最多调用 DeepSeek {estimated_calls} 次。")
        else:
            st.info("当前分P选择无效，修正后将显示预计调用次数。")
        reset_submitted = st.form_submit_button("恢复模板默认设置") if not video_info.is_multi_part else False
        generate_submitted = st.form_submit_button("生成学习笔记", type="primary")

    if reset_submitted:
        st.session_state["summary_section_keys"] = default_template.section_keys
        st.rerun()
    if generate_submitted:
        if not video_info.is_multi_part and template_error is not None:
            st.error("请至少保留一个总体笔记章节后再生成。")
            return
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
                generate_segmented_notes=generate_segmented_notes,
                note_template=selected_template,
                secondary_note_template=secondary_template,
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
