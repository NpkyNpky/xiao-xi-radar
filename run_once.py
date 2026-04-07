# -*- coding: utf-8 -*-
import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone, timedelta

import requests
import feedparser

POLYGON_KEY = os.getenv("POLYGON_KEY", "")
GROQ_KEY = os.getenv("GROQ_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
RADAR_HOOK = os.getenv("RADAR_HOOK", "")
INTEL_HOOK = os.getenv("INTEL_HOOK", "")
STATE_FILE = os.getenv("STATE_FILE", ".state.json")

WATCHLIST = {
    "META": "Meta Platforms", "MSFT": "微软", "NVDA": "英伟达", "GOOGL": "谷歌",
    "MU": "美光科技", "AMZN": "亚马逊", "AAPL": "苹果", "LMT": "洛克希德马丁",
    "KMI": "金德摩根", "AMD": "AMD", "AVGO": "博通", "TSLA": "特斯拉", "TSM": "台积电"
}

RSS_FEEDS = [
    {"name": "路透社国际", "url": "https://feeds.reuters.com/reuters/worldNews", "icon": "📡"},
    {"name": "BBC国际", "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "icon": "🌍"},
    {"name": "半岛电视台", "url": "https://www.aljazeera.com/xml/rss/all.xml", "icon": "🕌"},
    {"name": "美联社", "url": "https://feeds.apnews.com/apf-topnews", "icon": "📰"},
    {"name": "路透社财经", "url": "https://feeds.reuters.com/reuters/businessNews", "icon": "💼"},
    {"name": "MarketWatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/", "icon": "📊"},
    {"name": "中东之眼", "url": "https://www.middleeasteye.net/rss", "icon": "🌙"},
]

CRITICAL_KW = ["nuclear", "hormuz", "strait closed", "war declared", "missile strike", "bombed", "invasion", "airstrikes", "emergency rate", "circuit breaker", "iran attack", "oil embargo"]
HIGH_KW = ["war", "conflict", "sanctions", "oil price", "crude", "opec", "fed rate", "inflation", "recession", "military", "ceasefire", "tariff", "energy crisis", "iran", "israel", "taiwan"]
MARKET_KW = ["stock market", "dow jones", "nasdaq", "s&p", "earnings", "powell", "fomc", "treasury", "gold", "bond yield", "futures"]
SIGNAL_WORDS = ["earnings", "beat", "miss", "guidance", "upgrade", "downgrade", "acquisition", "layoff", "record", "surge", "decline", "buyback", "partnership", "contract", "revenue"]


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"stocks": [], "intel": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def push(hook, content="", embeds=None):
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    r = requests.post(hook, json=payload, timeout=20)
    return r.status_code in [200, 204]


def groq_call(prompt, max_tokens=180):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.25,
        },
        timeout=30,
    )
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"].strip()
    return None


def analyze_stock(ticker, title, desc):
    prompt = f"""你是投资分析助手，代表芒格、巴菲特、Burry、Soros、林奇、Keith Gill六位大师。
分析以下股票新闻，全部用中文回答。

股票：{ticker}（{WATCHLIST.get(ticker, ticker)}）
标题：{title}
摘要：{desc[:260]}

请严格按格式输出，每行尽量短：
评级: 🟢偏多 或 🔴偏空 或 🟡中性
影响: 对公司基本面的影响
行动: 建议操作"""
    out = groq_call(prompt)
    if out:
        lines = out.split("\n")
        rating = next((l.replace("评级:", "").strip() for l in lines if "评级" in l), "🟡中性")
        impact = next((l.replace("影响:", "").strip() for l in lines if "影响" in l), "待观察")
        action = next((l.replace("行动:", "").strip() for l in lines if "行动" in l), "持续监控")
    else:
        rating, impact, action = "🟡中性", "待观察", "持续监控"
    color = 0x00FF00 if "🟢" in rating else (0xFF0000 if "🔴" in rating else 0xFFFF00)
    return {"rating": rating, "impact": impact, "action": action, "color": color}


def classify_intel(title, summary):
    text = (title + " " + summary).lower()
    for k in CRITICAL_KW:
        if k in text:
            return "CRITICAL"
    if sum(1 for k in HIGH_KW if k in text) >= 2:
        return "HIGH"
    if sum(1 for k in MARKET_KW if k in text) >= 2:
        return "MARKET"
    return None


def analyze_intel(title, summary, level):
    level_cn = {"CRITICAL": "极紧急", "HIGH": "高度关注", "MARKET": "市场动态"}.get(level, "资讯")
    prompt = f"""你是全球情报分析师，专注全球事件对美股市场的影响，全部用中文回答。

新闻级别：{level_cn}
标题：{title}
摘要：{summary[:320]}

请严格按格式输出：
影响: 🟢利多 或 🔴利空 或 🟡中性 + 一句话
板块: 最受影响的板块或资产
建议: 具体操作建议"""
    out = groq_call(prompt)
    if out:
        lines = out.split("\n")
        impact = next((l.replace("影响:", "").strip() for l in lines if "影响" in l), "🟡中性 待观察")
        sector = next((l.replace("板块:", "").strip() for l in lines if "板块" in l), "待定")
        suggest = next((l.replace("建议:", "").strip() for l in lines if "建议" in l), "持续监控")
    else:
        impact, sector, suggest = "🟡中性 待观察", "待定", "持续监控"
    color = 0xFF3333 if "🔴" in impact else (0x00CC44 if "🟢" in impact else 0xFFAA00)
    return {"impact": impact, "sector": sector, "suggest": suggest, "color": color}


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def is_recent_polygon(article, minutes=90):
    dt = parse_dt(article.get("published_utc"))
    if not dt:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(minutes=minutes)


