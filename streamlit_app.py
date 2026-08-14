"""Streamlit 入口：生成、展示并下载学习笔记。"""

from __future__ import annotations

from collections.abc import MutableMapping
import re

import streamlit as st
from streamlit.components.v1 import html as render_html

import mindmap
import note_viewer
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


def _compact_viewer_markdown(markdown: str) -> str:
    """缩小查看器中的文档标题，避免双栏里的一级标题挤压正文。"""

    lines: list[str] = []
    in_code_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code_fence = not in_code_fence
        if not in_code_fence:
            matched = re.match(r"^(#{1,4})(\s+.*)$", line)
            if matched:
                line = "#" * min(6, len(matched.group(1)) + 2) + matched.group(2)
        lines.append(line)
    return "\n".join(lines)


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


def _sync_template_section_state(
    state: MutableMapping[str, object],
    *,
    template_key: str,
    default_section_keys: tuple[str, ...],
    section_state_key: str,
    template_state_key: str,
    force_reset: bool = False,
) -> None:
    """在控件重建或模板变化时恢复章节默认值。"""

    if (
        force_reset
        or state.get(template_state_key) != template_key
        or section_state_key not in state
    ):
        state[section_state_key] = default_section_keys
        state[template_state_key] = template_key


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


def _render_viewer_download(content: note_viewer.ViewerContent, *, suffix: str) -> None:
    """显示查看器内容的独立下载按钮。"""

    st.download_button(
        f"下载{content.label}",
        data=content.text,
        file_name=content.filename,
        mime=content.download_mime,
        key=f"download_viewer_{suffix}_{content.content_id}",
    )
    if content.kind == "transcript" and content.srt_text is not None and content.srt_filename is not None:
        st.download_button(
            "下载 SRT",
            data=content.srt_text,
            file_name=content.srt_filename,
            mime="application/x-subrip",
            key=f"download_viewer_srt_{suffix}_{content.content_id}",
        )


def _render_viewer_content(
    content: note_viewer.ViewerContent,
    *,
    key: str,
) -> None:
    """渲染一份 Markdown、字幕或思维导图内容。"""

    if content.kind == "transcript":
        st.caption(f"来源：{content.transcript_source or '未知'}")
        st.text(content.text)
    else:
        display_mode = st.radio(
            "显示方式",
            options=("Markdown", "思维导图"),
            horizontal=True,
            key=f"viewer_display_{key}_{content.content_id}",
        )
        if display_mode == "Markdown":
            st.markdown(_compact_viewer_markdown(content.text))
        else:
            render_html(
                mindmap.render_mindmap_html(content.text),
                height=760,
                scrolling=False,
            )
        if content.template_name:
            suffix = "；".join(content.section_keys) if content.section_keys else "默认章节"
            st.caption(f"模板：{content.template_name}；章节：{suffix}")
    _render_viewer_download(content, suffix=key)


def render_note_viewer(
    contents: tuple[note_viewer.ViewerContent, ...],
    *,
    key_prefix: str,
) -> None:
    """显示单篇或双篇笔记查看器，不触发新的模型调用。"""

    if not contents:
        return
    contents = note_viewer.validate_viewer_contents(contents)
    st.markdown("#### 笔记查看器")
    labels = {content.content_id: content.label for content in contents}
    ids = tuple(labels)
    view_mode = st.radio(
        "查看方式",
        options=("单篇阅读", "双篇对比"),
        horizontal=True,
        key=f"viewer_mode_{key_prefix}",
    )
    if view_mode == "单篇阅读":
        selected_id = st.selectbox(
            "选择内容",
            options=ids,
            format_func=labels.__getitem__,
            key=f"viewer_single_{key_prefix}",
        )
        _render_viewer_content(
            note_viewer.get_viewer_content(contents, selected_id),
            key=f"{key_prefix}_single",
        )
        return

    left_id = st.selectbox(
        "左侧内容",
        options=ids,
        format_func=labels.__getitem__,
        key=f"viewer_left_{key_prefix}",
    )
    right_options = tuple(content_id for content_id in ids if content_id != left_id)
    if not right_options:
        st.info("至少需要两份不同内容才能进行双篇对比。")
        return
    right_id = st.selectbox(
        "右侧内容",
        options=right_options,
        format_func=labels.__getitem__,
        key=f"viewer_right_{key_prefix}",
    )
    left, right = note_viewer.select_viewer_pair(contents, left_id, right_id)
    left_column, right_column = st.columns(2)
    with left_column:
        st.markdown(f"##### {left.label}")
        _render_viewer_content(left, key=f"{key_prefix}_left")
    with right_column:
        st.markdown(f"##### {right.label}")
        _render_viewer_content(right, key=f"{key_prefix}_right")


