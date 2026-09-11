#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日新闻早报 -> 微信推送

数据源（全部为公开页面 / RSS，无需 API Key）:
  国内要闻    中新网 RSS  https://www.chinanews.com.cn/rss/china.xml
  国际要闻    中新网 RSS  https://www.chinanews.com.cn/rss/world.xml
  人民日报时评 人民网观点频道 http://opinion.people.com.cn/

推送通道（二选一，优先用 WxPusher）:
  1. WxPusher 极简推送 SPT —— 推荐。扫码即得令牌，无需注册、无需实名认证。
     获取: 微信扫描 https://wxpusher.zjiecode.com/docs/#/?id=spt 页面上的二维码
     凭据: SPT_ 开头的字符串
  2. PushPlus —— 备选。需微信扫码登录 + 手机实名，免费 200 条/天。
     凭据: 32 位 token

用法:
  python daily_news.py --dry-run          # 只生成本地 HTML 预览，不推送
  python daily_news.py                    # 抓取并推送到微信
  python daily_news.py --spt SPT_xxxx     # 指定 WxPusher 令牌
  python daily_news.py --token xxxx       # 指定 PushPlus token

凭据优先级: --spt > --token > 环境变量 WXPUSHER_SPT > 环境变量 PUSHPLUS_TOKEN
"""

import argparse
import datetime
import gzip
import html as html_mod
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

CN_RSS = "https://www.chinanews.com.cn/rss/china.xml"
WORLD_RSS = "https://www.chinanews.com.cn/rss/world.xml"
OPINION_HOME = "http://opinion.people.com.cn/"
OPINION_COLUMN = "http://opinion.people.com.cn/GB/223228/index.html"

NEWS_PER_SECTION = 10
OPINION_COUNT = 3
SUMMARY_MAX = 70


# ---------------------------------------------------------------- 网络

def fetch(url, retries=3, timeout=25):
    """带重试的抓取，返回 bytes。自动处理 gzip。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Encoding": "gzip",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw
        except Exception as e:            # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"抓取失败 {url}: {last}")


