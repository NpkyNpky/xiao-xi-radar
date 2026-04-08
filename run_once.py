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
ASX_HOOK = os.getenv("ASX_HOOK", "https://discord.com/api/webhooks/1491282256696967210/vEMgETPLPi8DnVCHZLn-g_1rtSMFARoHuFK1Rkq6qpgyi-u-t2deQT2GBxQ15gL-BSwh")  # ASX专属频道
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
WARLINE_KW = ["trump", "iran", "hormuz", "strait", "ceasefire", "deadline", "strike", "missile", "attack", "airstrike", "bridge", "power plant", "oil", "crude", "sanctions", "israel"]
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
请把任何语言的原文都翻译成自然中文，再给出结论。全部用中文回答。

股票：{ticker}（{WATCHLIST.get(ticker, ticker)}）
原标题：{title}
原摘要：{desc[:260]}

请严格按以下格式输出：
级别: S / A / B / C
标题中文: 翻译后的中文标题
摘要中文: 1句中文摘要
评级: 🟢偏多 或 🔴偏空 或 🟡中性
影响: 对公司基本面的影响
行动: 建议操作

分级标准：
S = 明显改变仓位/风险暴露/估值框架
A = 足够影响观察、建仓节奏、卖Put决策
B = 有信息量但暂不改变动作
C = 噪音或重复信息"""
    out = groq_call(prompt)
    if out:
        lines = out.split("\n")
        level = next((l.replace("级别:", "").strip().upper() for l in lines if "级别" in l), "B")
        zh_title = next((l.replace("标题中文:", "").strip() for l in lines if "标题中文" in l), title)
        zh_summary = next((l.replace("摘要中文:", "").strip() for l in lines if "摘要中文" in l), desc[:120] if desc else "暂无摘要")
        rating = next((l.replace("评级:", "").strip() for l in lines if "评级" in l), "🟡中性")
        impact = next((l.replace("影响:", "").strip() for l in lines if "影响" in l), "待观察")
        action = next((l.replace("行动:", "").strip() for l in lines if "行动" in l), "持续监控")
    else:
        level = "B"
        zh_title, zh_summary = title, (desc[:120] if desc else "暂无摘要")
        rating, impact, action = "🟡中性", "待观察", "持续监控"
    color = 0x00FF00 if "🟢" in rating else (0xFF0000 if "🔴" in rating else 0xFFFF00)
    return {"level": level, "title_zh": zh_title, "summary_zh": zh_summary, "rating": rating, "impact": impact, "action": action, "color": color}


def classify_intel(title, summary):
    text = (title + " " + summary).lower()
    for k in CRITICAL_KW:
        if k in text:
            return "CRITICAL"
    war_hits = sum(1 for k in WARLINE_KW if k in text)
    if war_hits >= 2:
        return "WARLINE"
    if sum(1 for k in HIGH_KW if k in text) >= 2:
        return "HIGH"
    if sum(1 for k in MARKET_KW if k in text) >= 2:
        return "MARKET"
    return None


def analyze_intel(title, summary, level):
    level_cn = {"CRITICAL": "极紧急", "WARLINE": "战情线", "HIGH": "高度关注", "MARKET": "市场动态"}.get(level, "资讯")
    prompt = f"""你是全球情报分析师，专注全球事件对美股市场的影响。
请把任何语言的原文都翻译成自然中文，再给出结论。全部用中文回答。

新闻级别：{level_cn}
原标题：{title}
原摘要：{summary[:320]}

请严格按以下格式输出：
级别: S / A / B / C
标题中文: 翻译后的中文标题
摘要中文: 1句中文摘要
影响: 🟢利多 或 🔴利空 或 🟡中性 + 一句话
板块: 最受影响的板块或资产
建议: 具体操作建议

分级标准：
S = 明显改变市场风险偏好、油价、利率预期、仓位方向
A = 足够影响观察、节奏和风控
B = 有信息量但暂不改动作
C = 普通资讯或重复消息

