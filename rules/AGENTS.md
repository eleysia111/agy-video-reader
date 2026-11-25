# agy-video-reader 视频与评论区读取规范

当用户提供网页视频链接（如 B站 `bilibili.com` / `b23.tv`、YouTube 等）要求观看、分析画面、总结内容或查看评论区/弹幕时：

1. **先探测后下载**：优先调用 `agy-video-reader` 的 `inspect_video_link(url)` 检查时长与元信息；超过 300 秒的视频不要尝试全量下载，应结合 `start_time` / `end_time` 切片或先读取评论与字幕。
2. **优先用 `read_file` 看轻量视频**：在支持视频读取的 Antigravity 环境中，使用 `fetch_video_bundle(url, mode="video")` 获取 <=480p 且 <=25MB 的本地 `.mp4`，并直接调用 `read_file` 观看该视频文件。若当前 Agent 不支持直接读取视频，改用 `mode="frames_and_text"` 读取均匀关键帧 `.jpg` 与字幕全文。
3. **评论区优雅降级**：使用 `fetch_comments(url, limit=30)` 获取热评与 B站高能弹幕摘要；若因平台限流/登录限制发生降级，如实告知用户并基于已获取的视频/字幕信息继续完成回答，不得报错中断。
4. **严禁绕过限制**：严禁任何破解付费墙、绕过 CAPTCHA/反爬验证码或抓取未授权私密内容的行为。
