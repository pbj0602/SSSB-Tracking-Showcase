#!/usr/bin/env python3
"""SSSB 捡漏 bot：盯指定小区的一室户和走廊房，第一名的排队天数够得着时推送 Telegram。

页面是 JS 渲染的，所以用 Playwright 打开后按文字解析每张房源卡片。
配置全部走环境变量，改小区/天数不用动代码。
"""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

URL = "https://minasidor.sssb.se/en/available-apartments/"
AREAS = [a.strip() for a in os.getenv("AREAS", "Forum,Domus,Nyponet,Roslagstull,Mjölner,Vätan,Lucidor,Fyrtalet,Apeln,Kurland,Kungshamra,Lappkärrsberget,Jerum,Hugin,Munin,Pax,Birka,Skärmarbrink,Strix").split(",") if a.strip()]
TYPE_RE = re.compile(os.getenv("TYPE_REGEX", r"^1 (room|rum)\b"), re.I)  # 英文页面房型也是瑞典语：1 rum & pentry / kök
# 走廊房（Korridorrum / Room in corridor）单独一份小区名单，默认和一室户同一份。默认小区只是示例，换成你自己想住的
CORRIDOR_AREAS = [a.strip() for a in os.getenv("CORRIDOR_AREAS", "Forum,Domus,Nyponet,Roslagstull,Mjölner,Vätan,Lucidor,Fyrtalet,Apeln,Kurland,Kungshamra,Lappkärrsberget,Jerum,Hugin,Munin,Pax,Birka,Skärmarbrink,Strix").split(",") if a.strip()]
CORRIDOR_RE = re.compile(os.getenv("CORRIDOR_REGEX", r"korridor|corridor"), re.I)
ALL_AREAS = list(dict.fromkeys(AREAS + CORRIDOR_AREAS))
if not (os.getenv("MY_DAYS_BASE") and os.getenv("MY_DAYS_DATE")):
    sys.exit("请先设置 MY_DAYS_BASE（某一天你的排队天数）和 MY_DAYS_DATE（那一天的日期，YYYY-MM-DD）")
BASE_DAYS = int(os.environ["MY_DAYS_BASE"])                       # 某一天你的排队天数
BASE_DATE = date.fromisoformat(os.environ["MY_DAYS_DATE"])
MARGIN = int(os.getenv("MARGIN_DAYS", "60"))                      # 差多少天以内算"接近"
MIN_SIZE = float(os.getenv("MIN_SIZE", "20"))                     # 小于这个面积（m²）的不推；面积解析不出来的照推
HEARTBEAT_DAYS = int(os.getenv("HEARTBEAT_DAYS", "7"))             # 多少天发一次"还在监控"
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
DEBUG = os.getenv("DEBUG") == "1"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

# 从每个房源链接往上爬，取"只包含这一个房源"的最大容器作为卡片
EXTRACT_JS = """
() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="refid="]')) {
    if (seen.has(a.href)) continue;
    seen.add(a.href);
    let el = a;
    while (el.parentElement) {
      const p = el.parentElement;
      const refs = new Set([...p.querySelectorAll('a[href*="refid="]')].map(x => x.href));
      if (refs.size > 1) break;
      el = p;
    }
    out.push({ href: a.href, title: a.innerText.trim(), text: el.innerText });
  }
  return out;
}
"""
# 在含 « ‹ › » 的最小分页容器里找文字等于 n 的页码并打标记；楼层等字段也是数字，所以不能全页面搜
MARK_PAGE_JS = """
(n) => {
  const box = [...document.querySelectorAll('div, nav, ul')]
    .filter(el => ['«', '‹', '›', '»'].every(s => el.innerText.includes(s)))
    .sort((a, b) => a.innerText.length - b.innerText.length)[0];
  if (!box) return false;
  const hit = [...box.querySelectorAll('a, button, span, em, li')]
    .find(el => el.children.length === 0 && el.innerText.trim() === String(n));
  if (!hit || hit.tagName === 'EM') return false;   // <em> 是当前页
  document.querySelectorAll('[data-sssb-page]').forEach(el => el.removeAttribute('data-sssb-page'));
  hit.setAttribute('data-sssb-page', '1');
  return true;
}
"""


