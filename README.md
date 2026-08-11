# B 站视频 AI 学习笔记工具

输入一个 B 站视频链接或 BV 号，先获取视频字幕，再调用 DeepSeek API 生成 Markdown 学习笔记。项目同时提供命令行和 Streamlit 网页入口，支持自动检测同一 BV 号下的多 P 视频，逐 P 生成笔记后再生成课程总结。

项目使用维护活跃的开源项目 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 解析 B 站页面和字幕接口，不自行模拟浏览器请求。
模型调用使用 DeepSeek 官方文档推荐的 OpenAI Python SDK 兼容接口。

## 项目结构

```text
.
├── bilibili.py          # 独立字幕获取模块
├── llm.py               # DeepSeek API 调用模块
├── prompt.py            # 笔记模式、分 P 笔记和课程总结提示词
├── selection.py         # 分 P 选择表达式解析与校验
├── pipeline.py          # 单 P、多 P、文件保存和合集总结流程
├── main.py              # 命令行入口
├── web_service.py       # 网页参数、生成流程和展示结果适配层
├── streamlit_app.py     # Streamlit 网页入口
├── outputs/             # 生成的 Markdown 学习笔记
├── requirements.txt     # 运行依赖
├── requirements-dev.txt # 测试依赖
└── tests/
    ├── test_bilibili.py
    ├── test_llm.py
    ├── test_prompt.py
    ├── test_selection.py
    ├── test_main.py
    ├── test_pipeline.py
    └── test_web_service.py
```

## 环境要求

- Python 3.10 或更高版本
- 可以访问 B 站
- DeepSeek API Key

## 安装

在项目目录中执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell 激活虚拟环境的命令是：

```powershell
.venv\Scripts\Activate.ps1
```

## 配置 DeepSeek API Key

API Key 只从名为 `DEEPSEEK_API_KEY` 的环境变量读取，不要将 Key 直接写进 Python 文件。

macOS/Linux：

```bash
export DEEPSEEK_API_KEY="你的-DeepSeek-API-Key"
```

Windows PowerShell：

```powershell
$env:DEEPSEEK_API_KEY="你的-DeepSeek-API-Key"
```

默认使用 `deepseek-v4-flash`。如果需要切换到 Pro 模型，可以额外设置：

```bash
export DEEPSEEK_MODEL="deepseek-v4-pro"
```

## 运行

### 命令行入口

直接在命令后面传入视频链接：

```bash
python main.py "https://www.bilibili.com/video/BV1jJ411r7eH"
```

支持带参数的链接和直接 BV 号：

```bash
python main.py "https://www.bilibili.com/video/BV1DfrdByE2Hx/?spm_id_from=xxx"
python main.py "BV1DfrdByE2Hx"
```

程序会先输出 BV 号解析结果，再获取字幕、调用 DeepSeek，最后保存笔记：

```text
调试：实际提取到的 BV 号：BV1DfrdByE2Hx
视频标题：……
字幕获取成功：12345 个字符
正在调用 DeepSeek 生成学习笔记……
学习笔记已保存：.../outputs/BV1DfrdByE2Hx_study_notes.md
```

也可以只运行 `python main.py`，然后根据提示粘贴链接。

### Streamlit 网页入口

安装依赖后启动 Streamlit：

```bash
streamlit run streamlit_app.py
```

浏览器会打开本地页面。网页入口与命令行复用相同的解析、字幕、Prompt 和生成流程，不会在页面代码中重复业务逻辑。

输入 B 站视频链接或 BV 号并点击“解析视频”，页面会展示：

- 视频或课程标题
- BV号
- 视频简介
- 全部分P编号和标题

解析成功后可以：

- 为多 P 视频选择全部、单个、多个或连续范围的分 P
- 选择默认、`technical` 或 `course` 笔记模式
- 填写可选的额外学习要求
- 在生成期间查看字幕获取、模型调用和文件保存状态
- 在线查看生成的 Markdown 笔记
- 分别下载单 P、各成功分 P 和 `summary.md`

网页会明确区分以下处理状态：

