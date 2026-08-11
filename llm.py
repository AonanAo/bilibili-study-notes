"""DeepSeek API 调用模块。"""

from __future__ import annotations

import os

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

from prompt import (
    COURSE_SUMMARY_SYSTEM_PROMPT,
    SEGMENT_CONTENT_SYSTEM_PROMPT,
    SEGMENT_PLAN_SYSTEM_PROMPT,
    STUDY_NOTES_SYSTEM_PROMPT,
    NoteModeError,
    build_course_summary_prompt,
    build_segment_content_prompt,
    build_segment_plan_prompt,
    build_study_notes_prompt,
    get_note_mode,
)
from segmentation import (
    AssignedSegment,
    SegmentNoteContent,
    SegmentPlan,
    SegmentationError,
    parse_segment_note_contents,
    parse_segment_plan,
)
from transcript import Transcript


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
MAX_OUTPUT_TOKENS = 8192

REQUIRED_SUMMARY_HEADINGS = (
    "# 视频整体主题",
    "## 核心知识体系",
    "## 各章节关系",
    "## 关键概念",
    "## 学习建议",
)


class LLMError(Exception):
    """DeepSeek 笔记生成错误的基类。"""


class InvalidNoteModeError(LLMError):
    """请求了不存在或尚未开放的笔记模式。"""


class MissingAPIKeyError(LLMError):
    """未配置 DeepSeek API Key。"""


class LLMRequestError(LLMError):
    """DeepSeek API 请求失败。"""


class InvalidLLMResponseError(LLMError):
    """DeepSeek 返回了空内容或格式不完整的笔记。"""


def _clean_markdown(markdown: str) -> str:
    """移除模型偶尔添加的外层 Markdown 代码块。"""

    content = markdown.strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3:
            content = "\n".join(lines[1:-1]).strip()
    return content


def _ensure_level_one_heading(
    markdown: str,
    required_headings: tuple[str, ...],
) -> str:
    """模型省略一级标题时，补回约定的文档标题。"""

    if any(line.strip().startswith("# ") for line in markdown.splitlines()):
        return markdown
    required_title = next(
        (heading for heading in required_headings if heading.startswith("# ")),
        None,
    )
    if required_title is None:
        return markdown
    return f"{required_title}\n\n{markdown}"


def _validate_markdown(markdown: str, required_headings: tuple[str, ...]) -> None:
    """检查 Markdown 是否包含约定的所有章节。"""

    lines = {line.strip() for line in markdown.splitlines()}
    has_level_one_heading = any(
        line.startswith("# ") and line[2:].strip() for line in lines
    )
    missing = [
        heading
        for heading in required_headings
        if heading not in lines
        # 模型可以把“# 视频主题”替换成更具体的实际主题；二级章节仍需完全一致。
        and not (heading.startswith("# ") and has_level_one_heading)
    ]
    if missing:
        raise InvalidLLMResponseError(
            "DeepSeek 返回的 Markdown 缺少必要章节：" + "、".join(missing)
        )


def _request_markdown(
    *,
    system_prompt: str,
    user_prompt: str,
    required_headings: tuple[str, ...],
    model: str | None = None,
) -> str:
    """执行一次 DeepSeek 请求，并验证返回的 Markdown。"""

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "未找到 DEEPSEEK_API_KEY。请先在环境变量中设置 DeepSeek API Key。"
        )

    selected_model = model or os.environ.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL
    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    try:
        for attempt in range(2):
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=MAX_OUTPUT_TOKENS,
                stream=False,
                # 学习笔记是结构化整理任务，关闭默认思考模式，把生成额度留给正文。
                extra_body={"thinking": {"type": "disabled"}},
            )

            if not response.choices:
                if attempt == 0:
                    continue
                raise InvalidLLMResponseError(
                    "DeepSeek 连续两次没有返回可用结果，请稍后重试。"
                )

            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise InvalidLLMResponseError(
                    "DeepSeek 输出达到长度上限，返回的学习笔记不完整。请重试；"
                    "如果仍然失败，请改用 technical 或 course 模式。"
                )

            raw_content = choice.message.content
            if not isinstance(raw_content, str) or not raw_content.strip():
                if attempt == 0:
                    continue
                raise InvalidLLMResponseError(
                    "DeepSeek 连续两次返回了空的 Markdown，请稍后重试。"
                )

            markdown = _clean_markdown(raw_content)
            markdown = _ensure_level_one_heading(markdown, required_headings)
            _validate_markdown(markdown, required_headings)
            return markdown
    except AuthenticationError as error:
        raise LLMRequestError("身份验证失败，请检查 DEEPSEEK_API_KEY。") from error
    except RateLimitError as error:
        raise LLMRequestError(
            "DeepSeek 请求频率过高或账户额度不足，请稍后重试。"
        ) from error
    except (APITimeoutError, APIConnectionError) as error:
        raise LLMRequestError("无法连接 DeepSeek API，请检查网络后重试。") from error
    except APIStatusError as error:
        raise LLMRequestError(f"DeepSeek API 返回错误（HTTP {error.status_code}）。") from error
    except OpenAIError as error:
        raise LLMRequestError(f"DeepSeek API 请求失败：{error}") from error

    raise InvalidLLMResponseError("DeepSeek 没有返回可用结果。")