def my_days() -> int:
    today = datetime.now(ZoneInfo("Europe/Stockholm")).date()   # runner 是 UTC，凌晨会差一天
    return BASE_DAYS + (today - BASE_DATE).days


def scrape():
    cards, xhr, total = {}, [], None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="en-GB")
        page.on("response", lambda r: xhr.append(f"{r.status} {r.url}")
                if r.request.resource_type in ("xhr", "fetch") else None)
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightTimeout:   # 同翻页：网站请求不停时等不到 idle，改等列表文字出现
            print("首页：等 networkidle 超时，继续")
            try:
                page.wait_for_function("/total of \\d+/.test(document.body.innerText)", timeout=30_000)
            except PlaywrightTimeout:
                print("首页：30 秒内没看到 'total of N'，继续")
        page.wait_for_timeout(2_000)

        m = re.search(r"total of (\d+)", page.inner_text("body"))
        total = int(m.group(1)) if m else None

        for c in page.evaluate(EXTRACT_JS):
            cards[c["href"]] = c
        for n in range(2, 41):  # 翻页上限
            if not page.evaluate(MARK_PAGE_JS, n):
                break
            before = len(cards)
            page.click("[data-sssb-page]")
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeout:   # 网站偶尔一直有请求不停，等不到 idle 也照样往下读
                print(f"第 {n} 页：等 networkidle 超时，继续")
            page.wait_for_timeout(1_500)
            for c in page.evaluate(EXTRACT_JS):
                cards[c["href"]] = c
            print(f"第 {n} 页：累计 {len(cards)} 套")
            if len(cards) == before:
                break

        if DEBUG or (total and not cards):
            Path("debug").mkdir(exist_ok=True)
            Path("debug/page.html").write_text(page.content(), encoding="utf-8")
            Path("debug/xhr.txt").write_text("\n".join(xhr), encoding="utf-8")
            page.screenshot(path="debug/page.png", full_page=True)
        browser.close()
    return list(cards.values()), total


def parse(card):
    text = card["text"]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    area = next((a for a in ALL_AREAS if re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text)), None)
    q = re.search(r"(?m)^[ \t]*(\d+)?[ \t]*\([ \t]*(\d+)[ \t]*st[ \t]*\)", text)
    applicants = int(q.group(2)) if q else None
    top = int(q.group(1)) if q and q.group(1) else (0 if applicants == 0 else None)
    rent = re.search(r"(\d[\d \u00a0\u202f]*)\s*(?:SEK|kr)\b", text, re.I)  # 不跨行，防止把上一行的门牌号吞进来
    size = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m²|m2|kvm|sqm)", text, re.I)
    move = re.search(r"\d{4}-\d{2}-\d{2}", text)
    addr = next((l for l in lines if re.search(r"/\s*\d+\s*$", l)), None)
    notes = [l for l in lines if re.search(r"mån|hyra|included|ingår|korttid|short|max|möbler|furnished", l, re.I)]
    return {
        "key": f"{addr or card['href']}|{move.group(0) if move else ''}",
        "type": card["title"] or (lines[0] if lines else ""),
        "area": area, "addr": addr, "href": card["href"],
        "rent": re.sub(r"\s+", " ", rent.group(1)).strip() if rent else "?",
        "size": size.group(1) if size else "?",
        "move": move.group(0) if move else "?",
        "top": top, "applicants": applicants, "notes": notes,
    }


def big_enough(l):
    try:
        return float(l["size"].replace(",", ".")) >= MIN_SIZE
    except ValueError:
        return True   # 面积没解析出来就不过滤，宁可多推


