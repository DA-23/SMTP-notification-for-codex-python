# SMTP-notification-for-codex-python

[中文](#中文) | [English](#english)

An automation tool that sends email notifications when:

- a foreground Python task finishes
- an interactive Codex turn finishes with a real text answer

It uses a local persistent queue, async SMTP delivery, retry logic, and a platform-specific Codex integration path:

- macOS: native Codex `Stop` hook
- Windows: a background watcher over `~/.codex/sessions/*.jsonl`

## 中文

### 功能

这个项目用于在本地自动发送通知邮件，覆盖两类事件：

- 终端里的前台 Python 任务结束
- 交互式 Codex 真正输出了一次文本回答

它不是同步发邮件，而是：

1. 先把事件写入本地队列
2. 后台 sender 异步发信
3. 失败自动重试

### 平台支持

- macOS
  - 使用仓库原始方案：`zsh` 钩子 + Codex `Stop` hook + `launchd`
- Windows
  - Codex CLI 在 Windows 上的原生 lifecycle hook 当前不可用
  - 因此这里采用替代方案：后台 watcher 监听 `~/.codex/sessions/*.jsonl` 中的 `task_complete`
  - sender 和 watcher 在登录 Windows 后自动启动

### 组件

- `config/task-notify.zsh`
  - 给交互式 `zsh` 注入 `preexec` / `precmd`
- `bin/task_notify_enqueue.py`
  - 把 Python 任务完成事件写入本地队列
- `bin/task_notify_codex_stop.py`
  - macOS 上作为 Codex `Stop` hook，只在 `last_assistant_message` 非空时入队
- `bin/task_notify_codex_session_watcher.py`
  - Windows 替代方案：监听 `~/.codex/sessions/*.jsonl` 里的 `task_complete` 并入队
- `bin/task_notify_sender.py`
  - 后台 SMTP sender，负责重试和状态转移
- `lib/task_notify_common.py`
  - 公共路径、配置、队列、日志、锁与凭据读取逻辑
- `scripts/install.py`
  - 按平台安装运行时文件并接入后台启动方式

### 安装

1. 克隆仓库

```bash
git clone git@github.com:DA-23/SMTP-notification-for-codex-python.git
cd SMTP-notification-for-codex-python
```

2. 运行安装脚本

```bash
python3 scripts/install.py
```

3. 编辑配置文件

```bash
vim ~/.config/task-notify/config.json
```

至少修改：

- `smtp.sender`
- `smtp.recipient`

4. 配置 SMTP 授权码

macOS：

```bash
~/.local/share/task-notify/bin/task_notify_store_qq_smtp_password.sh your_qq_number@qq.com
```

Windows：

在 `~/.config/task-notify/credentials.env` 里写入：

```env
TASK_NOTIFY_SMTP_PASSWORD=你的SMTP授权码
```

这里用的是 SMTP 授权码，不是邮箱登录密码。

5. 完成平台相关步骤

macOS：

```bash
source ~/.zshrc
```

然后重启 Codex，让新的 `Stop` hook 生效。

Windows：

安装脚本会部署 sender 和 watcher。watcher 首次启动时只记录当前 session 位置，不回放历史会话；之后会持续监听新的 `task_complete` 事件并走同一条发信队列。

### 运行原理

- macOS
  - 安装脚本会开启 `~/.codex/config.toml` 里的 `features.codex_hooks = true`
  - 会把 Codex `Stop` hook 写入 `~/.codex/hooks.json`
  - 会把 sender 注册成 `launchd` 用户代理
- Windows
  - 不依赖 Codex 原生 hook
  - 会部署 sender 和 `task_notify_codex_session_watcher.py`
  - 会优先尝试计划任务；如果失败，则退回到 Startup 启动项

### 常见问题

#### QQ SMTP 授权码是什么

不是 QQ 登录密码。

它是 QQ 邮箱给第三方 SMTP/IMAP/POP 客户端生成的独立授权码。你需要先在 QQ 邮箱网页端开启 SMTP 服务，然后生成授权码。

#### 为什么收不到邮件

先检查：

- `~/.config/task-notify/config.json` 里的发件人和收件人是否正确
- SMTP 授权码是否正确
- `~/Library/Logs/task-notify/sender.log` 是否有 `535 Login fail` 之类的错误
- macOS 下是否已经重启 Codex 并重新加载 hook
- Windows 下是否有 `codex_watcher enqueued ...` 日志，说明 watcher 已经抓到会话完成事件

### 日志与队列

- 日志：`~/Library/Logs/task-notify/`
- 队列：`~/.local/share/task-notify/spool/`

## English

### What It Does

This project sends email notifications for two event types:

- a foreground Python command finishes
- an interactive Codex turn finishes with a real text response

It does not send email synchronously. Instead it:

1. writes an event to a local queue
2. lets a background sender process the queue
3. retries on SMTP failure

### Platform Support

- macOS
  - Uses the original design: `zsh` hooks, Codex `Stop` hook, and `launchd`
- Windows
  - Native Codex lifecycle hooks are currently unavailable in the Windows CLI
  - This repository therefore uses a fallback watcher over `~/.codex/sessions/*.jsonl`
  - The sender and watcher are started automatically after Windows sign-in

### Components

- `config/task-notify.zsh`
  - installs `preexec` / `precmd` hooks for interactive `zsh`
- `bin/task_notify_enqueue.py`
  - queues Python completion events
- `bin/task_notify_codex_stop.py`
  - macOS Codex `Stop` hook handler; only queues when `last_assistant_message` is non-empty
- `bin/task_notify_codex_session_watcher.py`
  - Windows fallback watcher that reads `~/.codex/sessions/*.jsonl` and queues `task_complete` events
- `bin/task_notify_sender.py`
  - async SMTP sender with retry logic
- `lib/task_notify_common.py`
  - shared config, queue, logging, locking, and credential helpers
- `scripts/install.py`
  - installs the runtime using the platform-appropriate integration path

### Installation

1. Clone the repository

```bash
git clone git@github.com:DA-23/SMTP-notification-for-codex-python.git
cd SMTP-notification-for-codex-python
```

2. Run the installer

```bash
python3 scripts/install.py
```

3. Edit the config

```bash
vim ~/.config/task-notify/config.json
```

At minimum, update:

- `smtp.sender`
- `smtp.recipient`

4. Configure your SMTP auth code

macOS:

```bash
~/.local/share/task-notify/bin/task_notify_store_qq_smtp_password.sh your_qq_number@qq.com
```

Windows:

Put this in `~/.config/task-notify/credentials.env`:

```env
TASK_NOTIFY_SMTP_PASSWORD=your_smtp_auth_code
```

Use your SMTP auth code here, not your mailbox password.

5. Finish the platform-specific setup

macOS:

```bash
source ~/.zshrc
```

Then restart Codex so the `Stop` hook is reloaded.

Windows:

The installer deploys the sender and watcher. On first start the watcher seeds its offsets from the current session files without replaying old conversations, then keeps watching new `task_complete` events and feeds them into the same mail queue.

### How It Works

- macOS
  - enables `features.codex_hooks = true` in `~/.codex/config.toml`
  - merges a `Stop` hook into `~/.codex/hooks.json`
  - registers the sender as a `launchd` user agent
- Windows
  - does not rely on native Codex hooks
  - deploys the sender and `task_notify_codex_session_watcher.py`
  - prefers Scheduled Tasks and falls back to Startup entries

### Troubleshooting

If email does not arrive, check:

- `~/.config/task-notify/config.json`
- whether the SMTP auth code is correct
- `~/Library/Logs/task-notify/sender.log`
- on macOS, whether Codex was restarted after hook installation
- on Windows, whether `sender.log` contains `codex_watcher enqueued ...`

### Runtime Paths

- Logs: `~/Library/Logs/task-notify/`
- Queue: `~/.local/share/task-notify/spool/`