def render_generation_result(result: web_service.WebGenerationResult) -> None:
    """展示单 P、多 P 以及合集总结的结构化处理结果。"""

    st.markdown("### 生成结果")

    if not result.is_multi_part:
        part = result.parts[0]
        if part.succeeded:
            st.success(f"P{part.page_number} {part.title}：总体笔记生成成功")
            if result.viewer_contents:
                render_note_viewer(result.viewer_contents, key_prefix="single")
            else:
                st.markdown(part.markdown)
                _render_download_button(part, key="download_single_part")
        elif part.error_type == "no_subtitle":
            st.warning(f"P{part.page_number} {part.title}：{part.error}")
        else:
            st.error(f"P{part.page_number} {part.title}：生成失败：{part.error}")

        if part.succeeded and result.segmented_notes_requested and result.segmented_markdown is None:
            st.error(result.segmented_error or "分段笔记生成失败。")
            st.caption("总体笔记已保留，仍可查看和下载。")
        if part.succeeded and result.secondary_template is not None and result.secondary_markdown is None:
            st.error(result.secondary_error or "第二份总体笔记生成失败。")
            st.caption("第一份总体笔记已保留，仍可查看和下载。")
        return

    st.write(
        f"成功 {result.succeeded_count} P　｜　"
        f"无字幕 {result.no_subtitle_count} P　｜　"
        f"生成失败 {result.failed_count} P"
    )

    successful_parts = tuple(part for part in result.parts if part.succeeded)
    render_note_viewer(result.viewer_contents, key_prefix="multi")
    no_subtitle_parts = tuple(
        part for part in result.parts if part.error_type == "no_subtitle"
    )
    failed_parts = tuple(
        part for part in result.parts if part.error_type == "processing_failed"
    )

    if not result.viewer_contents:
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
        if not result.viewer_contents:
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
    with st.container(border=True):
        part_selection = None
        generate_collection_summary = False
        generate_segmented_notes = False
        generate_secondary_notes = False
        include_transcript_in_viewer = True
        selected_template = None
        secondary_template = None
        secondary_section_keys = None
        template_error = None
        secondary_template_error = None
        note_mode = None

        estimated_parts = video_info.parts
        estimate_available = True
        if video_info.is_multi_part:
            part_selection = st.text_input(
                "选择分P",
                placeholder="例如：1；1,3,5；或 1,3,5-8；留空处理全部",
                help="选择规则与命令行 --parts 完全一致。",
            )
            try:
                estimated_parts = web_service.select_parts(video_info, part_selection)
            except web_service.PartSelectionError:
                estimate_available = False

        single_part_mode = not video_info.is_multi_part or (
            estimate_available and len(estimated_parts) == 1
        )
        if single_part_mode:
            if video_info.is_multi_part:
                selected_part = estimated_parts[0]
                st.info(
                    f"已单选 P{selected_part.page_number}，本次使用完整单 P 模式。"
                )
            templates = web_service.get_note_template_options_for_web()
            template = st.selectbox(
                "总体笔记预设",
                options=templates,
                format_func=_format_note_template,
            )
            default_template = web_service.resolve_note_template(template.key)
            reset_template_requested = st.session_state.pop(
                "_reset_summary_template", False
            )
            _sync_template_section_state(
                st.session_state,
                template_key=template.key,
                default_section_keys=default_template.section_keys,
                section_state_key="summary_section_keys",
                template_state_key="summary_template_key",
                force_reset=reset_template_requested,
            )
            section_keys = st.multiselect(
                "总体笔记章节（可增删）",
                options=tuple(web_service.NOTE_SECTION_LIBRARY),
                key="summary_section_keys",
                format_func=lambda key: web_service.NOTE_SECTION_LIBRARY[key].title,
                help="只能选择系统章节库中的章节；输出顺序按系统顺序排列。",
            )
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
                secondary_default = web_service.resolve_note_template(
                    secondary_choice.key
                )
                _sync_template_section_state(
                    st.session_state,
                    template_key=secondary_choice.key,
                    default_section_keys=secondary_default.section_keys,
                    section_state_key="secondary_summary_sections",
                    template_state_key="secondary_template_key",
                )
                secondary_section_keys = st.multiselect(
                    "第二份总体笔记章节（可增删）",
                    options=tuple(web_service.NOTE_SECTION_LIBRARY),
                    key="secondary_summary_sections",
                    format_func=lambda key: web_service.NOTE_SECTION_LIBRARY[key].title,
                )
                try:
                    secondary_template = web_service.resolve_note_template(
                        secondary_choice.key,
                        section_keys=secondary_section_keys,
                    )
                except web_service.NoteModeError as error:
                    secondary_template_error = str(error)
                    st.error(f"第二份总体笔记：{secondary_template_error}")
                else:
                    if secondary_template.customized:
                        st.caption("第二份总体笔记章节配置：已自定义")
            generate_segmented_notes = st.checkbox(
                "生成按内容语义分段的笔记",
                value=False,
                help="总体笔记仍会生成；另用一次调用规划分段、一次调用生成全部分段内容。",
            )
            include_transcript_in_viewer = st.checkbox(
                "在查看器中显示和下载原始字幕",
                value=True,
                help="不增加 DeepSeek 调用；取消后仍会获取字幕用于生成笔记，但不在查看器中展示或下载。",
            )
        else:
            generate_collection_summary = st.checkbox(
                "生成额外的合集总结",
                value=False,
            )
            include_transcript_in_viewer = st.checkbox(
                "在查看器中显示和下载原始字幕",
                value=True,
                help="不增加 DeepSeek 调用；取消后仍会获取字幕用于生成笔记，但不在查看器中展示或下载。",
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

        if estimate_available:
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
        reset_submitted = st.button("恢复模板默认设置") if single_part_mode else False
        generate_submitted = st.button("生成学习笔记", type="primary")

    if reset_submitted:
        # 下一次运行时，在 multiselect 实例化前重置，避免 Streamlit 禁止
        # 在控件创建后直接修改同名 session_state。
        st.session_state["_reset_summary_template"] = True
        st.rerun()
    if generate_submitted:
        if (
            single_part_mode
            and (template_error is not None or secondary_template_error is not None)
        ):
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
                include_transcript_in_viewer=include_transcript_in_viewer,
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
    layout="wide",
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