如果内容属于 Trump / Iran / Hormuz / ceasefire / strike / oil / deadline 这条战情线，请提高敏感度，不要轻易判成 B/C。"""
    out = groq_call(prompt)
    if out:
        lines = out.split("\n")
        level = next((l.replace("级别:", "").strip().upper() for l in lines if "级别" in l), "B")
        zh_title = next((l.replace("标题中文:", "").strip() for l in lines if "标题中文" in l), title)
        zh_summary = next((l.replace("摘要中文:", "").strip() for l in lines if "摘要中文" in l), summary[:120] if summary else "暂无摘要")
        impact = next((l.replace("影响:", "").strip() for l in lines if "影响" in l), "🟡中性 待观察")
        sector = next((l.replace("板块:", "").strip() for l in lines if "板块" in l), "待定")
        suggest = next((l.replace("建议:", "").strip() for l in lines if "建议" in l), "持续监控")
    else:
        level = "B"
        zh_title, zh_summary = title, (summary[:120] if summary else "暂无摘要")
        impact, sector, suggest = "🟡中性 待观察", "待定", "持续监控"
    color = 0xFF3333 if "🔴" in impact else (0x00CC44 if "🟢" in impact else 0xFFAA00)
    return {"level": level, "title_zh": zh_title, "summary_zh": zh_summary, "impact": impact, "sector": sector, "suggest": suggest, "color": color}


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
        if ana.get("level") not in {"S", "A"}:
            seen.add(aid)
            continue
        desc_short = ana["summary_zh"]
        tier = {"META": 1, "MSFT": 1, "NVDA": 1, "GOOGL": 1}.get(ticker, 2)
        tier_icon = {1: "🥇", 2: "🥈"}.get(tier, "🥉")
        embed = {
            "title": f"{tier_icon} 股票快讯 [{ticker}] {WATCHLIST[ticker]}",
            "description": f"**{ana['title_zh']}**\n\n{desc_short}",
            "color": ana["color"],
            "fields": [
                {"name": "⭐ 事件级别", "value": ana["level"], "inline": True},
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
                if level == "WARLINE" and ana.get("level") not in {"S", "A"}:
                    ana["level"] = "A"
                if ana.get("level") not in {"S", "A"}:
                    seen.add(uid)
                    continue
                label_map = {"CRITICAL": "🚨 极紧急", "WARLINE": "🪖 战情线", "HIGH": "⚠️ 高度关注", "MARKET": "📊 市场动态"}
                alert = "🚨 **极紧急！立即关注！**" if level == "CRITICAL" else ("🪖 **战情线更新**" if level == "WARLINE" else ("⚠️ **高度关注**" if level == "HIGH" else ""))
                embed = {
                    "title": f"{label_map.get(level, '📰 资讯')} | {feed['icon']} {feed['name']}",
                    "description": f"**{ana['title_zh']}**\n\n{ana['summary_zh']}",
                    "color": ana["color"],
                    "fields": [
                        {"name": "⭐ 事件级别", "value": ana["level"], "inline": True},
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


# ==================== ASX ETF 监控 ====================

ASX_ETF_PORTFOLIO = {
    "VHY": {"name": "Vanguard澳洲高股息ETF", "role": "核心收入引擎", "weight": "30%"},
    "VAS": {"name": "Vanguard澳洲宽基ETF", "role": "分散底仓", "weight": "15%"},
    "QPON": {"name": "BetaShares浮动利率债ETF", "role": "定海神针", "weight": "20%"},
    "VGS": {"name": "Vanguard全球发达市场ETF", "role": "增长引擎", "weight": "15%"},
    "IFRA": {"name": "全球基础设施ETF", "role": "抗通胀收益", "weight": "10%"},
    "MVB": {"name": "澳洲银行等权ETF", "role": "高Franking收益", "weight": "5%"},
    "AAA": {"name": "BetaShares现金管理ETF", "role": "再平衡弹药", "weight": "5%"},
}

ASX_ALERT_KEYWORDS = [
    "RBA", "reserve bank", "interest rate", "cash rate",  # RBA利率
    "dividend", "distribution", "ex-dividend", "ex-date",  # 分红公告
    "VHY", "VAS", "QPON", "VGS", "IFRA", "MVB", "AAA",  # 直接提到ETF
    "ASX", "asx 200", "australian market",  # 澳洲大盘
    "commonwealth bank", "CBA", "westpac", "ANZ", "NAB",  # 四大行
    "BHP", "rio tinto", "iron ore",  # 矿业
    "inflation", "CPI", "australia gdp",  # 宏观数据
    "franking", "dividend imputation",  # 税务
]

ASX_RSS_FEEDS = [
    # 澳洲财经新闻
    {"name": "路透社澳洲", "url": "https://feeds.reuters.com/reuters/AUBusinessNews", "icon": "🇦🇺"},
    {"name": "TheAge财经", "url": "https://www.theage.com.au/rss/business/markets.xml", "icon": "📊"},
    # RBA利率决议（官方）
    {"name": "RBA利率决议", "url": "https://www.rba.gov.au/rss/rss-cb-decisions.xml", "icon": "🏦"},
    {"name": "RBA新闻稿", "url": "https://www.rba.gov.au/rss/rss-cb-speeches.xml", "icon": "📢"},
    # ASX上市公司公告（四大行 + BHP）
    {"name": "CBA公告", "url": "https://www.asx.com.au/asx/1/company/CBA/announcements?count=5", "icon": "🏦"},
    {"name": "WBC公告", "url": "https://www.asx.com.au/asx/1/company/WBC/announcements?count=5", "icon": "🏦"},
    {"name": "ANZ公告", "url": "https://www.asx.com.au/asx/1/company/ANZ/announcements?count=5", "icon": "🏦"},
    {"name": "NAB公告", "url": "https://www.asx.com.au/asx/1/company/NAB/announcements?count=5", "icon": "🏦"},
    {"name": "BHP公告", "url": "https://www.asx.com.au/asx/1/company/BHP/announcements?count=5", "icon": "⛏️"},
]


def scan_asx(state):
    """扫描ASX ETF组合相关新闻和事件"""
    if not GROQ_KEY:
        return 0

    seen = set(state.get("asx", []))
    sent = 0
    now = datetime.now(timezone.utc)

    # 检查是否需要发送季度提醒（每90天一次）
    last_quarterly = state.get("asx_last_quarterly", 0)
    if (now.timestamp() - last_quarterly) > 90 * 24 * 3600:
        quarterly_embed = {
            "title": "📅 ASX ETF组合季度检查提醒",
            "description": "你的被动收入永动机组合需要季度检查了。",
            "color": 0x00AAFF,
            "fields": [
                {"name": "🔍 需要确认的事项", "value": "✅ 各ETF分红是否正常到账\n✅ NAV趋势是否健康\n✅ QPON收益率是否跟上RBA利率\n✅ VHY四大行成分有无异常", "inline": False},
                {"name": "💰 组合配置提醒",
                 "value": "\n".join([f"**{k}** {v['weight']} - {v['role']}" for k, v in ASX_ETF_PORTFOLIO.items()]),
                 "inline": False},
                {"name": "📞 年度操作", "value": "联系会计师确认Trust分配方案和税务申报", "inline": False},
            ],
            "footer": {"text": "晓犀ASX监控 | 永动机组合 | 下次提醒：90天后"}
        }
        if push(ASX_HOOK, "📅 **ASX ETF组合季度检查提醒**", [quarterly_embed]):
            state["asx_last_quarterly"] = now.timestamp()
            sent += 1

    # 扫描ASX相关新闻
    for feed_info in ASX_RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_info["url"])
            for entry in parsed.entries[:5]:
                if not is_recent_rss(entry, minutes=180):
                    continue
                raw = entry.get("id") or entry.get("link") or entry.get("title", "")
                uid = hashlib.md5(("asx_" + raw).encode("utf-8", "replace")).hexdigest()
                if uid in seen:
                    continue

                title = entry.get("title", "")
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
                text = (title + " " + summary).lower()

                # 检查是否触发关键词
                hits = [k for k in ASX_ALERT_KEYWORDS if k.lower() in text]
                if len(hits) < 2:
                    continue

                # AI分析
                prompt = f"""你是澳洲ETF被动收入组合顾问。分析以下新闻对这个组合的影响，全部用中文回答。

