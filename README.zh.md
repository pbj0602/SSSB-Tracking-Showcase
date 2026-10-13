# SSSB 捡漏 bot

[English](README.md) | **中文**

> 📌 **展示版。** 本仓库是定期发布的快照，日常开发在私有仓库进行，因此这里的提交记录对应版本发布，而非日常迭代。仓库不包含我的 `state.json`，新拷贝从空状态开始。

一个 GitHub Actions 机器人：盯 [SSSB](https://minasidor.sssb.se/en/available-apartments/)（斯德哥尔摩学生公寓）的房源，当我想要的房型出现、而且排第一的人排队天数和我差不多或比我少时，推送 Telegram。

SSSB 把房子分给排队天数最多的人，房源页会显示当前第一名的天数。所以真正有用的问题不是"有没有房"，而是"有没有我抢得到的房"。

## 工作方式

1. `sssb_watch.py` 用 Playwright 打开房源页（页面是 JS 渲染的），翻完所有页，把每张房源卡片按文字解析出：小区、房型、面积、房租、入住日期、第一名的排队天数和申请人数。
2. 只保留关注小区里的一室户（`1 rum & pentry / kök`）或走廊房，且面积不小于 `MIN_SIZE` m²。
3. 拿第一名的天数和我的天数比较。我的天数由一个已知日期的天数（`MY_DAYS_DATE` 那天是 `MY_DAYS_BASE` 天）加上之后过去的天数算出：

   | 标记 | 含义 |
   |---|---|
   | 🟢 | 我现在排第一 |
   | 🟠 | 和第一名同天数（先注册者优先） |
   | 🟡 | 第一名领先不超过 `MARGIN_DAYS` 天 |

4. 房源第一次出现或档位变化时才推送，同一套房不会重复推。档位存在 `state.json`，由 workflow 提交回仓库。
5. 每 `HEARTBEAT_DAYS` 天发一条"监控正常"和统计数字，所以没消息说明没有合适的房，而不是 bot 坏了。如果页面显示有房源但一套都没解析出来，会发告警并让这次运行失败。

### 运行时间

SSSB 每周分两批放房，所以在每批截止前和下一批刚发布后各跑一次，另外每天兜底一次，接住批次之外临时放出的房：

| Cron（UTC） | 斯德哥尔摩时间（夏令时 / 冬令时） | 目的 |
|---|---|---|
| `30 6 * * 4` | 周四 08:30 / 07:30 | 周一批截止前最后看一眼 |
| `15 9 * * 4` | 周四 11:15 / 10:15 | 周四批刚发布 |
| `30 12 * * 1` | 周一 14:30 / 13:30 | 周四批截止前最后看一眼 |
| `15 15 * * 1` | 周一 17:15 / 16:15 | 周一批刚发布 |
| `0 16 * * *` | 每天 18:00 / 17:00 | 兜底，接住临时放出和重新发布的房 |

## 自己部署

1. Fork 本仓库。Fork 出来的仓库默认关闭定时任务，要在 **Actions** 页面手动启用。
2. **Settings → Secrets and variables → Actions** 里添加 secrets：

   | Secret | 值 |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | 从 [@BotFather](https://t.me/BotFather) 拿到的 bot token |
   | `TELEGRAM_CHAT_ID` | 先给 bot 发一条消息，再打开 `https://api.telegram.org/bot<TOKEN>/getUpdates` 读 `message.chat.id` |

3. 添加 variables。前两个必填，不设置运行会直接停止：

   | Variable | 默认值 | 含义 |
   |---|---|---|
   | `MY_DAYS_BASE` | 必填 | 你在 `MY_DAYS_DATE` 那天的排队天数（在 sssb.se 的 My Pages 查看） |
   | `MY_DAYS_DATE` | 必填 | 读 `MY_DAYS_BASE` 的日期 |
   | `AREAS` | 一份示例小区列表 | 一室户关注的小区，逗号分隔 |
   | `CORRIDOR_AREAS` | 同 `AREAS` | 走廊房关注的小区，逗号分隔 |
   | `MARGIN_DAYS` | `60` | 落后第一名多少天以内算"接近" |
   | `MIN_SIZE` | `20` | 最小面积（m²），面积解析不出来的照推 |

4. **Settings → Actions → General → Workflow permissions** 选 **Read and write permissions**，否则无法提交 `state.json`。

## 运行

- **定时：** 见上表。
- **手动：** Actions 页面 → *SSSB watch* → *Run workflow*。勾选 *debug* 会把页面 HTML、截图和 XHR 列表存成 artifact。

本地运行：

```sh
pip install -r requirements.txt
python -m playwright install chromium
python sssb_watch.py            # 没配 Telegram 时直接打印消息
DEBUG=1 python sssb_watch.py    # 额外写出 debug/page.html、debug/page.png、debug/xhr.txt
```
