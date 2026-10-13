# SSSB Tracking

**English** | [中文](README.zh.md)

> 📌 **Showcase snapshot.** This repository is released periodically from a private one where active development happens, so its commit history reflects releases rather than day-to-day work. It ships without my `state.json`: a fresh copy starts from an empty state.

A GitHub Actions bot that watches [SSSB](https://minasidor.sssb.se/en/available-apartments/)
(Stockholm student housing) and sends a Telegram message when a room I want comes up and
the applicant at the top of its queue has about as many queue days as I do, or fewer.

SSSB gives each room to the applicant with the most queue days, and the listing page shows
the current leader's days. So the useful question isn't "is there a room?" but "is there a
room I could actually win?"

## How it works

1. `sssb_watch.py` opens the listing page in Playwright (it's rendered with JavaScript),
   walks every page and reads each listing card as text: area, room type, size, rent,
   move-in date, the leader's queue days and the number of applicants.
2. It keeps listings in the watched areas that are either a one-room apartment
   (`1 rum & pentry / kök`) or a corridor room, and at least `MIN_SIZE` m².
3. It compares the leader's days with mine, which it works out from one known
   date and count (`MY_DAYS_BASE` on `MY_DAYS_DATE`) plus the days since:

   | Tag | Meaning |
   |---|---|
   | 🟢 | I'd be first right now |
   | 🟠 | Same days as the leader (whoever registered first wins) |
   | 🟡 | The leader is ahead by at most `MARGIN_DAYS` |

4. A listing is sent when it first shows up or when its tag changes, so a room isn't sent
   again unless it moves to a different tag. The tags are saved in `state.json`, which
   the workflow commits back to the repo.
5. Every `HEARTBEAT_DAYS` days it sends a "still running" message with the counts, so a
   silent bot means nothing matched, not that it broke. If the page shows listings but none
   could be parsed, it sends a warning and the run fails.

Messages are in Chinese.

### Schedule

SSSB publishes rooms in two weekly batches, so the workflow runs just before each batch
closes and just after the next one opens, plus a daily catch-all for rooms released
outside the batches:

| Cron (UTC) | Stockholm time | Why |
|---|---|---|
| `30 6 * * 4` | Thu 08:30 / 07:30 | last look before the Monday batch closes |
| `15 9 * * 4` | Thu 11:15 / 10:15 | the Thursday batch has just been published |
| `30 12 * * 1` | Mon 14:30 / 13:30 | last look before the Thursday batch closes |
| `15 15 * * 1` | Mon 17:15 / 16:15 | the Monday batch has just been published |
| `0 16 * * *` | daily 18:00 / 17:00 | catches rooms released or re-released between batches |

## Setup

1. Fork this repo. Forks have scheduled workflows turned off: open the **Actions** tab and
   enable them.
2. Under **Settings → Secrets and variables → Actions**, add the secrets:

   | Secret | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
   | `TELEGRAM_CHAT_ID` | Your chat ID. Message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `message.chat.id` |

3. Add variables for your own queue. The first two are required; the run stops without them:

   | Variable | Default | Meaning |
   |---|---|---|
   | `MY_DAYS_BASE` | required | your queue days on `MY_DAYS_DATE` (from My Pages on sssb.se) |
   | `MY_DAYS_DATE` | required | the date you read `MY_DAYS_BASE` |
   | `AREAS` | an example list of areas | comma-separated areas to watch for one-room apartments |
   | `CORRIDOR_AREAS` | same as `AREAS` | comma-separated areas to watch for corridor rooms |
   | `MARGIN_DAYS` | `60` | how far behind the leader still counts as close |
   | `MIN_SIZE` | `20` | smallest size in m² (listings with no readable size are kept) |

4. Under **Settings → Actions → General → Workflow permissions**, choose **Read and write
   permissions** so the workflow can commit `state.json`.

## Running

- **Scheduled:** see the table above.
- **Manually:** Actions tab → *SSSB watch* → *Run workflow*. Tick *debug* to save the page
  HTML, a screenshot and the list of XHR requests as a run artifact.

Locally:

```sh
pip install -r requirements.txt
python -m playwright install chromium
python sssb_watch.py            # without Telegram secrets it prints the messages instead
DEBUG=1 python sssb_watch.py    # also writes debug/page.html, debug/page.png, debug/xhr.txt
```
