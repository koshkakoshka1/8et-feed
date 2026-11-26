"""Only submit public 8et.ru pages. No private search data or account IDs are logged."""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

HOME = "https://8et.ru"
API = "https://api.webmaster.yandex.net/v4"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

OPENER = urllib.request.build_opener(NoRedirect())

def request(url, token=None, payload=None):
    headers = {"Accept":"application/json,text/html,application/xml","User-Agent":"8ET recrawl/1.0"}
    if token is not None:
        if not url.startswith(API + "/"):
            raise RuntimeError("Refusing to send authorization outside Yandex API")
        headers["Authorization"] = "OAuth " + token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=UTF-8"
        data = json.dumps(payload).encode("utf8")
    try:
        with OPENER.open(urllib.request.Request(url, data=data, headers=headers), timeout=12) as r:
            return r.status, r.read(4000000), r.headers.get("X-Robots-Tag","")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""

def api(path, token, payload=None):
    status, raw, _ = request(API+path, token, payload)
    if status not in (200,202):
        return status, {}
    j = json.loads(raw)
    if not isinstance(j, dict):
        raise RuntimeError("Unexpected API response")
    return status, j

def required(path, token):
    status, data = api(path, token)
    if status != 200:
        raise RuntimeError("Yandex API HTTP " + str(status))
    return data

class Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = None
        self.noindex = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.canonical = a.get("href")
        if tag == "meta" and (a.get("name") or "").lower() in ("robots","yandex"):
            if re.search(r"\b(noindex|none)\b",(a.get("content") or "").lower()):
                self.noindex = True

def valid(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme=="https" and p.netloc=="8et.ru" and not p.query and not p.fragment and bool(re.fullmatch(r"/(?:articles(?:/[a-z0-9-]+)?|upcoming|methodology|statistics|sport/(?:football|hockey|basketball|tennis)|matches/[a-z0-9-]+/(?:calendar|[0-9]+))?",p.path))

def indexable(url):
    if not valid(url):
        return False
    status, body, robots = request(url)
    if status != 200 or re.search(r"\b(noindex|none)\b",robots.lower()):
        return False
    parser = Meta()
    parser.feed(body.decode("utf8","replace"))
    return parser.canonical == url and not parser.noindex

def sitemap():
    status, body, _ = request(HOME+"/pages-sitemap.xml")
    if status != 200:
        raise RuntimeError("Sitemap unavailable")
    urls = {e.text.strip() for e in ET.fromstring(body).findall(".//{*}loc") if e.text and valid(e.text.strip())}
    priority = ["/","/articles","/sport/football","/sport/hockey","/matches/khl/calendar","/matches/nhl/calendar","/matches/rpl/calendar","/matches/ucl/calendar","/matches/epl/calendar"]
    rank = {HOME+p:i for i,p in enumerate(priority)}
    return sorted(urls,key=lambda u:(rank.get(u,999),u))

def recrawl(token):
    uid = required("/user",token).get("user_id")
    if type(uid) is not int:
        raise RuntimeError("Invalid user response")
    root="/user/"+str(uid)+"/hosts"
    host = next((h for h in required(root,token).get("hosts",[]) if h.get("verified") is True and str(h.get("ascii_host_url","")).rstrip("/")==HOME),None)
    if host is None or not isinstance(host.get("host_id"),str):
        raise RuntimeError("8et.ru ownership not verified")
    prefix=root+"/"+urllib.parse.quote(host["host_id"],safe=":")
    quota=required(prefix+"/recrawl/quota",token).get("quota_remainder")
    if type(quota) is not int or quota<0:
        raise RuntimeError("Invalid quota response")
    limit=min(quota,10)
    if not limit:
        print("No remaining quota")
        return
    recent=set()
    start=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    for offset in range(0,1000,100):
        params=urllib.parse.urlencode({"offset":offset,"limit":100,"date_from":start})
        tasks=required(prefix+"/recrawl/queue?"+params,token).get("tasks")
        if not isinstance(tasks,list):
            raise RuntimeError("Unknown queue schema")
        recent.update(t["url"] for t in tasks if isinstance(t,dict) and isinstance(t.get("url"),str))
        if len(tasks)<100:
            break
    else:
        raise RuntimeError("Queue too large for duplicate prevention")
    submitted=0
    attempted=0
    for url in sitemap():
        if submitted>=limit or attempted>=10:
            break
        if url in recent or not indexable(url):
            continue
        attempted+=1
        status,result=api(prefix+"/recrawl/queue",token,{"url":url})
        if status==202 and isinstance(result.get("task_id"),str) and result["task_id"]:
            submitted+=1
        elif status==409:
            continue
        elif status==429:
            break
        else:
            raise RuntimeError("Submission failed HTTP "+str(status))
    print("Public pages accepted:",submitted,"attempted:",attempted)

def test():
    from unittest.mock import patch
    assert valid("https://8et.ru/")
    assert valid("https://8et.ru/matches/khl/calendar")
    assert not valid("https://8et.ru.example.com/")
    assert not valid("https://8et.ru/?token=example")
    assert not valid("https://8et.ru/api/feed-health")
    with patch(__name__+".request",return_value=(200,b'<link rel="canonical" href="https://8et.ru/">',"")):
        assert indexable(HOME+"/")
    with patch(__name__+".request",return_value=(200,b'<link rel="canonical" href="https://8et.ru/"><meta name="robots" content="noindex">',"")):
        assert not indexable(HOME+"/")
    def fake(path,token):
        if path=="/user":
            return {"user_id":1}
        if path=="/user/1/hosts":
            return {"hosts":[{"verified":True,"ascii_host_url":HOME,"host_id":"https:8et.ru:443"}]}
        if path.endswith("/recrawl/quota"):
            return {"quota_remainder":3}
        if "/recrawl/queue?" in path:
            return {"tasks":[{"url":HOME+"/"}]}
        raise AssertionError("Unexpected request")
    with patch(__name__+".required",side_effect=fake),patch(__name__+".sitemap",return_value=[HOME+"/",HOME+"/articles"]),patch(__name__+".indexable",return_value=True),patch(__name__+".api",return_value=(202,{"task_id":"test"})) as submit:
        recrawl("test")
        assert submit.call_count==1
        assert submit.call_args.args[2]=={"url":HOME+"/articles"}
    print("All offline checks passed")

if __name__=="__main__":
    try:
        if sys.argv[1:]==["--self-test"]:
            test()
        elif not sys.argv[1:]:
            token=os.environ.get("YANDEX_SECURE_TOKEN","").strip()
            if not token:
                raise RuntimeError("Protected environment token is missing")
            recrawl(token)
        else:
            raise RuntimeError("Unsupported arguments")
    except Exception as e:
        print(str(e) if type(e) is RuntimeError else "Failure: "+type(e).__name__)
        sys.exit(1)
