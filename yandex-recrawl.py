"""Переобход публичных страниц 8ET. Токен и данные кабинета не публикуются."""
import os, json, datetime as dt, urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

BASE="https://8et.ru"
API="https://api.webmaster.yandex.net/v4"
STATE=Path("yandex-recrawl-state.json")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
OPENER=urllib.request.build_opener(NoRedirect())
def request(url, token=None, payload=None):
    h={"Accept":"application/json","User-Agent":"8ET SEO"}
    if token: h["Authorization"]="OAuth "+token
    data=None if payload is None else json.dumps(payload).encode("utf-8")
    if data is not None: h["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=data,headers=h)
    try:
        with OPENER.open(req,timeout=15) as r:
            return r.status,r.read(4_000_000)
    except urllib.error.HTTPError as e: return e.code,b""
def api(path, token, payload=None):
    status,raw=request(API+path,token,payload)
    if status not in (200,202): return status,{}
    try: return status,json.loads(raw)
    except (ValueError,TypeError): return 502,{}
def must(path,token):
    status,j=api(path,token)
    if status!=200: raise RuntimeError("Яндекс API: HTTP "+str(status))
    return j
class Meta(HTMLParser):
    def __init__(self): super().__init__();self.canonical=None;self.noindex=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=="link" and "canonical" in (a.get("rel") or "").lower().split(): self.canonical=a.get("href")
        if tag=="meta" and (a.get("name") or "").lower() in ("robots","yandex") and "noindex" in (a.get("content") or "").lower(): self.noindex=True
def valid(url):
    p=urllib.parse.urlsplit(url)
    return p.scheme=="https" and p.netloc=="8et.ru" and not p.query and not p.fragment
def indexable(url):
    if not valid(url):return False
    status,body=request(url)
    if status!=200:return False
    p=Meta();p.feed(body.decode("utf-8","replace"))
    return p.canonical==url and not p.noindex
def main():
    token=os.environ.get("YANDEX_WEBMASTER_TOKEN","").strip()
    if not token:raise RuntimeError("Секрет YANDEX_WEBMASTER_TOKEN отсутствует")
    user=must("/user",token)
    uid=user.get("user_id")
    if not isinstance(uid,int):raise RuntimeError("Не получен идентификатор пользователя")
    hosts=must("/user/"+str(uid)+"/hosts",token).get("hosts",[])
    host=next((h for h in hosts if h.get("verified") is True and str(h.get("ascii_host_url","")).rstrip("/")==BASE),None)
    if not host:raise RuntimeError("Подтверждённый HTTPS-сайт 8et.ru не найден")
    prefix="/user/"+str(uid)+"/hosts/"+urllib.parse.quote(host["host_id"],safe="")
    print("Авторизация и права на 8et.ru подтверждены",flush=True)
    qs,_=api(prefix+"/search-queries/popular?order_by=TOTAL_SHOWS&limit=1",token)
    print("Проверка доступа к поисковым запросам: HTTP",qs,flush=True)
    # Не выводим поисковые запросы, статистику или идентификаторы в публичный журнал.
    q=must(prefix+"/recrawl/quota",token)
    quota=q.get("quota_remainder")
    if not isinstance(quota,int) or quota<0:raise RuntimeError("Некорректный остаток квоты")
    limit=min(quota,20)
    state=json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"accepted":{}}
    if not isinstance(state.get("accepted"),dict):raise RuntimeError("Повреждён журнал")
    status,raw=request(BASE+"/pages-sitemap.xml")
    if status!=200:raise RuntimeError("Карта сайта недоступна")
    urls=[e.text.strip() for e in ET.fromstring(raw).findall(".//{*}loc") if e.text and valid(e.text.strip())]
    priority=["/","/articles","/upcoming","/sport/football","/sport/hockey","/matches/khl/calendar","/matches/nhl/calendar","/matches/rpl/calendar","/matches/ucl/calendar","/matches/epl/calendar","/matches/bundesliga/calendar","/matches/ligue1/calendar","/matches/saudi/calendar","/matches/mls/calendar"]
    rank={BASE+x:i for i,x in enumerate(priority)}
    queue=sorted(set(urls),key=lambda u:(rank.get(u,999),u))
    accepted=0
    for url in queue:
        if limit<=0 or url in state["accepted"]:continue
        if not indexable(url):continue
        status,_=api(prefix+"/recrawl/queue",token,{"url":url})
        if status==202 or status==409:
            state["accepted"][url]={"status":"accepted" if status==202 else "already_queued","date":dt.datetime.now(dt.timezone.utc).isoformat()}
            STATE.write_text(json.dumps(state,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            if status==202:accepted+=1;limit-=1
            print("Переобход:",status,url,flush=True)
        elif status==429:break
        else:print("Переобход отложен: HTTP",status,url,flush=True)
    # Даже при пустой квоте сохраняем подтверждение проверки связи, без метрик.
    state["last_check"]={"at":dt.datetime.now(dt.timezone.utc).isoformat(),"auth":True,"query_endpoint_http":qs,"accepted_this_run":accepted}
    STATE.write_text(json.dumps(state,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":
    try:main()
    except Exception as e:
        # Не показывать тела ответов, секреты или идентификаторы аккаунта.
        if isinstance(e,RuntimeError):print(str(e))
        else:print("Ошибка интеграции:",type(e).__name__)
        raise SystemExit(1)