组合：VHY(30%) + VAS(15%) + QPON(20%) + VGS(15%) + IFRA(10%) + MVB(5%) + AAA(5%)
核心目标：每年约$47,500现金分红 + 本金温和增长

新闻标题：{title}
新闻摘要：{summary[:300]}

请按格式输出：
级别: S/A/B（S=需要立即检查仓位，A=值得关注，B=参考信息）
标题中文: 翻译
影响: 对组合的具体影响（一句话）
建议: 是否需要操作（一句话）"""

                out = groq_call(prompt, max_tokens=200)
                if not out:
                    continue

                lines = out.split("\n")
                level = next((l.replace("级别:", "").strip().upper() for l in lines if "级别" in l), "B")
                if level not in {"S", "A"}:
                    seen.add(uid)
                    continue

                zh_title = next((l.replace("标题中文:", "").strip() for l in lines if "标题中文" in l), title)
                impact = next((l.replace("影响:", "").strip() for l in lines if "影响" in l), "待分析")
                suggestion = next((l.replace("建议:", "").strip() for l in lines if "建议" in l), "持续观察")

                level_label = "🚨 需立即检查" if level == "S" else "⚠️ 值得关注"
                color = 0xFF3333 if level == "S" else 0xFFAA00

                embed = {
                    "title": f"{level_label} | {feed_info['icon']} {feed_info['name']} | ASX永动机",
                    "description": f"**{zh_title}**\n\n{summary[:250]}",
                    "color": color,
                    "fields": [
                        {"name": "📊 组合影响", "value": impact, "inline": False},
                        {"name": "🎯 建议操作", "value": suggestion, "inline": False},
                        {"name": "🔗 原文", "value": f"[点击查看]({entry.get('link', '#')})", "inline": False},
                    ],
                    "footer": {"text": f"晓犀ASX监控 | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} | 永动机组合"}
                }
                alert = "🚨 **ASX永动机组合需要关注！**" if level == "S" else "⚠️ **ASX组合参考信息**"
                if push(ASX_HOOK, alert, [embed]):
                    sent += 1
                    seen.add(uid)
                    time.sleep(1)
        except Exception as e:
            print(f"ASX RSS失败 {feed_info['name']}: {e}")

    state["asx"] = trim_state(list(seen))
    return sent


# ==================== ABS 澳洲统计局数据 ====================

ABS_DATASETS = [
    {"id": "CPI",   "name": "CPI通货膨脹",  "url": "https://api.data.abs.gov.au/data/CPI/1.50.10001.10.Q?startPeriod=2025-Q1&detail=Full"},
    {"id": "UNEMP", "name": "失业率",       "url": "https://api.data.abs.gov.au/data/LF/1.3.1599.30.M?startPeriod=2025-01&detail=Full"},
]


def fetch_abs_latest():
    results = []
    for ds in ABS_DATASETS:
        try:
            r = requests.get(ds["url"], headers={"Accept": "application/json"}, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            series = data.get("data", {}).get("dataSets", [{}])[0].get("series", {})
            if not series:
                continue
            first_series = list(series.values())[0]
            obs = first_series.get("observations", {})
            if not obs:
                continue
            latest_val = obs[sorted(obs.keys())[-1]][0]
            results.append(f"{ds['name']}: {latest_val}")
        except Exception as e:
            print(f"ABS失败 {ds['name']}: {e}")
    return results


def scan_abs(state):
    if not GROQ_KEY:
        return 0
    now = datetime.now(timezone.utc)
    if (now.timestamp() - state.get("abs_last_check", 0)) < 24 * 3600:
        return 0
    state["abs_last_check"] = now.timestamp()

    abs_data = fetch_abs_latest()
    if not abs_data:
        return 0

    data_str = "\n".join(abs_data)
    prompt = f"""你是澳洲ETF被动收入组合顾问。
