# Call Hermes

Call Hermes 是一个面向现代桌面与移动浏览器的实时语音对话 PWA。浏览器通过 WebRTC 将麦克风音频发送到 FastAPI 服务，服务端使用阿里云百炼完成实时语音识别和语音合成，并通过兼容 OpenAI Chat Completions 的 Hermes API 生成回复。项目重点适配 iPhone Safari，同时支持 Android Chrome 以及 PC 端 Chrome、Edge、Firefox 和 Safari。

## 界面预览

<p align="center">
  <img src="docs/images/pwa-conversation.gif" alt="Call Hermes PWA 实时语音对话演示" width="300">
</p>

## 亮点功能

- **端到端全流式对话**：WebRTC 实时上传麦克风音频，百炼实时 ASR 持续转写，Hermes 流式生成文本，再由百炼实时 TTS 分段合成并回传播放，减少等待完整回复的延迟。
- **免按键自然交互**：服务端 VAD 自动判断说话开始和结束，无需再次点击提交；预录音缓冲可保留开口前的音频，降低漏掉第一个字的概率。
- **可靠的语音打断**：播放回复时可以直接说话打断，系统会停止当前生成与播放，并将已识别的打断内容可靠写入下一轮 Hermes 上下文。
- **自适应流畅播放**：根据 WebRTC 的 RTT、jitter、丢包率和播放欠载动态调整音频预缓冲；网络波动时自动重新缓冲，兼顾首包速度与连续性。
- **完整多轮对话历史**：对话及上下文持久化到 SQLite，刷新页面、重连或服务重启后仍可恢复；支持搜索、重命名、收藏、切换、导出和删除会话。
- **多设备与跨平台 PWA**：适配 iPhone、iPad、Android、Windows、macOS 和 Linux，可选择浏览器公开的本机或蓝牙麦克风，并可安装到主屏幕或桌面。
- **个性化语音与模型**：可选择男女 TTS 音色、调节播放速度，并配置系统提示词、Hermes 模型、识别语言、上下文长度和最大输出 token。
- **弱网与连接恢复**：支持 STUN/TURN 穿透移动网络和受限 NAT；WebRTC 失败时可使用 HTTPS 录音降级通道，并支持自动重连和待处理消息恢复。
- **安全的账号与设备会话**：使用账号密码登录、短期访问 JWT 和 HttpOnly 刷新 Cookie；支持刷新令牌轮换、设备撤销、登录限流与审计，第三方密钥始终只保存在服务端。
- **内置诊断与可观测性**：设置页提供文字 Debug、设备和前端诊断信息；服务端提供结构化日志、健康检查、Prometheus 指标、Grafana 仪表盘及延迟趋势数据。
- **面向长期运行的运维能力**：提供 systemd 自动重启、SQLite 在线备份、证书与磁盘巡检、容量限制、空闲会话清理和依赖熔断。
- **自动化质量保障**：覆盖后端、前端鉴权、网络恢复、真实供应商链路和多浏览器冒烟测试，并集成 Ruff、Bandit、依赖审计与 ShellCheck。

## 数据流程

```text
PC、Android 或 iPhone 浏览器/PWA
  -> WebRTC 麦克风音频
  -> voice-bridge 重采样为 16 kHz 单声道 PCM
  -> 百炼实时 ASR
  -> Hermes /v1/chat/completions 流式接口
  -> 百炼实时 TTS
  -> WebRTC 远端音频轨
  -> 当前设备播放
```

API Key、Hermes 凭据和 TURN 密码只保存在服务器上，不会下发到 PWA。PWA 登录后使用保存在内存中的短期访问 JWT 建立 WebRTC 会话，并通过 HttpOnly Cookie 安全续期。

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

浏览器通常只有在 HTTPS 安全上下文中才能使用麦克风，本机地址开发场景除外。Chrome、Edge 和 Safari 均可使用浏览器公开的音频输入设备；具体的蓝牙按键、后台运行和扬声器输出路由能力由操作系统及浏览器决定。

### 浏览器兼容性

