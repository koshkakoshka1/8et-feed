import concurrent.futures as cf
import datetime as dt
import json, re, urllib.request, urllib.parse, urllib.error
from pathlib import Path

UTC=dt.timezone.utc
NOW=dt.datetime.now(UTC)
def date(s):
    try: return dt.datetime.fromisoformat(str(s).replace('Z','+00:00')).astimezone(UTC)
    except (ValueError,TypeError): return None
def get(url):
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'8ET editorial data collector'})
    with urllib.request.urlopen(req,timeout=15) as r: return json.load(r)
BASE='https://site.api.espn.com/apis/site/v2/sports/'
LEAGUES={'nhl':'hockey/nhl','rpl':'soccer/rus.1','ucl':'soccer/uefa.champions','uel':'soccer/uefa.europa','epl':'soccer/eng.1','laliga':'soccer/esp.1','seriea':'soccer/ita.1','nba':'basketball/nba'}
old_path=Path('pre-editorial.json')
old=json.loads(old_path.read_text(encoding='utf-8')) if old_path.exists() else {'articles':{}}
articles=old.get('articles',{})
archive=json.loads(Path('pre-events.json').read_text(encoding='utf-8')).get('events',[])
try:
    live=get('https://8et.ru/api/popular?view=archive-json').get('events',[])
except Exception as e:
    print('LIVE_CALENDAR_ERROR',str(e)); live=[]
events={e['path']:e for e in archive+live if isinstance(e,dict) and e.get('path') and date(e.get('when'))}
def competition(e):
    c=next(iter(e.get('competitions') or []),{})
    pp=c.get('competitors',[])
    if len(pp)!=2:return None
    p={x.get('homeAway'):x for x in pp}
    if set(p)!= {'home','away'}:return None
    if not date(c.get('date') or e.get('date')):return None
    return {'id':str(e.get('id') or c.get('id')),'when':date(c.get('date') or e.get('date')),'state':(c.get('status') or e.get('status') or {}).get('type',{}),'home':p['home'],'away':p['away'],'venue':(c.get('venue') or {}).get('fullName','')}
def teamid(x):return str(x.get('team',{}).get('id') or x.get('id') or '')
def score(x):
    v=x.get('score')
    if isinstance(v,dict):v=v.get('value',v.get('displayValue'))
    try:return int(v)
    except (TypeError,ValueError):return None
def final(c):
    status=c['state']
    if re.search(r'cancel|postpon|abandon|suspend|forfeit|tbd',str(status.get('name',''))+' '+str(status.get('description','')),re.I):return False
    return status.get('completed') is True and status.get('state')=='post' and all(score(c[x]) is not None and score(c[x])>=0 for x in ('home','away'))
def record(x):
    for r in x.get('records') or []:
        s=str(r.get('summary') or r.get('record') or '')
        if (r.get('type') in ('total','overall') or r.get('name') in ('overall','Total')) and re.fullmatch(r'\d+-\d+(?:-\d+)?',s): return s
    return ''
def board(key,route):
    out={}
    # Short, nonoverlapping windows avoid scoreboard truncation.
    begin=(NOW-dt.timedelta(days=28)).date()
    end=(NOW+dt.timedelta(days=14)).date()
    while begin<=end:
        last=min(begin+dt.timedelta(days=6),end)
        date_range=begin.strftime('%Y%m%d')+'-'+last.strftime('%Y%m%d')
        def read_window(dates):
            for limit in (200,100,None):
                params={'dates':dates}
                if limit:params['limit']=limit
                url=BASE+route+'/scoreboard?'+urllib.parse.urlencode(params)
                try:
                    payload=get(url)
                    if not isinstance(payload.get('events'),list):
                        raise ValueError('scoreboard missing events')
                    for e in payload['events']:
                        c=competition(e)
                        if c:out[c['id']]=c
                    if limit is not None and len(payload['events'])>=limit:
                        return False
                    return True
                except urllib.error.HTTPError as ex:
                    print('SOURCE_HTTP',key,dates,limit,ex.code,ex.read(160).decode(errors='replace').replace('\n',' '),flush=True)
                    if ex.code not in (400,422):return False
                except Exception as ex:
                    print('SOURCE_ERROR',key,dates,str(ex)[:200],flush=True)
                    return False
            return False
        if not read_window(date_range):
            # A smaller single-day request is only a fallback for rejected ranges.
            for offset in range((last-begin).days+1):
                if not read_window((begin+dt.timedelta(days=offset)).strftime('%Y%m%d')):
                    break
        begin=last+dt.timedelta(days=1)
    print('SOURCE',key,'events',len(out),'finished',sum(final(c) for c in out.values()),flush=True)
    return key,out