def is_watched(l):
    if not big_enough(l):
        return False
    if l["area"] in AREAS and TYPE_RE.search(l["type"]):
        return True
    return l["area"] in CORRIDOR_AREAS and bool(CORRIDOR_RE.search(l["type"]))


def tier(l, mine):
    if l["top"] is None:
        return None
    if l["top"] < mine:
        return "green"
    if l["top"] == mine:
        return "tie"
    if l["top"] <= mine + MARGIN:
        return "yellow"
    return None


LABEL = {"green": "🟢 你目前排第一", "tie": "🟠 和第一名同天数（先注册者优先）", "yellow": "🟡 接近"}


def fmt(l, t, mine):
    gap = "" if t != "yellow" else f"，差 {l['top'] - mine} 天"
    extra = f"\n⚠️ {' / '.join(l['notes'])}" if l["notes"] else ""
    return (f"{LABEL[t]}{gap}\n{l['area']} · {l['type']}\n"
            f"💰 {l['rent']} SEK/月 · 📐 {l['size']} m²\n"
            f"{l['addr'] or ''}\n"
            f"入住 {l['move']} · 第一名 {l['top']} 天（{l['applicants']}人）· 你 {mine} 天{extra}\n{l['href']}")


def send(msg):
    if not (TG_TOKEN and TG_CHAT):
        print("[no telegram configured]\n" + msg)
        return
    for i in range(0, len(msg), 3900):  # Telegram 单条 4096 字符上限
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": msg[i:i + 3900],
                                "disable_web_page_preview": True}, timeout=20)
        r.raise_for_status()


def load_state():
    """state.json：{"listings": {key: 档位}, "last_heartbeat": "YYYY-MM-DD"}；兼容旧的 {key: 档位}。"""
    raw = json.loads(STATE_FILE.read_text() or "{}") if STATE_FILE.exists() else {}
    if "listings" not in raw:
        raw = {"listings": raw}
    raw.setdefault("last_heartbeat", None)
    return raw


def main():
    mine = my_days()
    cards, total = scrape()
    listings = [parse(c) for c in cards]
    watched = [l for l in listings if is_watched(l)]
    print(f"页面总数 {total}，解析 {len(listings)} 套，符合小区+房型+≥{MIN_SIZE:g}m² {len(watched)} 套，你的天数 {mine}")
    for l in watched:
        print(f"  {l['area']:8} {l['type']:28} {l['size']}m² {l['rent']}SEK top={l['top']} n={l['applicants']} {l['addr']}")

    if total and not listings:
        send(f"⚠️ SSSB bot：页面显示 {total} 套房源但一套都没解析出来，页面结构可能变了。去 Actions 下载 debug artifact 看看。")
        sys.exit(1)

    state = load_state()
    old, new, msgs = state["listings"], {}, []
    for l in watched:
        t = tier(l, mine)
        if not t:
            continue
        new[l["key"]] = t
        if old.get(l["key"]) != t:   # 新出现或档位变化才推
            msgs.append(fmt(l, t, mine))

    if msgs:
        send(f"🏠 SSSB 捡漏提醒（{len(msgs)} 条）\n\n" + "\n\n".join(msgs)
             + "\n\n提醒：同时排第一会随机给一套；拒绝第二次排队天数清零。")

    today = datetime.now(ZoneInfo("Europe/Stockholm")).date()
    last = state["last_heartbeat"]
    if not last or (today - date.fromisoformat(last)).days >= HEARTBEAT_DAYS:
        send(f"💓 SSSB bot 监控正常\n页面总数 {total}，解析 {len(listings)} 套\n"
             f"符合小区+房型+≥{MIN_SIZE:g}m² {len(watched)} 套\n你当前排队 {mine} 天")
        last = today.isoformat()

    STATE_FILE.write_text(json.dumps({"listings": new, "last_heartbeat": last},
                                     ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
