# -*- coding: utf-8 -*-
import os
import requests, feedparser, schedule, time, threading, hashlib, re
from datetime import datetime

POLYGON_KEY = os.getenv("POLYGON_KEY", "")
GROQ_KEY = os.getenv("GROQ_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
RADAR_HOOK = os.getenv("RADAR_HOOK", "")
INTEL_HOOK = os.getenv("INTEL_HOOK", "")

WATCHLIST = {
    "META":"Meta Platforms","MSFT":"微软","NVDA":"英伟达","GOOGL":"谷歌",
    "MU":"美光科技","AMZN":"亚马逊","AAPL":"苹果","LMT":"洛克希德马丁",
    "KMI":"金德摩根","AMD":"AMD","AVGO":"博通","TSLA":"特斯拉","TSM":"台积电"
}

RSS_FEEDS = [
    {"name":"路透社国际","url":"https://feeds.reuters.com/reuters/worldNews","icon":"📡"},
    {"name":"BBC国际","url":"http://feeds.bbci.co.uk/news/world/rss.xml","icon":"🌍"},
    {"name":"半岛电视台","url":"https://www.aljazeera.com/xml/rss/all.xml","icon":"🕌"},
    {"name":"美联社","url":"https://feeds.apnews.com/apf-topnews","icon":"📰"},
    {"name":"路透社财经","url":"https://feeds.reuters.com/reuters/businessNews","icon":"💼"},
    {"name":"MarketWatch","url":"https://feeds.marketwatch.com/marketwatch/topstories/","icon":"📊"},
    {"name":"中东之眼","url":"https://www.middleeasteye.net/rss","icon":"🌙"},
]

CRITICAL_KW = ["nuclear","hormuz","strait closed","war declared","missile strike","bombed","invasion","airstrikes","emergency rate","circuit breaker","iran attack","oil embargo"]
HIGH_KW = ["war","conflict","sanctions","oil price","crude","opec","fed rate","inflation","recession","military","ceasefire","tariff","energy crisis","iran","israel","taiwan"]
MARKET_KW = ["stock market","dow jones","nasdaq","s&p","earnings","powell","fomc","treasury","gold","bond yield","futures"]
SIGNAL_WORDS = ["earnings","beat","miss","guidance","upgrade","downgrade","acquisition","layoff","record","surge","decline","buyback","partnership","contract","revenue"]

stock_seen = set()
intel_seen = set()

def push(hook, content="", embeds=None):
    try:
        r = requests.post(hook, json={"content":content,"embeds":embeds or []}, timeout=10)
        return r.status_code in [200,204]
    except: return False

def groq_call(prompt, max_tokens=150):
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {GROQ_KEY}","Content-Type":"application/json"},
            json={"model":GROQ_MODEL,"messages":[{"role":"user","content":prompt}],"max_tokens":max_tokens,"temperature":0.3},
            timeout=15)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except: pass
    return None

def analyze_stock(ticker, title, desc):
    prompt = f"""你是投资分析助手，代表芒格、巴菲特、Burry、Soros、林奇、Keith Gill六位大师。
分析以下股票新闻，全部用中文回答：

股票：{ticker}（{WATCHLIST.get(ticker,ticker)}）
标题：{title}
摘要：{desc[:200]}

请用以下格式回答（每行不超过20字）：
评级: 🟢偏多 或 🔴偏空 或 🟡中性
影响: 对该公司基本面的影响
行动: 建议操作（如逢低建仓/暂缓操作/卖Put等）"""
    out = groq_call(prompt)
    if out:
        lines = out.split("\n")
        rating = next((l.replace("评级:","").strip() for l in lines if "评级" in l), "🟡中性")
        impact = next((l.replace("影响:","").strip() for l in lines if "影响" in l), "待观察")
        action = next((l.replace("行动:","").strip() for l in lines if "行动" in l), "持续监控")
        color = 0x00ff00 if "🟢" in rating else (0xff0000 if "🔴" in rating else 0xffff00)
        return {"rating":rating,"impact":impact,"action":action,"color":color}
    return {"rating":"🟡中性","impact":"待分析","action":"持续监控","color":0xffff00}

