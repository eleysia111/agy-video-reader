<p align="center">
  <img src="assets/logo.png" alt="AGY Video Reader Logo" width="128" height="128" />
</p>

<h1 align="center">agy-video-reader</h1>

<p align="center">
  <b>甩一个视频链接，让你的 AI 真正「看见」视频画面、读懂字幕与热评弹幕。</b><br/>
  原生支持 <b>Antigravity</b>（插件 + MCP App 设置面板）与 <b>DeepSeek Harness (DSH)</b>，一套 MCP 服务鱼猫通用。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/MCP-Stdio%20Server-6366F1" alt="MCP Stdio Server" />
  <img src="https://img.shields.io/badge/Antigravity-Plugin%20%2B%20UI-9333EA" alt="Antigravity Plugin" />
  <img src="https://img.shields.io/badge/DSH-Compatible-10B981" alt="DSH Compatible" />
  <img src="https://img.shields.io/badge/License-MIT-F59E0B" alt="MIT License" />
</p>

---

## 为什么做这个插件？

日常把 B站、YouTube 视频链接丢给 AI 伴侣或编程 Agent 时，常常会遇到三个痛点：
1. **只能看标题盲猜**：普通网页抓取拿不到视频画面、听不到配音，更看不到评论区玩什么梗。
2. **直接下载太重**：动辄几百 MB 的长视频会瞬间撑爆上下文或拖垮下载。
3. **跨端兼容踩坑多**：不同宿主对视频、图片和工具 Schema 的支持差异很大（比如部分网关遇到无参空 Schema 会直接报 `400`）。

`agy-video-reader` 用一套轻量、零污染的 Python MCP 服务把这些问题一次解决。

---

## 核心特性

- **先探针，后按需拉取**：`inspect_video_link` 秒级获取标题、UP主、时长、简介、字幕列表与时长预警，绝不盲目下载大文件。
- **双端最优视觉模式**：
  - **Antigravity（`mode="video"`）**：下载轻量 `.mp4`（默认 `<= 480p` / `<= 25MB`，超限由 `ffmpeg` 自动压制），直接配合 `read_file` 观看完整音画。
  - **DeepSeek Harness / 通用视觉 Agent（`mode="frames_and_text"`）**：均匀抽取关键帧并自动拼接成一张带时间戳的 **故事板网格图 `storyboard.jpg`** + 字幕全文；开启 `AGY_VIDEO_INLINE_STORYBOARD=1` 可通过标准 MCP `ImageContent` 直出拼图，一次调用直接看懂全片。
- **超长视频防呆守卫**：超过设定时长阈值（默认 300 秒）自动拒绝全量下载，引导先看字幕/热评或指定 `start_time` / `end_time` 切片观看。
- **B站热评与高能弹幕摘要**：基于 `bilibili-api-python` 抓取高赞热评与弹幕高能时间段；支持可选 `cookies.txt`，遇到风控或登录限制自动优雅降级，不中断主流程。
- **灵活调参（GUI + 自然语言 + 环境变量）**：
  - 在 **Antigravity** 中内置亮/暗双主题适配的 MCP App 可视化设置面板（侧边栏 `Plugins > 🎬 视频读取设置` 或对话调用 `open_settings`），实时调节时长上限、分辨率（360p~1080p）、最大体积、关键帧数与缓存上限。
  - 在 **DSH** 中遵循极简原生理念，无需额外 GUI，直接用自然语言让 Agent 调用 `update_settings` 或配置环境变量即可（如需 Web 面板也可自行搭配社区 `dsh-mcp-apps`）。
- **网关友好 & 自动缓存清理**：所有工具均杜绝无参空 Schema，避免网关 400 假报错；内置基于条目数 / 容量 / 存活时长的 LRU 缓存自动淘汰。

---

## 快速开始

