---
name: video-reader
description: 当用户发来网页视频链接（如 B站 bilibili.com / b23.tv、YouTube 等）并希望 Agent 看视频、总结内容、查看画面细节、阅读评论区/弹幕或调整视频读取参数时使用。
---

# Video Reader 使用指南

本技能配合 `agy-video-reader` MCP 工具使用，遵循「先探针、后轻量下载、再读文件」的安全高效流程：

## 1. 标准调用流程

1. **先探测元信息**：收到视频链接后，先调用 `inspect_video_link(url)` 获取标题、UP主/作者、时长、简介、可用字幕列表以及是否超过当前配置的时长阈值（默认 300 秒，不下载大文件）。
2. **判断时长与模式**：
   - **阈值内视频（<= `max_duration_seconds`）**：调用 `fetch_video_bundle(url, mode="auto")`（在支持视频读取的 Antigravity 中默认下载轻量 `.mp4`，受 `max_height` 与 `max_size_mb` 限制，超出自动由 `ffmpeg` 压制）。
   - **超时长视频（> `max_duration_seconds`）**：**严禁全量下载**。先结合元信息、简介与 `fetch_comments(url)` 向用户说明视频较长，或在明确关注片段后传入 `start_time` / `end_time` 切片调用 `fetch_video_bundle`。
   - **不支持直接看视频的 Agent（如部分 Claude Code / Cursor 环境）**：调用 `fetch_video_bundle(url, mode="frames_and_text")`，获取均匀抽取的关键帧 `.jpg` 路径与字幕全文。
3. **观看视频或关键帧**：
   - 在 Antigravity 中拿到返回的 `video_path` 后，**优先直接调用 `read_file` 读取该本地 `.mp4` 文件**观看视频画面与听觉内容，再结合 `subtitle_text` 和 `comments_summary` 给出完整回答。
   - 在仅支持图像的 Agent 中，依次查看 `keyframe_paths` 的 `.jpg` 并结合 `subtitle_text` 与热评总结。
4. **单独读取评论区与弹幕**：
   - 用户只想看评论区或高能弹幕时，直接调用 `fetch_comments(url)`，无需下载视频。
5. **参数设置与缓存管理**：
   - 当用户希望调整最大允许时长、下载分辨率（360p/480p/720p/1080p）、最大体积（MB）、关键帧数量或缓存上限时，可调用 `open_settings()` 打开 Antigravity 交互式设置面板，或直接调用 `update_settings(...)` 持久化修改（立即生效）。
   - 插件已内置后台 LRU 自动淘汰机制；如需立即清空，可调用 `cleanup_cache()`。

## 2. 合规与安全底线

- **严禁绕过付费墙、大会员加密、登录墙或 CAPTCHA / 人机验证**。
- 若遇到反爬风控或需要登录才可见的评论区，插件会自动优雅降级并返回提示。用户可自愿将自己账号导出的 Netscape 格式 `cookies.txt` 放置于插件根目录（`agy-video-reader/cookies.txt`），Agent 不得自行抓取或伪造凭证。
