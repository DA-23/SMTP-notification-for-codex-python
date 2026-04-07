# SMTP-notification-for-codex-python

[中文](#中文) | [English](#english)

An automation tool that sends email notifications when:

- a foreground Python task finishes in `zsh`
- an interactive Codex turn finishes with a real text answer

It uses a local persistent queue, async SMTP delivery, retry logic, a `zsh` hook, and a Codex `Stop` hook.

## 中文

### 功能

这个项目用于在本地自动发送通知邮件，覆盖两类事件：

- 你在终端里运行的前台 Python 任务结束
- 交互式 Codex 真正输出了一次文本回答

它不是“同步发邮件”，而是：

1. 先把事件写入本地队列
2. 后台 sender 异步发信
3. 失败自动重试

这样可以避免卡住终端，并保留较好的鲁棒性。

### 组件

- `config/task-notify.zsh`
  - 给交互式 `zsh` 注入 `preexec` / `precmd` 钩子
- `bin/task_notify_enqueue.py`
  - 把 Python 任务完成事件写入本地队列
- `bin/task_notify_codex_stop.py`
  - 作为 Codex `Stop` hook，只在 `last_assistant_message` 非空时入队
- `bin/task_notify_sender.py`
  - 后台 SMTP sender，负责重试和状态转移
- `lib/task_notify_common.py`
  - 公共路径、配置、队列、凭据读取逻辑
- `scripts/install.py`
  - 本地安装脚本，会把运行时文件部署到你的用户目录

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

至少要改这两个字段：

- `smtp.sender`
- `smtp.recipient`

如果你用 QQ 邮箱，通常是 `your_qq_number@qq.com`。

4. 写入 SMTP 授权码

```bash
~/.local/share/task-notify/bin/task_notify_store_qq_smtp_password.sh your_qq_number@qq.com
```

注意：这里要输入的是 SMTP 授权码，不是 QQ 登录密码。

5. 重新加载终端和 Codex

```bash
source ~/.zshrc
```

然后重启你的 Codex 会话，让新的 `Stop` hook 生效。

### 运行原理

- 安装脚本会开启 `~/.codex/config.toml` 中的 `features.codex_hooks = true`
- 会把 Codex `Stop` hook 写入 `~/.codex/hooks.json`
- 会把 sender 注册成 macOS `launchd` 用户代理

### 常见问题

#### QQ SMTP 授权码是什么

不是 QQ 登录密码。

它是 QQ 邮箱为第三方 SMTP/IMAP/POP 客户端生成的独立授权密码。你需要先在 QQ 邮箱网页版开启 SMTP 服务，然后生成授权码。

#### 为什么收不到邮件

先检查这几处：

- `~/.config/task-notify/config.json` 里的发件人和收件人是否正确
- Keychain 里存的是不是 SMTP 授权码，而不是登录密码
- `~/Library/Logs/task-notify/sender.log` 是否有 `535 Login fail` 之类的错误
- 你的 Codex 是否已经重启并重新加载了 hook

### 日志与队列

- 日志：`~/Library/Logs/task-notify/`
- 队列：`~/.local/share/task-notify/spool/`

## English

### What It Does

This project sends email notifications for two event types:

- a foreground Python command finishes in your interactive terminal
- an interactive Codex turn finishes with a real text response

It does not send email synchronously from the shell. Instead it:

1. writes an event to a local queue
2. lets a background sender process the queue
3. retries on SMTP failure

### Components

- `config/task-notify.zsh`
  - installs `preexec` / `precmd` hooks for interactive `zsh`
- `bin/task_notify_enqueue.py`
  - queues Python completion events
- `bin/task_notify_codex_stop.py`
  - Codex `Stop` hook handler; only queues when `last_assistant_message` is non-empty
- `bin/task_notify_sender.py`
  - async SMTP sender with retry logic
- `lib/task_notify_common.py`
  - shared config, queue, logging, and credential helpers
- `scripts/install.py`
  - installs the runtime into your user environment

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

4. Store your SMTP auth code

```bash
~/.local/share/task-notify/bin/task_notify_store_qq_smtp_password.sh your_qq_number@qq.com
```

Use your SMTP auth code here, not your regular QQ login password.

5. Reload your terminal and restart Codex

```bash
source ~/.zshrc
```

Then restart Codex so the `Stop` hook is reloaded.

### How It Works

- The installer enables `features.codex_hooks = true` in `~/.codex/config.toml`
- It merges a `Stop` hook into `~/.codex/hooks.json`
- It registers the sender as a macOS `launchd` user agent

### Troubleshooting

If email does not arrive, check:

- `~/.config/task-notify/config.json`
- whether your Keychain entry stores the SMTP auth code, not the mailbox password
- `~/Library/Logs/task-notify/sender.log`
- whether Codex has been restarted after hook installation

### Runtime Paths

- Logs: `~/Library/Logs/task-notify/`
- Queue: `~/.local/share/task-notify/spool/`