- iPhone/iPad Safari：重点适配平台，可安装到主屏幕；后台运行、锁屏和输出路由受 iOS 限制。
- Android Chrome：支持 WebRTC、麦克风和 PWA 安装；不同厂商的蓝牙音频行为可能存在差异。
- Windows、macOS、Linux：支持最新版 Chrome、Edge、Firefox 和 Safari，具备主要通话、设置和 Debug 功能。
- 其他现代浏览器：具备 WebRTC、MediaDevices 和安全上下文时原则上可用，具体音频设备能力取决于浏览器实现。

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
CONVERSATION_DATABASE_PATH=data/conversations.sqlite3
MAX_CONCURRENT_SESSIONS=8
DEPENDENCY_HEALTH_CACHE_SECONDS=5

DASHSCOPE_API_KEY=sk-xxxxxxxx
DASHSCOPE_ASR_MODEL=fun-asr-realtime
DASHSCOPE_TTS_MODEL=qwen3-tts-flash-realtime
DASHSCOPE_TTS_VOICE=Cherry
DASHSCOPE_CONTROL_TIMEOUT_SECONDS=12
DASHSCOPE_TTS_AUDIO_TIMEOUT_SECONDS=90

HERMES_BASE_URL=http://127.0.0.1:8642
HERMES_API_KEY=
HERMES_MODEL=hermes
HERMES_TIMEOUT_SECONDS=45
HERMES_MAX_ATTEMPTS=2
HERMES_RETRY_BACKOFF_SECONDS=0.4
HERMES_MAX_TOKENS=500
HERMES_HISTORY_MAX_TURNS=12
HERMES_HISTORY_MAX_CHARS=24000

WEBRTC_AUDIO_PREBUFFER_SECONDS=1.0
WEBRTC_ADAPTIVE_BUFFER_ENABLED=true
WEBRTC_AUDIO_PREBUFFER_MIN_SECONDS=0.5
WEBRTC_AUDIO_PREBUFFER_MAX_SECONDS=1.2
WEBRTC_REBUFFER_STEP_SECONDS=0.2
WEBRTC_SESSION_IDLE_TIMEOUT_SECONDS=45
AUTO_VAD_SILENCE_MS=2500
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

生产环境建议交给 systemd 自动启动和故障重启：

```bash
chmod +x scripts/install_systemd.sh
./scripts/install_systemd.sh
```

查看日志：

```bash
tail -f server/voice-bridge-https.log
tail -f server/logs/voice-bridge.log
```

对话数据库可在线一致性备份，默认保留 14 天：

```bash
./scripts/backup_conversations.sh
```

`scripts/install_systemd.sh` 还会安装每日备份定时器，以及每 5 分钟运行的就绪、证书和磁盘检查。Prometheus 告警规则位于 `deploy/prometheus-alerts.yml`，Grafana 仪表盘位于 `deploy/grafana-dashboard.json`。

## TURN

Web 服务端口为 `10005`，TURN 默认端口为 `10004`，两者用途不同，不能在 PWA 的 Bridge URL 中互换。

安装 coturn 后，先配置 `ICE_TURN_URLS`、`ICE_TURN_USERNAME` 和 `ICE_TURN_CREDENTIAL`，再运行：

```bash
TURN_PORT=10004 ./scripts/run_turn.sh
```

防火墙和路由器需要同时放行或转发 TURN 入口的 TCP/UDP 端口，以及 coturn
`min-port` 到 `max-port` 对应的 UDP 中继端口。入口端口负责分配，中继端口承载实际音频。检查配置：

```bash
./scripts/check_turn.sh
```

## PWA 使用

1. 在 PC 或手机的现代浏览器中打开 `https://your-domain.example:10005`。
2. 打开设置，填写 Bridge URL、用户名和密码。首次启动用户名默认是 `admin`，密码为服务端 `APP_SHARED_SECRET`。
3. 点击连接按钮并允许使用麦克风。
4. 连接后直接说话，VAD 会自动判断说话结束并提交。
5. 状态栏会显示当前 WebRTC 网络质量；鼠标悬停可查看 RTT、jitter、丢包率、连接路径和缓冲大小。
6. 点击底部麦克风按钮可暂停或恢复音频发送。
7. 点击右上角设备图标可选择浏览器当前公开的麦克风；Debug 信息位于设置页面底部。
8. 设置中的 Conversation history 可搜索、重命名、收藏、切换、导出或删除持久化对话。
9. 浏览器支持时可从设置安装 PWA；静态壳支持离线启动，但语音通话仍需要网络。

对话记录以独立 `conversation_id` 持久化到 SQLite。选择“恢复对话”后，即使刷新页面、JWT
更新或服务重启，也会重新加载页面记录和 Hermes 上下文；选择“新建对话”会创建隔离的新上下文。
服务端默认向 Hermes 携带最近 12 轮、最多 24000 个字符。