def classify_intel(title, summary):
    text = (title+" "+summary).lower()
    for k in CRITICAL_KW:
        if k in text: return "CRITICAL"
    if sum(1 for k in HIGH_KW if k in text) >= 2: return "HIGH"
    if sum(1 for k in MARKET_KW if k in text) >= 2: return "MARKET"
    return None

def analyze_intel(title, summary, level):
    level_cn = {"CRITICAL":"极紧急","HIGH":"高度关注","MARKET":"市场消息"}.get(level,"资讯")
    prompt = f"""你是全球情报分析师，专注于全球事件对美股市场的影响，全部用中文回答。

新闻级别：{level_cn}
标题：{title}
摘要：{summary[:300]}

请用以下格式回答（每行不超过25字）：
影响: 🟢利多 或 🔴利空 或 🟡中性 + 一句话说明
板块: 最受影响的板块或资产
建议: 具体操作建议"""
    out = groq_call(prompt)
    if out:
        lines = out.split("\n")
        impact  = next((l.replace("影响:","").strip() for l in lines if "影响" in l), "🟡中性 待观察")
        sector  = next((l.replace("板块:","").strip() for l in lines if "板块" in l), "待定")
        suggest = next((l.replace("建议:","").strip() for l in lines if "建议" in l), "持续监控")
        color = 0xff3333 if "🔴" in impact else (0x00cc44 if "🟢" in impact else 0xffaa00)
        return {"impact":impact,"sector":sector,"suggest":suggest,"color":color}
    return {"impact":"🟡中性 待分析","sector":"待定","suggest":"持续监控","color":0xffaa00}

def scan_stocks():
    try:
        r = requests.get("https://api.polygon.io/v2/reference/news",
            params={"apiKey":POLYGON_KEY,"ticker.any_of":",".join(WATCHLIST.keys()),
                    "order":"desc","limit":20,"sort":"published_utc"},timeout=15)
        arts = r.json().get("results",[]) if r.status_code==200 else []
    except: return
    for art in arts:
        aid = art.get("id","")
        if not aid or aid in stock_seen: continue
        stock_seen.add(aid)
        relevant = [t for t in art.get("tickers",[]) if t in WATCHLIST]
        if not relevant: continue
        title = art.get("title","")
        desc = art.get("description","")
        if not any(w in (title+desc).lower() for w in SIGNAL_WORDS): continue
        for ticker in relevant[:1]:
            ana = analyze_stock(ticker, title, desc)
            desc_short = desc[:200]+"..." if len(desc)>200 else desc
            tier = {"META":1,"MSFT":1,"NVDA":1,"GOOGL":1}.get(ticker,2)
            tier_icon = {1:"🥇",2:"🥈"}.get(tier,"🥉")
            embed = {
                "title":f"{tier_icon} 股票快讯 [{ticker}] {WATCHLIST[ticker]}",
                "description":f"**{title}**\n\n{desc_short}",
                "color":ana["color"],
                "fields":[
                    {"name":"📊 六大师评级","value":ana["rating"],"inline":True},
                    {"name":"💡 基本面影响","value":ana["impact"],"inline":True},
                    {"name":"🎯 建议操作","value":ana["action"],"inline":False},
                    {"name":"🔗 原文链接","value":f"[点击查看]({art.get('article_url','#')})","inline":False},
                ],
                "footer":{"text":f"晓犀六大师雷达 | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} | Groq Llama 3.3 70B"}
            }
            push(RADAR_HOOK, "", [embed])
            time.sleep(1)