with cf.ThreadPoolExecutor(max_workers=6) as pool:
    boards=dict(pool.map(lambda kv:board(*kv),LEAGUES.items()))

# Discover additional fixtures from the same verified scoreboards.
# Preserve original URL and Russian labels for known events.
LABELS={'nhl':'НХЛ','rpl':'РПЛ','ucl':'Лига чемпионов','uel':'Лига Европы','epl':'АПЛ','laliga':'Ла Лига','seriea':'Серия А','nba':'НБА'}
team_names={}
for path,e in list(events.items()):
    m=re.fullmatch(r'/matches/([a-z0-9-]+)/([0-9]+)',path)
    if not m or m[1] not in boards:continue
    c=boards[m[1]].get(m[2]);names=e.get('match','').split(' — ')
    if not c or len(names)!=2:continue
    if abs((date(e['when'])-c['when']).total_seconds())>900:continue
    for side,name in zip(('home','away'),names):
        ident=teamid(c[side])
        if ident:team_names[(m[1],ident)]=name
def name_for(key,x):
    t=x.get('team') or {}
    return team_names.get((key,teamid(x))) or t.get('displayName') or t.get('name') or ''
added=0
for key,rows in boards.items():
    for i,c in rows.items():
        if not re.fullmatch(r'[0-9]+',i):continue
        if c['state'].get('state')!='pre' or c['state'].get('completed') or not NOW<c['when']<=NOW+dt.timedelta(days=14):continue
        if re.search(r'cancel|postpon|suspend|tbd',str(c['state'].get('name','')),re.I):continue
        path='/matches/'+key+'/'+i
        if path in events:continue
        home,away=name_for(key,c['home']),name_for(key,c['away'])
        if not home or not away or not teamid(c['home']) or not teamid(c['away']):continue
        events[path]={'path':path,'key':key,'league':LABELS[key],'match':home+' — '+away,'when':c['when'].isoformat().replace('+00:00','Z'),'source':'ESPN'}
        added+=1
print('CALENDAR_EXPANSION',json.dumps({'added':added,'total':len(events),'future':sum(date(e['when'])>NOW for e in events.values())}),flush=True)
def form(rows,id,cut):
    out=[]
    for c in rows.values():
        if not final(c) or c['when']>=cut:continue
        side='home' if teamid(c['home'])==id else 'away' if teamid(c['away'])==id else None
        if not side:continue
        opp='away' if side=='home' else 'home'
        out.append((c['when'],score(c[side]),score(c[opp])))
    return sorted(out,reverse=True)[:5]
def summarize(a):
    n=len(a);w=sum(x[1]>x[2] for x in a);d=sum(x[1]==x[2] for x in a);l=n-w-d
    gf=sum(x[1] for x in a);ga=sum(x[2] for x in a)
    return n,w,d,l,gf,ga
