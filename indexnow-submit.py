"""Отправка новых и изменённых канонических страниц 8et.ru в IndexNow."""
import concurrent.futures
import hashlib
import json
import re
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

HOST = '8et.ru'
BASE = 'https://' + HOST
KEY = 'c8477cb6096f4cc1b4bcc523d54847fd'
STATE = Path('indexnow-state.json')
UA = '8ET IndexNow publisher/1.0'

def valid(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme == 'https' and p.netloc == HOST and not p.query and not p.fragment and bool(p.path)

def fetch(url, data=None):
    if data is None and not valid(url):
        raise ValueError('Недопустимый адрес: ' + url)
    req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Content-Type': 'application/json; charset=utf-8'})
    with urllib.request.urlopen(req, timeout=25) as r:
        if data is None and r.geturl() != url:
            raise ValueError('Неожиданная переадресация: ' + url)
        return r.status, r.read(8_000_000)

def sitemap(url, seen=None):
    if seen is None: seen = set()
    if url in seen: return {}
    seen.add(url)
    _, body = fetch(url)
    root = ET.fromstring(body)
    kind = root.tag.split('}')[-1]
    result = {}
    for item in root:
        loc = item.find('{*}loc')
        if loc is None or not loc.text or not valid(loc.text.strip()): continue
        u = loc.text.strip()
        if kind == 'sitemapindex':
            result.update(sitemap(u, seen))
        elif kind == 'urlset':
            mod = item.find('{*}lastmod')
            stamp = mod.text.strip() if mod is not None and mod.text else ''
            if u not in result or stamp > result[u]:
                result[u] = stamp
    return result

def main():
    key_url = BASE + '/' + KEY + '.txt'
    status, b = fetch(key_url)
    if status != 200 or b.decode().strip() != KEY:
        raise RuntimeError('Ключ IndexNow не подтверждён')
    old = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    if not isinstance(old, dict): raise RuntimeError('Повреждён журнал отправки')
    urls = sitemap(BASE + '/sitemap.xml')
    if not urls: raise RuntimeError('Пустая карта сайта; отправка отменена')
    # Страницы без содержательной аналитики не отправляем массово.
    def hub(u):
        return bool(re.fullmatch(r'/matches/[a-z0-9-]+/calendar', urllib.parse.urlsplit(u).path))
    priority = [BASE+p for p in ('/sport/olympics','/sport/biathlon','/sport/figure-skating','/sport/esports','/sport/futsal','/matches/khl/calendar','/sport/hockey','/sport/football','/matches/rpl/calendar','/matches/nhl/calendar','/matches/epl/calendar','/matches/ucl/calendar','/','/articles','/upcoming')]
    rank = {u:i for i,u in enumerate(priority)}
    candidates = {u: stamp for u, stamp in urls.items() if hub(u) or not urllib.parse.urlsplit(u).path.startswith('/matches/') or stamp}
    changed = sorted((u for u, stamp in candidates.items() if u not in old or (stamp and old.get(u) != stamp)), key=lambda u:(rank.get(u, 100 if hub(u) else 200),u))
    print('Кандидатов:',len(candidates),'новых или изменённых:',len(changed))
    # Проверяем HTML и канонический адрес перед отправкой. Ограничение нагрузки на сайт.
    def check(u):
        try:
            status, raw = fetch(u)
            h = raw.decode('utf-8', errors='replace')
            match = re.search(r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', h, re.I)
            if status != 200 or not match or match.group(1) != u: return None
            if re.search(r'<meta\b[^>]*name=["\']robots["\'][^>]*content=["\'][^"\']*noindex', h, re.I): return None
            if hub(u):
                if not re.search(r'<a\b[^>]*class=["\']fixture["\']',h,re.I):return None
            elif '/matches/' in u and not re.search(r'data-et-(?:editorial|result)=',h):return None
            return u
        except Exception as e:
            print('Проверка отложена:',u,str(e)[:120])
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        verified = [u for u in pool.map(check, changed[:200]) if u]
    if not verified:
        print('Новых подтверждённых страниц нет')
        return
    body = json.dumps({'host':HOST,'key':KEY,'keyLocation':key_url,'urlList':verified}).encode()
    try:
        status, _ = fetch('https://api.indexnow.org/indexnow',body)
    except urllib.error.HTTPError as e:
        print('IndexNow отклонил запрос:',e.code)
        raise
    if status not in (200,202): raise RuntimeError('Ответ IndexNow: '+str(status))
    for u in verified:old[u]=candidates[u]
    STATE.write_text(json.dumps(old,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print('IndexNow: HTTP',status,'принято URL:',len(verified))
if __name__ == '__main__':main()