def decode_smart(raw):
    """人民网 meta 声明 GB2312 但实际是 UTF-8，必须先试 UTF-8。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


# ---------------------------------------------------------------- 文本清洗

def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return html_mod.unescape(s).replace("\u3000", " ").strip()


def norm_key(s):
    """归一化用于相似度比较：只保留中英文数字。"""
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s or "")


def make_summary(text, title="", limit=SUMMARY_MAX):
    """把 RSS description 清洗成一句话摘要。

    中新网 description 有四种形态，必须区别处理：
      1. '中新社北京9月9日电 (记者 张三)正文...'  -> 去电头 + 去记者括号
      2. '中新网天津9月9日电 题：<标题>'          -> 内容等于标题，无价值，丢弃
      3. '原标题：...'                            -> 残缺标题，无价值，丢弃
      4. '记者从业内获悉，正文...'                -> 无电头，直接用
    """
    text = re.sub(r"\s+", " ", strip_tags(text))
    if not text:
        return ""

    # 形态 3：原标题开头的一律不要，它不是正文摘要
    if re.match(r"^原标题\s*[:：]", text):
        return ""

    # 形态 1/2：剥掉「中新社北京9月9日电」这类电头
    text = re.sub(r"^(中新社|中新网|中国新闻网|人民网)[^，。！？]{0,18}?[电讯]\s*",
                  "", text)
    # 剥掉紧跟其后的「(记者 张三 李四)」
    text = re.sub(r"^[（(][^）)]{0,40}[）)]\s*", "", text)
    # 形态 2：剩下的是「题：<标题>」
    text = re.sub(r"^题\s*[:：]\s*", "", text)
    text = text.strip()

    if len(norm_key(text)) < 12:
        return ""

    # 摘要与标题高度重合说明没有增量信息，宁缺勿滥
    if title:
        import difflib
        a, b = norm_key(text), norm_key(title)
        if a and b:
            if b in a and len(a) - len(b) < 10:
                return ""
            if difflib.SequenceMatcher(None, a, b).ratio() > 0.7:
                return ""

    # 按句累积，避免正文以「111天。」这类超短句开头时切出没信息量的摘要
    acc = ""
    for seg in re.findall(r"[^。！？!?]*[。！？!?]|[^。！？!?]+$", text):
        if acc and len(acc) + len(seg) > limit:
            break
        acc += seg
        if len(acc) >= 20:
            break
    if acc and len(acc) <= limit:
        return acc
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for p in ("，", "；", ",", " "):
        idx = cut.rfind(p)
        if idx > limit * 0.6:
            return cut[:idx] + "…"
    return cut + "…"


# ---------------------------------------------------------------- 数据源

def parse_rss(url, want):
    """解析中新网 RSS，返回新闻列表。"""
    raw = fetch(url)
    text = decode_smart(raw)
    text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text).strip()
    root = ET.fromstring(text)

    items = []
    for it in root.findall(".//item"):
        title = strip_tags(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        desc = it.findtext("description") or ""
        if not title or not link:
            continue
        items.append({
            "title": title,
            "link": link,
            "summary": make_summary(desc, title),
            "time": parse_pubdate(it.findtext("pubDate")),
        })

    # 按时间倒序，去重
    items.sort(key=lambda x: x["time"] or "", reverse=True)
    seen, out = set(), []
    for x in items:
        key = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", x["title"])[:20]
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
        if len(out) >= want:
            break
    return out


def parse_pubdate(s):
    """RFC822 -> HH:MM，失败返回空串。"""
    if not s:
        return ""
    m = re.search(r"(\d{1,2})\s+(\w{3})\s+(\d{4})\s+(\d{2}):(\d{2})", s)
    if not m:
        return ""
    months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    d, mon, y, hh, mm = m.groups()
    if mon not in months:
        return ""
    return f"{y}-{months[mon]:02d}-{int(d):02d} {hh}:{mm}"


ART_RE = re.compile(
    r'<a[^>]*href="((?:http://opinion\.people\.com\.cn)?/n1/(\d{4})/(\d{4})/c\d+-\d+\.html)"[^>]*>(.*?)</a>',
    re.S)


def collect_opinion_links(page_url):
    html = decode_smart(fetch(page_url))
    found = []
    for href, year, mmdd, title in ART_RE.findall(html):
        title = strip_tags(title)
        if len(title) < 6:
            continue
        if href.startswith("/"):
            href = "http://opinion.people.com.cn" + href
        found.append({"title": title, "link": href, "date": f"{year}{mmdd}"})
    return found


def fetch_opinion(want=OPINION_COUNT):
    """抓人民日报时评：栏目页 + 首页合并，按日期取最新，进正文补全标题和摘要。"""
    pool = []
    for url in (OPINION_COLUMN, OPINION_HOME):
        try:
            pool += collect_opinion_links(url)
        except Exception as e:                       # noqa: BLE001
            print(f"  [警告] 时评来源不可用 {url}: {e}", file=sys.stderr)

    seen, uniq = set(), []
    for x in pool:
        if x["link"] in seen:
            continue
        seen.add(x["link"])
        uniq.append(x)
    uniq.sort(key=lambda x: x["date"], reverse=True)

    out = []
    for x in uniq:
        if len(out) >= want:
            break
        detail = fetch_article(x["link"])
        title = detail.get("title") or x["title"].rstrip("…")
        out.append({
            "title": title,
            "link": x["link"],
            "summary": detail.get("summary", ""),
            "time": f"{x['date'][:4]}-{x['date'][4:6]}-{x['date'][6:]}",
        })
    return out


def fetch_article(url):
    """进人民网文章页取完整标题 + 首段摘要。失败不阻塞主流程。"""
    try:
        html = decode_smart(fetch(url, retries=2, timeout=20))
    except Exception:                                 # noqa: BLE001
        return {}
    title = ""
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        title = strip_tags(m.group(1))
        title = re.sub(r"\s*--+\s*(观点|评论)?\s*--+\s*人民网\s*$", "", title).strip()
        title = re.sub(r"[-—]+\s*人民网\s*$", "", title).strip()

    # 正文容器是 <div class="rm_txt_con cf">，内部还有嵌套 div，
    # 正则无法匹配嵌套结构，所以定位到容器起点后截取一段区域再找 <p>。
    body = ""
    idx = html.find("rm_txt_con")
    if idx == -1:
        idx = html.find("rwb_zw")
    region = html[idx:idx + 20000] if idx != -1 else html
    for p in re.findall(r"<p[^>]*>(.*?)</p>", region, re.S):
        t = strip_tags(p)
        if len(t) >= 24 and not re.match(r"^(责编|编辑|来源|分享|相关)", t):
            body = t
            break
    return {"title": title, "summary": make_summary(body, title, 90)}


# ---------------------------------------------------------------- 排版

def render_html(domestic, world, opinions, today):
    css_item = "margin:0 0 14px 0;padding:0 0 12px 0;border-bottom:1px solid #eee;"
    css_title = ("color:#1a1a1a;font-size:16px;font-weight:600;"
                 "text-decoration:none;line-height:1.5;")
    css_sum = "margin:6px 0 0 0;color:#666;font-size:14px;line-height:1.6;"
    css_h2 = ("margin:26px 0 14px 0;padding-left:10px;font-size:17px;"
              "border-left:4px solid #c62828;color:#111;")

    parts = [
        '<div style="font-family:-apple-system,BlinkMacSystemFont,'
        '\'PingFang SC\',\'Microsoft YaHei\',sans-serif;max-width:700px;">',
        f'<p style="color:#999;font-size:13px;margin:0 0 4px 0;">{today} 早报</p>',
    ]

    def section(name, rows, badge):
        if not rows:
            parts.append(f'<h2 style="{css_h2}">{name}</h2>'
                         f'<p style="{css_sum}">今日暂未获取到内容。</p>')
            return
        parts.append(f'<h2 style="{css_h2}">{name}<span style="color:#999;'
                     f'font-size:13px;font-weight:400;"> · {len(rows)} 条</span></h2>')
        for i, r in enumerate(rows, 1):
            t = html_mod.escape(r["title"])
            parts.append(f'<div style="{css_item}">')
            parts.append(
                f'<a href="{html_mod.escape(r["link"])}" style="{css_title}">'
                f'<span style="color:{badge};font-weight:600;">{i:02d}.</span> {t}</a>')
            if r.get("summary"):
                parts.append(f'<p style="{css_sum}">{html_mod.escape(r["summary"])}</p>')
            if r.get("time"):
                parts.append(f'<p style="margin:4px 0 0 0;color:#bbb;'
                             f'font-size:12px;">{r["time"]}</p>')
            parts.append('</div>')

    section("国内要闻", domestic, "#c62828")
    section("国际要闻", world, "#1565c0")
    section("人民日报时评", opinions, "#6a1b9a")

    parts.append('<p style="margin:22px 0 0 0;color:#bbb;font-size:12px;">'
                 '数据来源：中国新闻网、人民网观点频道 · 点击标题阅读原文</p>')
    parts.append('</div>')
    return "\n".join(parts)


def wrap_page(fragment, today):
    """把正文片段包成手机友好的独立网页（用于在线发布 / 本地预览）。

    与推送用的片段共用同一份正文，只是多一层外壳：viewport、浅灰底白卡、
    禁止缓存（保证扫码永远看到当天内容）。
    """
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,'
        'viewport-fit=cover">\n'
        '<meta http-equiv="Cache-Control" '
        'content="no-cache, no-store, must-revalidate">\n'
        f'<title>{today} 新闻早报</title>\n'
        '<style>\n'
        'html{-webkit-text-size-adjust:100%;}\n'
        'body{margin:0;background:#f2f3f5;}\n'
        '.page{max-width:700px;margin:0 auto;background:#fff;\n'
        '     padding:16px 15px 28px;min-height:100vh;\n'
        '     padding-bottom:calc(28px + env(safe-area-inset-bottom));}\n'
        'a{-webkit-tap-highlight-color:transparent;}\n'
        '@media (max-width:400px){\n'
        '  .page{padding:14px 12px 24px;}\n'
        '}\n'
        '</style>\n</head>\n<body>\n<div class="page">\n'
        f'{fragment}\n'
        '</div>\n</body>\n</html>\n')


# ---------------------------------------------------------------- 推送

MAX_CONTENT_BYTES = 60000          # WxPusher 硬上限 65535 字节，留安全边界


def post_json(url, obj):
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "ignore")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"接口返回非 JSON: {body[:200]}")


def load_secret(key):
    """从同目录 secrets.json 读取凭据，避免每次命令行传参。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get(key, "") or "")
    except (OSError, ValueError):
        return ""


