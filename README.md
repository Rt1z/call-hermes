# Call Hermes

Call Hermes 是一个面向 iPhone Safari/PWA 的实时语音对话项目。浏览器通过 WebRTC 将麦克风音频发送到 FastAPI 服务，服务端使用阿里云百炼完成实时语音识别和语音合成，并通过兼容 OpenAI Chat Completions 的 Hermes API 生成回复。

## 功能

- WebRTC 双向实时音频传输
- 阿里云百炼 `fun-asr-realtime` 实时语音识别
- Hermes 流式文本回复
- 多轮对话历史与 Hermes 上下文，重连后继续当前会话
- 阿里云百炼 `qwen3-tts-flash-realtime` 流式语音合成
- 服务端 VAD 自动断句，无需再次点击麦克风提交
- 播放期间支持说话打断
- 可关闭麦克风，并暂停向服务端发送音频
- 支持选择 Safari 暴露的本机或蓝牙麦克风
- 支持选择 TTS 音色和调节播放速度
- 提供文字 Debug 模式和前端诊断日志
- WebRTC 失败时提供 HTTPS 录音降级通道
- 支持 STUN/TURN，适用于移动网络和受限 NAT 环境
- 实时显示 WebRTC 网络质量，并根据 RTT、jitter 和丢包率自动调整 TTS 音频预缓冲

## 界面预览

<p align="center">
  <img src="docs/images/pwa-conversation.jpg" alt="Call Hermes PWA 多轮语音对话界面" width="390">
</p>

## 数据流程

```text
iPhone Safari/PWA
  -> WebRTC 麦克风音频
  -> voice-bridge 重采样为 16 kHz 单声道 PCM
  -> 百炼实时 ASR
  -> Hermes /v1/chat/completions 流式接口
  -> 百炼实时 TTS
  -> WebRTC 远端音频轨
  -> iPhone 播放
```

API Key、Hermes 凭据和 TURN 密码只保存在服务器上，不会下发到 PWA。PWA 使用共享密钥换取短期 JWT，再建立 WebRTC 会话。

## 目录结构

```text
server/                 FastAPI、WebRTC、ASR、Hermes、TTS 服务
server/app/static/      PWA 前端
server/tests/           自动化测试
scripts/                启停、健康检查和 TURN 检查脚本
deploy/                 Caddy、coturn 等部署配置参考
ssl/                    本地 TLS 证书目录，不纳入 Git
```

## 环境要求

- Linux
- Python 3.11 或更高版本
- 可访问的 Hermes Chat Completions API
- 阿里云百炼 API Key
- 有效域名和 TLS 证书
- coturn，移动网络场景建议安装

iPhone Safari 只有在 HTTPS 安全上下文中才能使用麦克风。Safari 可以选择其公开的音频输入设备，但扬声器输出路由仍由 iOS 控制。

## 安装

```bash
cp server/.env.example server/.env
cd server
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cd ..
```

生成用于开发环境的随机密钥：

```bash
openssl rand -hex 32
```

分别为 `APP_SHARED_SECRET`、`JWT_SECRET` 和 TURN 密码生成不同的值。不要将 `server/.env`、证书私钥或日志提交到 Git。

## 配置

编辑 `server/.env`，至少确认以下配置：

```dotenv
PUBLIC_BASE_URL=https://your-domain.example:10005
CORS_ALLOW_ORIGINS=https://your-domain.example:10005

APP_SHARED_SECRET=替换为随机密钥
JWT_SECRET=替换为另一个随机密钥

DASHSCOPE_API_KEY=sk-xxxxxxxx
DASHSCOPE_ASR_MODEL=fun-asr-realtime
DASHSCOPE_TTS_MODEL=qwen3-tts-flash-realtime
DASHSCOPE_TTS_VOICE=Cherry

HERMES_BASE_URL=http://127.0.0.1:8642
HERMES_API_KEY=
HERMES_MODEL=hermes
HERMES_TIMEOUT_SECONDS=45
HERMES_HISTORY_MAX_TURNS=12
HERMES_HISTORY_MAX_CHARS=24000

WEBRTC_AUDIO_PREBUFFER_SECONDS=0.6
WEBRTC_ADAPTIVE_BUFFER_ENABLED=true
WEBRTC_AUDIO_PREBUFFER_MIN_SECONDS=0.5
WEBRTC_AUDIO_PREBUFFER_MAX_SECONDS=1.2
AUTO_VAD_PREROLL_MS=500

SSL_CERT_FILE=../ssl/your-domain.example.pem
SSL_KEY_FILE=../ssl/your-domain.example.key

ICE_STUN_URLS=stun:stun.l.google.com:19302
ICE_TURN_URLS=turn:your-domain.example:10004?transport=udp,turn:your-domain.example:10004?transport=tcp
ICE_TURN_USERNAME=替换为TURN用户名
ICE_TURN_CREDENTIAL=替换为TURN密码
```