def _request_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> str:
    """执行一次要求 JSON 对象的 DeepSeek 请求。"""

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "未找到 DEEPSEEK_API_KEY。请先在环境变量中设置 DeepSeek API Key。"
        )

    selected_model = model or os.environ.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL
    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    try:
        response = client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            stream=False,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        if not response.choices:
            raise InvalidLLMResponseError("DeepSeek 没有返回可用的 JSON。")
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise InvalidLLMResponseError(
                "DeepSeek 输出达到长度上限，返回的分段 JSON 不完整。"
            )
        raw_content = choice.message.content
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise InvalidLLMResponseError("DeepSeek 返回了空的 JSON。")
        return raw_content
    except AuthenticationError as error:
        raise LLMRequestError("身份验证失败，请检查 DEEPSEEK_API_KEY。") from error
    except RateLimitError as error:
        raise LLMRequestError(
            "DeepSeek 请求频率过高或账户额度不足，请稍后重试。"
        ) from error
    except (APITimeoutError, APIConnectionError) as error:
        raise LLMRequestError("无法连接 DeepSeek API，请检查网络后重试。") from error
    except APIStatusError as error:
        raise LLMRequestError(f"DeepSeek API 返回错误（HTTP {error.status_code}）。") from error
    except OpenAIError as error:
        raise LLMRequestError(f"DeepSeek API 请求失败：{error}") from error


def generate_study_notes(
    subtitle_text: str,
    *,
    video_title: str = "",
    video_description: str = "",
    mode: str | None = None,
    extra_instruction: str | None = None,
    model: str | None = None,
) -> str:
    """调用 DeepSeek，将字幕生成 Markdown 学习笔记。

    API Key 只从 ``DEEPSEEK_API_KEY`` 环境变量读取。如果需要
    替换模型，可以传入 ``model``，或设置 ``DEEPSEEK_MODEL``。
    """

    if not isinstance(subtitle_text, str) or not subtitle_text.strip():
        raise LLMError("字幕文本为空，无法生成学习笔记。")
    if extra_instruction is not None and not isinstance(extra_instruction, str):
        raise LLMError("额外学习要求必须是字符串或 None。")

    try:
        note_mode = get_note_mode(mode)
    except NoteModeError as error:
        # 对外统一使用 LLMError 体系，便于命令行和 pipeline 处理。
        raise InvalidNoteModeError(str(error)) from error

    user_prompt = build_study_notes_prompt(
        subtitle_text.strip(),
        video_title=video_title.strip(),
        video_description=video_description.strip(),
        mode=note_mode.key,
        extra_instruction=(extra_instruction or "").strip(),
    )
    return _request_markdown(
        system_prompt=STUDY_NOTES_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        required_headings=note_mode.required_headings,
        model=model,
    )


def generate_course_summary(
    part_notes: list[tuple[int, str, str]],
    *,
    course_title: str = "",
    failed_parts: list[str] | None = None,
    model: str | None = None,
) -> str:
    """读取所有成功的分 P 笔记，生成课程级 Markdown 总结。"""

    if not part_notes:
        raise LLMError("没有可用的分 P 学习笔记，无法生成课程总结。")

    failures = list(failed_parts or [])
    user_prompt = build_course_summary_prompt(
        part_notes,
        course_title=course_title.strip(),
        failed_parts=failures,
    )
    markdown = _request_markdown(
        system_prompt=COURSE_SUMMARY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        required_headings=REQUIRED_SUMMARY_HEADINGS,
        model=model,
    )

    # 处理状态由程序追加，确保模型不会遗漏失败分 P。
    status_lines = [
        f"- 成功：P{page_number:02d} {title}"
        for page_number, title, _ in part_notes
    ]
    if failures:
        status_lines.extend(f"- 失败：{item}" for item in failures)
    else:
        status_lines.append("- 失败：无")

    return markdown.rstrip() + "\n\n## 处理状态\n" + "\n".join(status_lines)


def generate_segment_plan(
    transcript: Transcript,
    *,
    video_title: str = "",
    video_description: str = "",
    model: str | None = None,
) -> SegmentPlan:
    """调用 DeepSeek 规划单 P 字幕的语义分段。"""

    if not isinstance(transcript, Transcript) or not transcript.cues:
        raise LLMError("字幕没有有效时间轴 cue，无法规划语义分段。")
    raw_response = _request_json(
        system_prompt=SEGMENT_PLAN_SYSTEM_PROMPT,
        user_prompt=build_segment_plan_prompt(
            transcript.to_srt(),
            video_title=video_title.strip(),
            video_description=video_description.strip(),
        ),
        model=model,
    )
    try:
        return parse_segment_plan(raw_response)
    except SegmentationError as error:
        raise InvalidLLMResponseError(f"DeepSeek 返回的分段方案无效：{error}") from error


def generate_segment_note_contents(
    assigned_segments: tuple[AssignedSegment, ...],
    *,
    video_title: str = "",
    video_description: str = "",
    extra_instruction: str | None = None,
    model: str | None = None,
) -> tuple[SegmentNoteContent, ...]:
    """一次调用读取全部切分字幕并生成各段正文和总结重点。"""

    if not assigned_segments:
        raise LLMError("没有已分配的字幕分段，无法生成分段笔记。")
    if extra_instruction is not None and not isinstance(extra_instruction, str):
        raise LLMError("额外学习要求必须是字符串或 None。")

    segment_sources = [
        (
            index,
            assigned.segment.title,
            assigned.segment.start_seconds,
            assigned.segment.end_seconds,
            assigned.transcript.to_srt(),
        )
        for index, assigned in enumerate(assigned_segments, start=1)
    ]
    raw_response = _request_json(
        system_prompt=SEGMENT_CONTENT_SYSTEM_PROMPT,
        user_prompt=build_segment_content_prompt(
            segment_sources,
            video_title=video_title.strip(),
            video_description=video_description.strip(),
            extra_instruction=(extra_instruction or "").strip(),
        ),
        model=model,
    )
    try:
        return parse_segment_note_contents(
            raw_response,
            expected_count=len(assigned_segments),
        )
    except SegmentationError as error:
        raise InvalidLLMResponseError(f"DeepSeek 返回的分段内容无效：{error}") from error