def is_recent_rss(entry, minutes=120):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return True
    dt = datetime(*published[:6], tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc) - timedelta(minutes=minutes)


def trim_state(values, limit=800):
    if len(values) > limit:
        return values[-limit:]
    return values


def scan_stocks(state):
    if not all([POLYGON_KEY, GROQ_KEY, RADAR_HOOK]):
        raise RuntimeError("股票雷达缺少环境变量")

    seen = set(state.get("stocks", []))
    sent = 0
    r = requests.get(
        "https://api.polygon.io/v2/reference/news",
        params={
            "apiKey": POLYGON_KEY,
            "ticker.any_of": ",".join(WATCHLIST.keys()),
            "order": "desc",
            "limit": 20,
            "sort": "published_utc",
        },
        timeout=30,
    )
    articles = r.json().get("results", []) if r.status_code == 200 else []

    for art in articles:
        aid = art.get("id", "")
        if not aid or aid in seen or not is_recent_polygon(art):
            continue
        relevant = [t for t in art.get("tickers", []) if t in WATCHLIST]
        if not relevant:
            continue
        title = art.get("title", "")
        desc = art.get("description", "")
        if not any(w in (title + " " + desc).lower() for w in SIGNAL_WORDS):
            continue

        ticker = relevant[0]
        ana = analyze_stock(ticker, title, desc)
        desc_short = (desc[:220] + "...") if len(desc) > 220 else desc
        tier = {"META": 1, "MSFT": 1, "NVDA": 1, "GOOGL": 1}.get(ticker, 2)
        tier_icon = {1: "🥇", 2: "🥈"}.get(tier, "🥉")
        embed = {
            "title": f"{tier_icon} 股票快讯 [{ticker}] {WATCHLIST[ticker]}",
            "description": f"**{title}**\n\n{desc_short}",
            "color": ana["color"],
            "fields": [
                {"name": "📊 六大师评级", "value": ana["rating"], "inline": True},
                {"name": "💡 基本面影响", "value": ana["impact"], "inline": True},
                {"name": "🎯 建议操作", "value": ana["action"], "inline": False},
                {"name": "🔗 原文链接", "value": f"[点击查看]({art.get('article_url', '#')})", "inline": False},
            ],
            "footer": {"text": f"晓犀六大师雷达 | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} | GitHub Actions"},
        }
        if push(RADAR_HOOK, embeds=[embed]):
            sent += 1
            seen.add(aid)
            time.sleep(1)

    state["stocks"] = trim_state(list(seen))
    return sent


def scan_intel(state):
    if not all([GROQ_KEY, INTEL_HOOK]):
        raise RuntimeError("全球情报缺少环境变量")

    seen = set(state.get("intel", []))
    sent = 0

    for feed in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries[:8]:
                if not is_recent_rss(entry):
                    continue
                raw = entry.get("id") or entry.get("link") or entry.get("title", "")
                uid = hashlib.md5(raw.encode("utf-8", "replace")).hexdigest()
                if uid in seen:
                    continue
                title = entry.get("title", "")
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
                level = classify_intel(title, summary)
                if not level:
                    continue

                ana = analyze_intel(title, summary, level)
                label_map = {"CRITICAL": "🚨 极紧急", "HIGH": "⚠️ 高度关注", "MARKET": "📊 市场动态"}
                alert = "🚨 **极紧急！立即关注！**" if level == "CRITICAL" else ("⚠️ **高度关注**" if level == "HIGH" else "")
                embed = {
                    "title": f"{label_map.get(level, '📰 资讯')} | {feed['icon']} {feed['name']}",
                    "description": f"**{title}**\n\n{summary[:300]}",
                    "color": ana["color"],
                    "fields": [
                        {"name": "📈 市场影响", "value": ana["impact"], "inline": False},
                        {"name": "🏭 受影响板块", "value": ana["sector"], "inline": True},
                        {"name": "🎯 操作建议", "value": ana["suggest"], "inline": True},
                        {"name": "🔗 原文", "value": f"[点击查看]({entry.get('link', '#')})", "inline": False},
                    ],
                    "footer": {"text": f"晓犀全球情报 | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} | GitHub Actions"},
                }
                if push(INTEL_HOOK, content=alert, embeds=[embed]):
                    sent += 1
                    seen.add(uid)
                    time.sleep(1)
        except Exception as e:
            print(f"RSS源失败 {feed['name']}: {e}")

    state["intel"] = trim_state(list(seen))
    return sent


def main():
    missing = [k for k, v in {
        "POLYGON_KEY": POLYGON_KEY,
        "GROQ_KEY": GROQ_KEY,
        "RADAR_HOOK": RADAR_HOOK,
        "INTEL_HOOK": INTEL_HOOK,
    }.items() if not v]
    if missing:
        raise RuntimeError("缺少环境变量: " + ", ".join(missing))

    state = load_state()
    stock_sent = scan_stocks(state)
    intel_sent = scan_intel(state)
    save_state(state)
    print(f"完成：股票 {stock_sent} 条，全球情报 {intel_sent} 条")


if __name__ == "__main__":
    main()