需要作为 PWA 使用时，可通过 iPhone Safari 的“添加到主屏幕”安装，或使用 Android Chrome、桌面 Chrome/Edge 提供的安装入口。

## 健康检查与排错

```bash
./scripts/health.sh
./scripts/check_config.sh
```

`GET /live` 是不访问外部依赖的存活探针；公开的 `GET /ready` 和 `GET /health` 仅返回适合探针使用的
概要状态，登录后可通过 `GET /health/details` 查看 Hermes、ASR、TTS、TURN、TLS 证书、自适应缓冲、
容量和活动会话详情。Hermes 健康结果默认缓存 5 秒，避免探针放大依赖压力。`GET /metrics` 提供会话、
播放欠载、打断、Hermes 首 token、TTS 首音频和整轮响应指标。
浏览器诊断日志位于设置页面底部，服务端完整日志位于 `server/logs/voice-bridge.log`。

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
cd ..
node scripts/test_frontend.mjs
```

服务运行时可执行不调用 ASR/TTS 的三浏览器 WebRTC 冒烟测试：

```bash
APP_SHARED_SECRET='与 server/.env 一致的值' python3 scripts/e2e_browser_smoke.py
APP_SHARED_SECRET='与 server/.env 一致的值' CYCLES=20 python3 scripts/e2e_stability.py
APP_SHARED_SECRET='与 server/.env 一致的值' python3 scripts/e2e_network_adaptation.py
APP_SHARED_SECRET='与 server/.env 一致的值' python3 scripts/e2e_session_management.py
```

## 接口

- `POST /auth/session`：旧版兼容接口，默认关闭
- `POST /auth/login`：账号密码登录并签发短期访问令牌与 HttpOnly 刷新 Cookie
- `POST /auth/refresh`：轮换刷新令牌并获取新访问令牌
- `POST /auth/logout`：退出并撤销当前刷新令牌
- `GET /auth/devices`、`DELETE /auth/devices/{device_id}`：管理授权设备
- `GET /auth/audit`：查看账号安全审计记录
- `GET /auth/users`、`POST /auth/users`：管理员管理用户
- `POST /auth/password`：修改当前账号密码
- `GET /rtc/config`：获取 ICE 与 TTS 配置
- `POST /rtc/offer`：提交 SDP Offer 并获取 SDP Answer
- `DELETE /rtc/session`：主动结束并释放媒体会话
- `GET /rtc/sessions`：列出活动会话、设备类型和连接状态
- `DELETE /rtc/sessions/{session_id}`：远程断开指定活动会话
- `GET /conversations/{conversation_id}`：读取持久化对话历史
- `GET /conversations`：列出和搜索持久化对话
- `DELETE /conversations/{conversation_id}`：删除持久化对话
- `PATCH /conversations/{conversation_id}`：重命名、收藏或归档对话
- WebRTC `events` DataChannel：传输识别文本、状态、回复增量和错误
- WebRTC 音频轨：上传麦克风音频并接收合成语音
- `POST /pwa/turn`：WebRTC 不可用时的录音降级接口
- `POST /client/log`：接收 PWA 客户端诊断日志
- `GET /health`：服务和配置健康检查
- `GET /live`：进程存活探针
- `GET /ready`：包含依赖状态的就绪探针
- `GET /metrics`：运行时质量和可靠性指标
- `GET /metrics/prometheus`：Prometheus 文本格式指标

## 安全建议

- 生产环境使用独立且足够长的随机密钥。
- 首次启动会以 `BOOTSTRAP_ADMIN_USERNAME` 创建管理员，初始密码为 `APP_SHARED_SECRET`；登录后应立即修改密码。
- PWA 只在内存保存短期访问令牌，刷新令牌使用 `HttpOnly`、`Secure`、`SameSite=Strict` Cookie。
- 公网访问 `/metrics` 时必须配置 `MONITORING_TOKEN` 并发送 Bearer Token。
- Hermes API 只监听 `127.0.0.1` 或受保护的内网地址。
- 不要在 PWA 中保存百炼或 Hermes API Key。
- 定期检查 TLS 证书有效期与 TURN 凭据。
- 确认 `server/.env`、`ssl/` 私钥和日志始终处于 Git 忽略状态。