def push_wxpusher(spt, summary, content):
    """WxPusher 极简推送。成功返回 sendRecordId。"""
    data = post_json("https://wxpusher.zjiecode.com/api/send/message/simple-push", {
        "spt": spt,
        "summary": summary[:100],       # 官方限制 100 字符
        "content": content,
        "contentType": 2,               # 2 = HTML
    })
    if data.get("code") != 1000:        # WxPusher 成功码是 1000
        raise RuntimeError(f"WxPusher 拒绝请求: code={data.get('code')} "
                           f"msg={data.get('msg')}")
    rec = data.get("data")
    if isinstance(rec, list) and rec:
        return rec[0].get("sendRecordId")
    return rec


def push_pushplus(token, title, content):
    """PushPlus 推送。成功返回消息流水号。"""
    data = post_json("https://www.pushplus.plus/send", {
        "token": token,
        "title": title,
        "content": content,
        "template": "html",
    })
    if data.get("code") != 200:
        raise RuntimeError(f"PushPlus 拒绝请求: code={data.get('code')} "
                           f"msg={data.get('msg')}")
    return data.get("data")


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="每日新闻早报推送到微信")
    ap.add_argument("--dry-run", action="store_true",
                    help="只生成本地 HTML 预览，不推送")
    ap.add_argument("--spt", default="", help="WxPusher 极简推送 SPT（推荐）")
    ap.add_argument("--token", default="", help="PushPlus token（备选通道）")
    ap.add_argument("--out", default="", help="预览文件输出路径")
    args = ap.parse_args()

    today = datetime.date.today().strftime("%Y年%m月%d日")
    print(f"抓取 {today} 新闻...")

    domestic, world, opinions = [], [], []
    try:
        domestic = parse_rss(CN_RSS, NEWS_PER_SECTION)
        print(f"  国内要闻 {len(domestic)} 条")
    except Exception as e:                            # noqa: BLE001
        print(f"  [错误] 国内新闻抓取失败: {e}", file=sys.stderr)
    try:
        world = parse_rss(WORLD_RSS, NEWS_PER_SECTION)
        print(f"  国际要闻 {len(world)} 条")
    except Exception as e:                            # noqa: BLE001
        print(f"  [错误] 国际新闻抓取失败: {e}", file=sys.stderr)
    try:
        opinions = fetch_opinion(OPINION_COUNT)
        print(f"  人民日报时评 {len(opinions)} 篇")
    except Exception as e:                            # noqa: BLE001
        print(f"  [错误] 时评抓取失败: {e}", file=sys.stderr)

    if not (domestic or world or opinions):
        print("三路数据源全部失败，放弃推送。", file=sys.stderr)
        return 1

    content = render_html(domestic, world, opinions, today)
    push_title = f"{today} 新闻早报"

    if args.dry_run:
        out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "preview.html")
        parent = os.path.dirname(os.path.abspath(out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(wrap_page(content, today))
        print(f"\n预览已生成: {out}")
        print("确认无误后去掉 --dry-run 即可推送到微信。")
        return 0

    nbytes = len(content.encode("utf-8"))
    if nbytes > MAX_CONTENT_BYTES:
        print(f"  [警告] 正文 {nbytes} 字节超限，裁剪时评部分后重排。", file=sys.stderr)
        content = render_html(domestic, world, [], today)

    spt = args.spt or os.environ.get("WXPUSHER_SPT", "") or load_secret("WXPUSHER_SPT")
    token = args.token or os.environ.get("PUSHPLUS_TOKEN", "") or load_secret("PUSHPLUS_TOKEN")

    if spt:
        try:
            rec = push_wxpusher(spt, push_title, content)
        except Exception as e:                        # noqa: BLE001
            print(f"\n推送失败: {e}", file=sys.stderr)
            return 3
        print(f"\n已通过 WxPusher 推送，发送记录号: {rec}")
        print("若手机未收到，检查是否已关注 WxPusher 公众号 / 安装客户端。")
        return 0

    if token:
        try:
            code = push_pushplus(token, push_title, content)
        except Exception as e:                        # noqa: BLE001
            print(f"\n推送失败: {e}", file=sys.stderr)
            return 3
        print(f"\n已通过 PushPlus 推送，消息流水号: {code}")
        print("若微信未收到，去 PushPlus 官网「消息记录」用流水号查最终状态。")
        return 0

    print("未找到推送凭据，二选一配置即可：\n"
          "  [推荐] WxPusher：扫码获取 SPT（免注册免实名），\n"
          "         设置环境变量 WXPUSHER_SPT 或用 --spt 参数；\n"
          "         二维码见 https://wxpusher.zjiecode.com/docs/#/?id=spt\n"
          "  [备选] PushPlus：需实名，设置 PUSHPLUS_TOKEN 或用 --token 参数。",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