- 成功生成的分 P
- 没有可用字幕的分 P
- 字幕获取、模型调用或文件处理失败的分 P
- `summary.md` 生成或读取失败

单 P 和多 P 的输出位置与命令行完全一致。单 P 保存为：

```text
outputs/BV号_study_notes.md
```

多 P 保存到独立的 BV 目录，成功的分 P 和课程总结都可以在网页中查看和下载。

只解析视频信息不需要 DeepSeek API Key；点击“生成学习笔记”前需要配置 `DEEPSEEK_API_KEY`。

如果 B 站要求登录后才能读取字幕，请在解析视频前通过“B站登录浏览器”选择 Chrome、Edge、Firefox 或 Safari。所选浏览器需要已经登录 B 站；不需要登录的视频可以保持“不使用 Cookie”。

Cookie 由本机 yt-dlp 直接读取，网页不会显示或保存 Cookie，也不会把 Cookie 发送给 DeepSeek。如果读取失败，可以先关闭所选浏览器后重试；macOS 首次读取时也可能要求授权访问钥匙串。

### CLI 与 Web 的关系

```text
命令行：main.py ───────────────┐
                               ├─→ pipeline.py → bilibili.py / llm.py / prompt.py
网页：streamlit_app.py → web_service.py ┘
```

新增网页入口没有改变原有命令行参数、默认模式、文件名和输出目录结构。

### 笔记生成模式

使用 `--mode` 可以指定每个视频或分 P 的笔记结构：

| 模式 | 适用内容 | 主要章节 |
|---|---|---|
| `technical` | AI、编程和技术教程 | 核心概念、原理解释、实践案例、常见问题、复习问题 |
| `course` | 普通知识课程和公开课 | 内容概括、核心知识点、关键观点、知识关联、总结 |

例如：

```bash
python main.py "BV视频编号" --mode technical
python main.py "BV视频编号" --mode course
```

不传 `--mode` 时继续使用 v0.1 的原有笔记结构。`academic` 学术阅读模式已经预留配置接口，但当前版本暂不开放选择。

直接执行 `python main.py` 进入交互模式时，可以通过编号选择模式；直接回车使用原有默认模板。

Streamlit 页面提供相同的默认、`technical` 和 `course` 选择。额外学习要求可以在网页文本框中填写，它只影响单个视频或各分 P 的笔记，不改变 `summary.md` 的固定合集总结结构。

### 多 P 视频

命令行使用方式不变。直接传入包含多个分 P 的 BV 号：

```bash
python main.py "BV多P视频编号" --cookies-from-browser chrome
```

默认情况下，程序会自动获取所有分 P，每个分 P 独立执行“字幕 → DeepSeek → Markdown”，不会把所有原始字幕合并成一次请求。

使用 `--parts` 可以只生成指定分 P 的笔记：

```bash
python main.py "BV多P视频编号" --parts "3"
python main.py "BV多P视频编号" --parts "1,3,5"
python main.py "BV多P视频编号" --parts "1-5"
python main.py "BV多P视频编号" --parts "1,3,5-8"
python main.py "BV多P视频编号" --parts "1,3,5-8" --mode technical
```

选择表达式支持单个编号、逗号分隔的多个编号和连续范围。重复编号会自动去重，处理顺序始终按视频原始分 P 顺序排列；非法格式或不存在的分 P 会在开始获取字幕前明确提示。

直接执行 `python main.py` 进入交互模式时，程序会先显示全部分 P 标题，再询问需要处理的分 P。直接回车仍然处理全部分 P。

Streamlit 页面使用完全相同的选择规则。留空处理全部分 P，也可以输入 `3`、`1,3,5` 或 `1,3,5-8`。

输出结构：

```text
outputs/
└── BV多P视频编号/
    ├── P01_第一章.md
    ├── P02_第二章.md
    ├── P03_第三章.md
    └── summary.md
```

分 P 标题中不适合文件名的字符会自动替换。某个分 P 无字幕、请求失败或笔记生成失败时，程序会跳过该 P 并继续。

