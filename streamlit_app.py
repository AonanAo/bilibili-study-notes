"""v0.2.3.2 Streamlit 入口：选择参数并调用学习笔记生成流程。"""

from __future__ import annotations

import streamlit as st

import web_service


def render_video_info(video_info: web_service.VideoCollection) -> None:
    """展示一个视频或课程的基本信息和全部分 P。"""

    st.subheader(video_info.title)
    st.write(f"**BV号：** {video_info.bvid}")

    if video_info.description:
        st.markdown("#### 视频简介")
        st.write(video_info.description)
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


def render_generation_form(video_info: web_service.VideoCollection) -> None:
    """收集分 P、笔记模式和额外要求，并交给网页适配层。"""

    st.markdown("### 生成学习笔记")
    with st.form("notes_generation_form"):
        part_selection = None
        if video_info.is_multi_part:
            part_selection = st.text_input(
                "选择分P",
                placeholder="例如：1；1,3,5；或 1,3,5-8；留空处理全部",
                help="选择规则与命令行 --parts 完全一致。",
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

    if not generate_submitted:
        return

    try:
        selected_parts = web_service.select_parts(video_info, part_selection)
        st.session_state.generation_result = web_service.generate_notes(
            video_info,
            selected_parts=selected_parts,
            note_mode=note_mode.key if note_mode is not None else None,
            extra_instruction=extra_instruction.strip() or None,
        )
    except web_service.PartSelectionError as error:
        st.error(f"分P选择错误：{error}")
    except (web_service.BilibiliError, web_service.LLMError, OSError) as error:
        st.error(f"生成失败：{error}")
    else:
        st.success("学习笔记生成流程已执行完毕，请在 outputs 目录查看结果。")
        st.caption("Markdown 在线展示、详细状态和下载功能将在 v0.2.3.3 加入。")


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

with st.form("video_info_form"):
    video_input = st.text_input(
        "B站视频链接或BV号",
        placeholder="例如：https://www.bilibili.com/video/BVxxxx 或 BVxxxx",
    )
    submitted = st.form_submit_button("解析视频", type="primary")

if submitted:
    # 每次开始解析时先清空旧结果，避免失败后仍显示上一个视频。
    st.session_state.video_info = None
    st.session_state.generation_result = None
    try:
        with st.spinner("正在解析视频信息……"):
            st.session_state.video_info = web_service.load_video_info(video_input)
    except web_service.BilibiliError as error:
        st.error(f"解析失败：{error}")
    else:
        st.success("视频信息解析成功。")

if st.session_state.video_info is not None:
    render_video_info(st.session_state.video_info)
    render_generation_form(st.session_state.video_info)
