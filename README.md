# DDNS-Watchdog

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![Dependencies](https://img.shields.io/badge/dependencies-none-success)

Windows 上的 DDNS（动态域名解析）更新工具，支持 **腾讯云 API 密钥** 与 **DNSPod API 令牌** 两种认证。

公网 IP 变了就改解析；检测接口全挂、甚至整机网络卡死时，它会自己换源、退避，并尝试把网卡重连回来。

针对 [ddns-go](https://github.com/jeessy2/ddns-go) 在弱网环境下的两个常见问题做了加固：**HTTP 检测源超时后反复失败**，以及 **网卡卡死后只能手动拔插网线**。

---

## 它解决什么

| 常见问题 | DDNS-Watchdog 的做法 |
|---|---|
| HTTP 检测接口超时或解析失败 | 先用 STUN（UDP）直接取公网 IP，不走 HTTP 和 DNS；再备 7 个 HTTP 源，失败自动换源并降权 |
| 网络抖动时不停重试、刷屏 | 指数退避，失败后逐步拉长间隔，最长 30 分钟 |
| 网卡卡死，只能拔插网线 | 全部检测源失败后自动 `flushdns`，再执行 `ipconfig /release /renew` |
| DNS 记录被改成别的地址 | 定期核对云端记录，发现漂移自动改回 |
| 系统代理把代理出口 IP 写进解析 | 所有检测与 API 请求强制直连，绕过系统代理 |

## 特性

- **多源检测**：3 个 STUN 服务器（UDP 3478，不依赖本机 DNS/HTTP）+ 7 个 HTTP 接口，按历史成功率动态排序，坏源自动降权排到队尾
- **纯 IPv4 源优先**：双栈网络下避免把 IPv6 地址写进 A 记录
- **交叉确认**：IP 变化时用第二个源复核，降低单源误报
- **指数退避**：连续失败后逐步拉长检查间隔，避免刷屏式请求
- **网络自愈**：检测源连续全失败后执行 `flushdns`，可选 `ipconfig /release /renew`
- **漂移自愈**：云端记录被别人改掉时自动改回
- **状态缓存**：`state.json` 记录上次写入的 IP，IP 没变不调用修改接口
- **运行中可配置**：监控时按 `Q` 进入设置菜单，无需重启
- **零依赖**：纯 Python 标准库实现，可打包成单个 exe

---

## 快速开始

### 方式一：下载 exe（推荐，免安装 Python）

1. 打开 [Releases](https://github.com/Mark136880/DDNS-Watchdog/releases/latest)，下载 `DDNS-Watchdog.exe`
2. 放到一个固定目录（例如 `D:\DDNS\`）
3. **右键以管理员身份运行**（自愈里的 `ipconfig /renew` 需要足够权限）
4. 首次运行进入配置向导，按提示填写认证信息和域名
5. 配置完成后按提示选择是否立即启动监控

### 方式二：运行源码

需要 Python 3.8 及以上，无第三方依赖。

```powershell
python ddns_client.py
```

### 方式三：自己打包 exe

```powershell
pip install pyinstaller
pyinstaller --onefile --console --clean --noconfirm --name DDNS-Watchdog ddns_client.py
```

产物在 `dist\DDNS-Watchdog.exe`。

---

## 首次配置向导

第一次运行会依次询问：

1. **认证方式**
   - `[1]` 腾讯云 API 密钥：在 [API 密钥管理](https://console.cloud.tencent.com/cam/capi) 创建，并授予 DNSPod 记录读写权限
   - `[2]` DNSPod API 令牌：在 [DNSPod Token](https://console.dnspod.cn/account/token/token) 创建，格式为 `ID,Token`
2. **主域名**：例如 `example.com`
3. **DDNS 主机名**：直接回车使用 `@`（即根域名本身）；填 `ddns` 则维护 `ddns.example.com`
4. **检查间隔**（秒，默认 300）

配置保存在程序同目录的 `config.json`。已有配置时直接回车即可保留原值。

也可以先复制示例再手工编辑：

```powershell
Copy-Item config.example.json config.json
```

> 密钥在向导中输入时会以掩码显示，但最终是以明文存放在 `config.json` 中，请妥善保管该文件，不要提交到仓库或发送给别人。

随时重新进入向导（修改密钥、域名、间隔）：

```powershell
python ddns_client.py --setup
```

---

## 运行中修改配置

监控运行中按 `Q`（`S` 同样有效）：

```text
[1] 重新配置密钥/域名
[2] 修改检查间隔
[3] 立即执行一次检查
[4] 返回监控
[0] 退出程序
```

停止运行：按 `Q` 后选 `0`，或直接 `Ctrl+C`。

---

## 开机自启

以管理员打开 PowerShell，把路径换成实际位置：

```powershell
schtasks /Create /TN "DDNS-Watchdog" /TR "D:\DDNS\DDNS-Watchdog.exe" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
```

使用 SYSTEM 账户后，开机即启动、无需登录，网卡重连也有权限。任务计划环境下建议设置 `DDNS_WATCHDOG_NOPAUSE=1`，避免程序在无人值守时停在「按回车退出」。

取消自启：

```powershell
schtasks /Delete /TN "DDNS-Watchdog" /F
```

---

## 配置说明

`config.json` 首次运行自动生成，也可以从 [`config.example.json`](config.example.json) 复制。

| 字段 | 默认 | 说明 |
|---|---|---|
| `provider` | `tencent` | `tencent` 使用 SecretId/SecretKey；`dnspod` 使用 `ID,Token` |
| `secret_id` / `secret_key` | 空 | 腾讯云 API 密钥（`provider=tencent` 时必填） |
| `login_token` | 空 | DNSPod API 令牌，格式 `ID,Token`（`provider=dnspod` 时必填） |
| `domain` | — | 主域名，如 `example.com` |
| `sub_domain` | `@` | 主机名，`@` 表示根域名 |
| `check_interval_seconds` | `300` | 正常时每 5 分钟检查一次 |
| `max_interval_seconds` | `1800` | 失败后退避的最长间隔 |
| `heal_after_failures` | `1` | 连续多少轮全部检测源失败后触发网卡重连 |
| `auto_renew_adapter` | `true` | 自愈时是否执行 release/renew。会短暂断网数秒，不想自动断网就改为 `false` |
| `stun_servers` | 腾讯 / 小米 / Cloudflare | UDP 3478，基本不受本机 DNS 故障影响 |
| `http_sources` | 7 个 | HTTP 备用检测源，按历史成功率动态排序 |

### 环境变量

| 变量 | 作用 |
|---|---|
| `DDNS_WATCHDOG_DRYRUN=1` | 走完检测流程，但绝不修改 DNS。在另一台机器上试用时务必打开，避免和正在运行的实例互相改同一条记录 |
| `DDNS_WATCHDOG_NOPAUSE=1` | 出错退出时不等待回车，适合任务计划等无人值守场景 |

---

## 工作流程

```text
每轮检查
  ├─ STUN 取公网 IP；失败则按成功率尝试 HTTP 源
  ├─ 取不到 IP
  │    └─ 累计失败达到 heal_after_failures
  │         └─ flushdns + ipconfig /release /renew，然后拉长重试间隔
  ├─ IP 与上次相同 → 核对云端记录，漂移则改回
  └─ IP 变化 → 第二个源确认 → 创建或修改 A 记录 → 写入 state.json
```

日志写在 `logs\ddns.log`，同时输出到控制台。`state.json` 只记录上次成功写进去的 IP，避免每次都调用修改接口。

---

## 常见问题

**日志里反复出现「检测到 DNS 记录漂移」？**

最可能是有**第二个实例**在别处运行（另一台电脑、或残留进程）在同时维护同一条记录，两边互相改。先确认是不是只有一台机器在跑：

```powershell
tasklist | findstr /i "DDNS-Watchdog"
```

同一域名下的 A 记录只应该由一个实例维护。

**认证失败怎么办？**

程序会打印排查提示。确认认证方式与凭据类型匹配：腾讯云要用 API 密钥（SecretId/SecretKey），DNSPod 要用 API 令牌（`ID,Token`，ID 在前）。改完运行 `DDNS-Watchdog.exe --setup`。

**自愈会把我网络弄断吗？**

`auto_renew_adapter` 为 `true` 时，检测源全失败会主动断开并重连网卡，期间断网数秒。不想自动断网，把它改成 `false`。

**IP 没变会不会一直调 API？**

IP 未变化时只做一次记录核对查询，不会调用修改接口。

**只想先试试，不想动线上记录？**

先设 `DDNS_WATCHDOG_DRYRUN=1` 再运行，走完检测流程但绝不写 DNS。

---

## 注意

- 同一条解析记录只应该有一个实例在维护。两台电脑同时运行会互相覆盖记录。
- `auto_renew_adapter` 为 `true` 时，检测源全失败会主动断开并重连网卡。
- 不要把填好密钥的 `config.json` 提交到仓库或发给别人。
- 建议以管理员身份运行，否则自愈里的 `ipconfig /renew` 可能失败。

## 文件

| 文件 | 说明 |
|---|---|
| `ddns_client.py` | 程序源码 |
| `config.example.json` | 配置模板，不含密钥 |
| `config.json` | 本机配置，首次运行生成，已在 `.gitignore` 中排除 |
| `state.json` | 上次 IP，自动生成 |
| `logs/ddns.log` | 运行日志 |

## 许可

[MIT](LICENSE)