def dec(n):return ('%.1f'%n).replace('.',',')
def dtm(x):return (x+dt.timedelta(hours=3)).strftime('%d.%m.%Y')
def build(path,e):
    m=re.fullmatch(r'/matches/([a-z0-9-]+)/([0-9]+)',path)
    if not m or m[1] not in boards:return
    k,i=m.groups();rows=boards[k];c=rows.get(i)
    if not c:return
    when=date(e['when'])
    if abs((when-c['when']).total_seconds())>900:
        print('DATE_MISMATCH',path,e['when'],c['when'].isoformat());return
    names=e.get('match','').split(' — ')
    if len(names)!=2 or not all(names):return
    # Preserve fixture order from the existing home/away calendar.
    h,a=names;url=BASE+LEAGUES[k]+'/scoreboard'
    summary_url=BASE+LEAGUES[k]+'/summary?event='+i
    prior=articles.get(path,{})
    if final(c):
        result={'home':score(c['home']),'away':score(c['away']),'date':c['when'].isoformat(),'source':summary_url,'observedAt':NOW.isoformat()}
        if prior:
            if prior.get('result',{}).get('home')==result['home'] and prior.get('result',{}).get('away')==result['away']:return
            articles[path]={**prior,'result':result,'updatedAt':NOW.isoformat()}
        else:
            articles[path]={'when':e['when'],'match':e['match'],'league':e['league'],'updatedAt':NOW.isoformat(),'sections':[],'sources':[{'name':'ESPN: данные встречи','url':summary_url}],'result':result}
        return
    if c['when']<=NOW or c['state'].get('state')!='pre':return
    hf,af=form(rows,teamid(c['home']),NOW),form(rows,teamid(c['away']),NOW)
    if min(len(hf),len(af))<2:return
    n,w,d,l,gf,ga=summarize(hf);n2,w2,d2,l2,gf2,ga2=summarize(af)
    unit='очков' if k=='nba' else 'шайб' if k=='nhl' else 'голов'
    sections=[]
    for name,stats,arr in [(h,(n,w,d,l,gf,ga),hf),(a,(n2,w2,d2,l2,gf2,ga2),af)]:
        nn,ww,dd,ll,ff,aa=stats
        desc=f'{name}: {ww} побед, {ll} поражений'+(f', {dd} ничьих' if dd else '')+f' в {nn} последних завершённых встречах, доступных в выборке. Средние показатели: {dec(ff/nn)} {unit} за матч в атаке, {dec(aa/nn)} — у соперников. Последние результаты (с позиции команды): '+', '.join(f'{v}:{o} ({dtm(t)})' for t,v,o in arr)+'.'
        sections.append({'title':'Форма: '+name,'text':desc})
    hr,ar=record(c['home']),record(c['away'])
    if hr and ar:
        sections.append({'title':'Сезонный баланс','text':f'{h} — {hr}, {a} — {ar}. Баланс приведён в формате источника на момент сбора данных.'})
    if c['venue']:
        sections.append({'title':'Место проведения','text':str(c['venue'])[:180]+'.'})
    hmargin=(gf-ga)/n;amargin=(gf2-ga2)/n2
    delta=hmargin-amargin
    minimum=3 if k=='nba' else .4
    if abs(delta)>=minimum:
        fav=h if delta>0 else a
        conclusion=f'{fav} имеет более высокий средний баланс набранных и пропущенных {unit} в доступной выборке. Однако результаты против разных соперников не равнозначны и не определяют вероятность победы в конкретном матче.'
    else:conclusion='По среднему балансу результативности явного преимущества в доступной выборке нет.'
    conclusion+=' '+('В хоккее существенны составы звеньев, стартовые вратари и игра спецбригад.' if k=='nhl' else 'В баскетболе важны темп, ротация и доступность лидеров.' if k=='nba' else 'В футболе необходимо учитывать составы, домашнее поле и уровень соперников.')
    sections.append({'title':'Предварительная оценка','text':conclusion+' Это редакционный статистический разбор, а не опубликованный прогноз модели.'})
    candidate={'match':e['match'],'league':e['league'],'when':e['when'],'updatedAt':NOW.isoformat(),'lead':f'{h} — {a}: сопоставляем недавние результаты команд перед встречей. В расчёт включены только завершённые матчи с подтверждённым счётом в доступной выборке турнира.','sections':sections,'sources':[{'name':'ESPN: календарь и результаты турнира','url':url},{'name':'ESPN: данные встречи','url':summary_url}]}
    if prior.get('result'):return
    if {k:v for k,v in prior.items() if k!='updatedAt'}=={k:v for k,v in candidate.items() if k!='updatedAt'}:return
    articles[path]=candidate
for path,e in events.items():
    try:build(path,e)
    except Exception as ex:print('EVENT_ERROR',path,str(ex))
future=sum(date(v.get('when')) and date(v['when'])>NOW and len(v.get('sections',[]))>=3 for v in articles.values())
finished=sum(bool(v.get('result')) for v in articles.values())
print('EDITORIAL_RESULT',json.dumps({'events':len(events),'articles':len(articles),'future_with_form':future,'with_result':finished},ensure_ascii=False),flush=True)
if not articles:
    raise RuntimeError('No verified editorial data; refusing to overwrite archive')
out={'version':1,'updatedAt':NOW.isoformat(),'articles':articles}
old_path.write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
# Save the expanded public calendar without deleting previously published URLs.
calendar={'version':1,'updatedAt':NOW.isoformat(),'events':list(events.values())}
Path('pre-events.json').write_text(json.dumps(calendar,ensure_ascii=False,separators=(',',':')),encoding='utf-8')