`course` 是单个视频或单个分 P 的笔记模式，只决定每份分 P 笔记的结构。`summary.md` 是所有选中分 P 处理完成后的固定合集总结，不是笔记模式，也不受 `--mode` 控制。

`summary.md` 会读取本次成功写入的分 P 笔记，再调用一次 DeepSeek，包含：

- 视频整体主题
- 核心知识体系
- 各章节关系
- 关键概念
- 学习建议
- 本次成功与失败的分 P 状态

因此，一个共 N 个分 P 且全部成功的视频，会调用 N 次分 P 笔记生成和 1 次课程总结生成。

网页只展示本次选择产生的处理报告，不会扫描并混入输出目录中的历史文件。如果课程总结失败，已经成功生成的分 P 笔记仍会保留，并可继续查看和下载。

### 字幕需要登录时

B 站目前经常要求登录后才返回 CC/AI 字幕。先在浏览器中登录 B 站，然后运行：

```bash
python main.py "https://www.bilibili.com/video/BV1jJ411r7eH" --cookies-from-browser chrome
```

把 `chrome` 换成你使用的浏览器，例如 `edge`、`firefox` 或 `safari`。登录 Cookie 由 yt-dlp 直接读取，本项目不会打印或保存 Cookie。

> 如果浏览器正在占用 Cookie 数据库，可以先关闭浏览器后再试。不要把 Cookie 发给他人或提交到 Git。

## Markdown 笔记结构

不传 `--mode` 时沿用下面的默认结构：

```markdown
# 视频主题
视频的主要内容与学习目标。

## 核心知识点
### 1. 知识点名称
- 定义：……
- 解释：……
- 重要程度：高/中/低，以及判断理由。

## 关键观点
- ……

## 与已有知识关联
- ……

## 复习问题
1. ……
```

如果视频没有字幕，程序会明确输出：

```text
错误：该视频没有可用的 CC/AI 字幕。
```

## 在其他 Python 代码中调用

```python
from bilibili import fetch_video_subtitle
from llm import generate_study_notes

video = fetch_video_subtitle(
    "https://www.bilibili.com/video/BV1jJ411r7eH",
    cookies_from_browser="chrome",  # 不需登录的视频可以删掉这一行
)

notes = generate_study_notes(
    video.subtitle_text,
    video_title=video.title,
    video_description=video.description,
    mode="technical",
    # 网页和 Python 接口支持；当前命令行不开放额外要求参数。
    extra_instruction="请重点解释代码实现和设计原因。",
)

print(notes)
```

## 运行测试

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

单元测试不访问 B 站，不会消耗网络流量。

## 当前范围

- 支持 `https://www.bilibili.com/video/BV...` 形式的链接、带查询参数的链接和直接 BV 号。
- 单 P 视频继续保存为 `outputs/BV号_study_notes.md`，原有用法不变。
- 同一 BV 号下的多 P 视频默认自动处理全部分 P，也可以用 `--parts` 选择部分分 P；输入链接中的 `?p=` 不会代替 `--parts`。
- 支持 `technical` 和 `course` 两种可选笔记模式；不指定时沿用 v0.1 默认模板。
- `course` 只控制单P笔记结构，`summary.md` 仍使用独立、固定的合集总结流程。
- Streamlit 页面支持生成状态显示、Markdown 在线查看和单文件下载。
- 多语言字幕优先返回简体中文，否则返回第一个可用语言。
- 弹幕不当作字幕。
- 只处理同一 BV 号内的分 P，不扩展为 UP 主播放列表或其他视频合集。
- 不会下载视频或音频文件。
- DeepSeek 返回的 Markdown 必须包含所有约定章节，否则程序会明确报错且不会保存不完整文件。
- 当前不包含数据库、RAG、用户系统或多用户数据隔离。
- Streamlit 页面可以选择本机已登录 B 站的 Chrome、Edge、Firefox 或 Safari，由 yt-dlp 读取 Cookie；页面不接收、显示或保存 Cookie 原文。
- 下载功能以单个 Markdown 文件为单位，不生成 ZIP 压缩包。
- B 站接口会变化；遇到解析问题时，先执行 `python -m pip install -U yt-dlp`。