def scan_intel():
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:8]:
                raw = entry.get("id") or entry.get("link") or entry.get("title","")
                uid = hashlib.md5(raw.encode("utf-8","replace")).hexdigest()
                if uid in intel_seen: continue
                intel_seen.add(uid)
                title = entry.get("title","")
                summary = re.sub(r'<[^>]+',"",entry.get("summary",""))
                level = classify_intel(title, summary)
                if not level: continue
                ana = analyze_intel(title, summary, level)
                label_map = {"CRITICAL":"🚨 极紧急","HIGH":"⚠️ 高度关注","MARKET":"📊 市场动态"}
                label = label_map.get(level,"📰 资讯")
                embed = {
                    "title":f"{label} | {fi['icon']} {fi['name']}",
                    "description":f"**{title}**\n\n{summary[:300]}",
                    "color":ana["color"],
                    "fields":[
                        {"name":"📈 市场影响","value":ana["impact"],"inline":False},
                        {"name":"🏭 受影响板块","value":ana["sector"],"inline":True},
                        {"name":"🎯 操作建议","value":ana["suggest"],"inline":True},
                        {"name":"🔗 原文","value":f"[点击查看]({entry.get('link','#')})","inline":False},
                    ],
                    "footer":{"text":f"晓犀全球情报 | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} | Groq Llama 3.3 70B"}
                }
                alert = "🚨 **极紧急！立即关注！**" if level=="CRITICAL" else ("⚠️ **高度关注**" if level=="HIGH" else "")
                push(INTEL_HOOK, alert, [embed])
                time.sleep(0.8)
        except: continue

def run_radar():
    push(RADAR_HOOK, "🦏 **晓犀六大师雷达已上线（云端24/7运行）**", [{
        "title":"✅ 系统启动成功",
        "description":"**监控标的：** META | MSFT | NVDA | GOOGL | MU | AMZN | AAPL | LMT | KMI | AMD | AVGO | TSLA | TSM\n\n**扫描频率：** 每1分钟\n**AI引擎：** Groq Llama 3.3 70B\n**运行模式：** 云端24/7不间断",
        "color":65280,
        "footer":{"text":"晓犀 🦏 | 六大师雷达 v2.0"}
    }])
    scan_stocks()
    schedule.every(1).minutes.do(scan_stocks)
    while True:
        schedule.run_pending()
        time.sleep(20)

def run_intel():
    push(INTEL_HOOK, "🌍 **晓犀全球情报已上线（云端24/7运行）**", [{
        "title":"✅ 系统启动成功",
        "description":"**新闻源：** 路透社 | BBC | 半岛电视台 | 美联社 | MarketWatch | 中东之眼\n\n**触发条件：**\n🚨 极紧急 - 战争/核武/海峡封锁/美股熔断\n⚠️ 高度关注 - 地缘冲突/制裁/油价/美联储\n📊 市场动态 - 股市/债市/黄金/期货\n\n**扫描频率：** 每1分钟\n**AI引擎：** Groq Llama 3.3 70B",
        "color":39423,
        "footer":{"text":"晓犀 🦏 | 全球情报 v1.0"}
    }])
    scan_intel()
    schedule.every(1).minutes.do(scan_intel)
    while True:
        schedule.run_pending()
        time.sleep(20)

if __name__ == "__main__":
    missing = [
        name for name, value in {
            "POLYGON_KEY": POLYGON_KEY,
            "GROQ_KEY": GROQ_KEY,
            "RADAR_HOOK": RADAR_HOOK,
            "INTEL_HOOK": INTEL_HOOK,
        }.items() if not value
    ]
    if missing:
        raise RuntimeError(f"缺少环境变量: {', '.join(missing)}")

    print("晓犀雷达系统启动中...")
    print("股票雷达 + 全球情报，每1分钟扫描")
    t1 = threading.Thread(target=run_radar, daemon=True)
    t2 = threading.Thread(target=run_intel, daemon=True)
    t1.start()
    t2.start()
    print("两个模块均已启动，运行中...")
    while True:
        time.sleep(60)