### 环境要求
- Python `>= 3.12`
- [`uv`](https://docs.astral.sh/uv/)（用于隔离插件专属虚拟环境，不污染全局 Python）
- （可选）系统级 `ffmpeg`；若本机未安装，插件会自动回退到内置的 `imageio-ffmpeg` 二进制

---

### 1. 安装到 Antigravity（推荐）

```bash
git clone https://github.com/eleysia111/agy-video-reader.git
cd agy-video-reader
```

- **Windows**：双击或运行 `install.bat`
- **macOS / Linux**：运行 `bash install.sh`

脚本会将插件安装到 `~/.gemini/config/plugins/agy-video-reader` 并自动执行 `uv sync`。Antigravity 会自动发现并热加载插件，无需重启。

> 国内网络如 `uv sync` 下载缓慢，可临时使用国内镜像加速：
> ```bash
> uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
> ```

---

### 2. 挂载到 DeepSeek Harness (DSH)

先在克隆下来的仓库目录执行一次 `uv sync`，然后在你的 DSH Profile 配置中加入以下条目（完整模板见 [`examples/dsh-mcp-client.yml`](examples/dsh-mcp-client.yml)）：

```yaml
- id: mcp-agy-video-reader
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: agy-video-reader
    transport: stdio
    command: uv
    args:
      - run
      - --directory
      - /absolute/path/to/agy-video-reader
      - python
      - -m
      - agy_video_reader.server
    env:
      # DSH 默认走故事板+字幕，不与同机 Antigravity 的 video 模式冲突
      AGY_VIDEO_DEFAULT_MODE: frames_and_text
      # 以 MCP ImageContent 直接返回带时间戳的故事板拼图
      AGY_VIDEO_INLINE_STORYBOARD: '1'
```

---

## 日常怎么用？

装好后直接像聊天一样把链接发给你的 Agent 即可：

- 「帮我看看这个 B站视频讲了什么，再顺便总结一下评论区热评：`https://www.bilibili.com/video/BV...`」
- 「这个视频有点长，先帮我看下简介和弹幕高能片段，再切片看 `02:00` 到 `04:30`」
- 「把视频读取插件的最大时长调到 600 秒、分辨率改成 720p」
- 「打开视频读取设置面板 / 清理一下视频缓存」

---

## MCP 工具一览

| 工具 | 说明 |
| --- | --- |
| `inspect_video_link(url)` | 快速探测标题、作者、时长、简介、可用字幕与是否超过阈值 |
| `fetch_video_bundle(url, mode="auto", start_time=None, end_time=None, inline_storyboard=None)` | 下载轻量 `.mp4` 或生成带时间戳故事板 + 关键帧 + 字幕，并附带热评/弹幕摘要 |
| `fetch_comments(url, limit=None)` | 抓取高赞热评与 B站高能弹幕时间段摘要 |
| `open_settings(include_cache_stats=True)` | 打开 Antigravity 设置面板并返回当前配置与缓存统计 |
| `update_settings(...)` | 持久化修改最大时长、分辨率、体积上限、关键帧数、热评条数、缓存上限或恢复默认 |
| `cleanup_cache(max_age_hours=None)` | 手动清理本地缓存目录（后台已内置 LRU 自动淘汰） |

### 环境变量

| 变量 | 可选值 | 说明 |
| --- | --- | --- |
| `AGY_VIDEO_DEFAULT_MODE` | `auto` / `video` / `frames_and_text` | 覆写默认读取模式，便于同机多宿主共存 |
| `AGY_VIDEO_INLINE_STORYBOARD` | `1` / `true` | 在 `frames_and_text` 模式下通过 `ImageContent` 直出 `storyboard.jpg` |

---

## 可选 Cookies 说明

部分平台（如 B站）的完整热评或高清字幕可能需要登录会话。如有需要，可将自己浏览器导出的 Netscape 格式 Cookie 保存为插件根目录下的 `cookies.txt`（参考 [`cookies.txt.example`](cookies.txt.example)）。

`cookies.txt`、`settings.json`、`.venv/` 与 `cache/` 均已写入 `.gitignore`，请勿提交个人凭证到公开仓库。

## 合规与安全声明

本项目仅用于辅助读取公开可访问的网页视频、字幕与评论信息，或基于用户自愿提供的合法会话读取其有权访问的内容。**严禁用于绕过付费墙、会员加密或任何 CAPTCHA / 人机验证。**

## License

[MIT](LICENSE)