最新ABS澳洲官方数据：
{data_str}

组合：VHY(30%) + VAS(15%) + QPON(20%) + VGS(15%) + IFRA(10%) + MVB(5%) + AAA(5%)

级别: S(需立即检查) / A(值得关注) / B(普通参考)
影响: 对组合各ETF的具体影响
建议: 是否需要调整仓位，全部用中文"""

    out = groq_call(prompt, max_tokens=250)
    if not out:
        return 0

    lines = out.split("\n")
    level = next((l.replace("级别:", "").strip().upper() for l in lines if "级别" in l), "B")
    if level not in {"S", "A"}:
        return 0

    impact = next((l.replace("影响:", "").strip() for l in lines if "影响" in l), "待分析")
    suggestion = next((l.replace("建议:", "").strip() for l in lines if "建议" in l), "持续观察")
    color = 0xFF3333 if level == "S" else 0xFFAA00
    label = "🚨 需立即检查" if level == "S" else "⚠️ 值得关注"

    embed = {
        "title": f"{label} | 📊 ABS官方数据更新",
        "description": f"**最新数据**\n{data_str}",
        "color": color,
        "fields": [
            {"name": "📊 对永动机组合的影响", "value": impact, "inline": False},
            {"name": "🎯 建议", "value": suggestion, "inline": False},
            {"name": "🔗 数据来源", "value": "[ABS 澳洲统计局](https://www.abs.gov.au)", "inline": False},
        ],
        "footer": {"text": f"晓犀ASX监控 | ABS官方数据 | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"}
    }
    alert = "🚨 **ABS数据重大变化！**" if level == "S" else "⚠️ **ABS宏观数据更新**"
    if push(ASX_HOOK, alert, [embed]):
        return 1
    return 0


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
    asx_sent = scan_asx(state)
    abs_sent = scan_abs(state)
    save_state(state)
    print(f"完成：股票 {stock_sent} 条，全球情报 {intel_sent} 条，ASX监控 {asx_sent} 条，ABS数据 {abs_sent} 条")


if __name__ == "__main__":
    main()