`HERMES_API_KEY` 是否留空取决于 Hermes 服务是否启用了鉴权。`HERMES_BASE_URL` 必须指向实际监听地址，服务端会在该地址后调用 `/v1/chat/completions`。

开发时如需绕过百炼，可设置：

```dotenv
USE_MOCK_ASR=true
USE_MOCK_TTS=true
```

## HTTPS 启动

将证书和私钥放入 `ssl/`，然后前台运行：

```bash
./scripts/run_https.sh
```

默认监听 `0.0.0.0:10005`。也可以显式指定端口和证书：

```bash
PORT=10005 \
CERT_FILE="$PWD/ssl/your-domain.example.pem" \
KEY_FILE="$PWD/ssl/your-domain.example.key" \
./scripts/run_https.sh
```

后台运行和停止：

```bash
./scripts/start_https_background.sh
./scripts/stop_https.sh
```

查看日志：

```bash
tail -f server/voice-bridge-https.log
tail -f server/logs/voice-bridge.log
```

## TURN

Web 服务端口为 `10005`，TURN 默认端口为 `10004`，两者用途不同，不能在 PWA 的 Bridge URL 中互换。

安装 coturn 后，先配置 `ICE_TURN_URLS`、`ICE_TURN_USERNAME` 和 `ICE_TURN_CREDENTIAL`，再运行：

```bash
TURN_PORT=10004 ./scripts/run_turn.sh
```

防火墙和路由器需要同时放行或转发 TCP/UDP `10004`。检查配置：

```bash
./scripts/check_turn.sh
```

## PWA 使用

1. 在 iPhone Safari 打开 `https://your-domain.example:10005`。
2. 打开设置，填写 Bridge URL 和与服务端一致的 Shared Secret。
3. 点击连接按钮并允许使用麦克风。
4. 连接后直接说话，VAD 会自动判断说话结束并提交。
5. 状态栏会显示当前 WebRTC 网络质量；鼠标悬停可查看 RTT、jitter、丢包率、连接路径和缓冲大小。
6. 点击底部麦克风按钮可暂停或恢复音频发送。
7. 点击右上角设备图标可选择 Safari 当前公开的麦克风；Debug 信息位于设置页面底部。

对话记录会在当前页面会话中完整保留。服务端默认向 Hermes 携带最近 12 轮、最多 24000 个字符的上下文；刷新页面或创建新的认证会话后重新开始。

需要作为 PWA 使用时，可通过 Safari 的“添加到主屏幕”安装。

## 健康检查与排错

```bash
./scripts/health.sh
./scripts/check_config.sh
```

`GET /health` 会分别报告 Hermes、ASR、TTS、TURN、TLS 证书、自适应缓冲和活动会话状态。浏览器端最近的诊断日志位于设置页面底部，服务端完整日志位于 `server/logs/voice-bridge.log`。

常见端口：

| 端口 | 用途 |
| --- | --- |
| `10005/TCP` | PWA、HTTPS API、WebRTC 信令 |
| `10004/TCP+UDP` | coturn 中继 |
| `8642/TCP` | 示例 Hermes 本机接口，仅建议监听内网或回环地址 |

## 测试

```bash
cd server
source .venv/bin/activate
ruff check app tests
pytest -q
node --check app/static/app.js
node --check app/static/rtc.js
node --check app/static/ui.js
```

## 接口

- `POST /auth/session`：使用共享密钥换取短期 JWT 和会话 ID
- `GET /rtc/config`：获取 ICE 与 TTS 配置
- `POST /rtc/offer`：提交 SDP Offer 并获取 SDP Answer
- WebRTC `events` DataChannel：传输识别文本、状态、回复增量和错误
- WebRTC 音频轨：上传麦克风音频并接收合成语音
- `POST /pwa/turn`：WebRTC 不可用时的录音降级接口
- `POST /client/log`：接收 PWA 客户端诊断日志
- `GET /health`：服务和配置健康检查

## 安全建议

- 生产环境使用独立且足够长的随机密钥。
- Hermes API 只监听 `127.0.0.1` 或受保护的内网地址。
- 不要在 PWA 中保存百炼或 Hermes API Key。
- 定期检查 TLS 证书有效期与 TURN 凭据。
- 确认 `server/.env`、`ssl/` 私钥和日志始终处于 Git 忽略状态。
