# -*- coding: utf-8 -*-
"""Монитор брендов Casher.

Раз в сутки (запуск руками): берёт список брендов из Google-таблицы, обходит
вкладку Reels каждого аккаунта в отдельном окне Edge, сохраняет срез, считает
медиану и кратность каждого ролика и собирает дашборд dashboard.html.

    py -X utf8 monitor.py            сбор (продолжает с места) + дашборд
    py -X utf8 monitor.py --build    только пересобрать дашборд из собранного
    py -X utf8 monitor.py --login    открыть окно и войти в Instagram руками
    py -X utf8 monitor.py --limit 5  тест на первых 5 аккаунтах
    py -X utf8 monitor.py --only a,b собрать только указанные ники
    py -X utf8 monitor.py --serve    только открыть дашборд с превью роликов

Прокси: переменная MONITOR_PROXY или первая строка proxy.txt (http://user:pass@host:port).
"""
import argparse, csv, datetime, glob, io, json, os, random, re, statistics, sys, time, urllib.parse, urllib.request, webbrowser

# Если Python запущен без папки пользователя (двойной щелчок по .bat), ищем библиотеки там сами
try:
    import playwright  # noqa: F401
except ImportError:
    ver = 'Python%d%d' % sys.version_info[:2]
    for d in glob.glob(os.path.join(os.environ.get('SystemDrive', 'C:') + '\\', 'Users', '*', 'AppData', 'Roaming', 'Python', ver, 'site-packages')):
        if os.path.isdir(os.path.join(d, 'playwright')) and d not in sys.path:
            sys.path.append(d)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
RAW = os.path.join(DATA, 'raw')
SESSION = os.path.join(ROOT, '_session')
PROFILE = os.path.join(SESSION, 'browser_tech')    # профиль технического IG-аккаунта
COOKIES = os.path.join(SESSION, 'ig_cookies.json')
PROXY_FILE = os.path.join(ROOT, 'proxy.txt')
USE_PROXY = True        # --no-proxy выключает
PAUSE = (2.5, 5.0)      # пауза между аккаунтами, с
THUMBS = os.path.join(ROOT, 'thumbs')
SHEET_ID = '14zR74sckY5aOOlqg5NNs87PLKf-h-jwHyjtgjK7GUAI'
SHEET_CSV = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0'
OWN = [dict(h='casher.collection', name='CASHER', region='Наш аккаунт', country='Россия', seg='Наш аккаунт',
            scale='', note='', site='', client='', src='Наш аккаунт', own=1)]
DEEP_SCROLLS = 10       # прокруток при глубоком сборе (--deep), ~10-12 Reels на прокрутку
MATURE_DAYS = 3         # медиана считается по роликам старше 3 дней (молодые ещё набирают)

A64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'


def posted_at(code):
    n = 0
    for ch in code[:11]:
        n = n * 64 + A64.index(ch)
    return datetime.datetime.fromtimestamp(((n >> 23) + 1314220021721) / 1000)


def num(t):
    """'48,4 тыс.' / '6 086' / '1,2 млн' / '12.5K' -> int."""
    if not t:
        return None
    t = t.replace('\xa0', ' ').replace(' ', ' ').strip()
    m = re.match(r'([\d ]+(?:[.,]\d+)?)\s*(тыс|млн|K|M|k|m)?', t)
    if not m or not m.group(1).strip():
        return None
    v = float(m.group(1).replace(' ', '').replace(',', '.'))
    mul = {'тыс': 1e3, 'K': 1e3, 'k': 1e3, 'млн': 1e6, 'M': 1e6, 'm': 1e6}.get(m.group(2) or '', 1)
    return int(v * mul)


def followers(txt):
    t = (txt or '').replace('\xa0', ' ')
    m = re.search(r'([\d ]+(?:[.,]\d+)?)\s*(тыс\.|млн|K|M)?\s*(подписчик|followers)', t)
    if not m:
        return 0
    return num(m.group(1) + (m.group(2) or '').replace('.', '')) or 0


def wjson(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def rjson(path, default):
    try:
        with open(path, encoding='utf8') as f:
            return json.load(f)
    except Exception:
        return default


# ---------------------------------------------------------------- бренды

def load_exclude():
    try:
        lines = open(os.path.join(ROOT, 'exclude.txt'), encoding='utf8').read().splitlines()
    except FileNotFoundError:
        return []
    return [l.split('#')[0].strip().lstrip('@').lower() for l in lines if l.split('#')[0].strip()]


def load_brands():
    cache = os.path.join(DATA, 'brands_cache.csv')
    text = None
    try:
        with urllib.request.urlopen(SHEET_CSV, timeout=30) as r:
            text = r.read().decode('utf8')
        open(cache, 'w', encoding='utf8').write(text)
        print('Список брендов взят из Google-таблицы')
    except Exception as e:
        print('Таблица недоступна (%s), беру сохранённую копию' % e)
        text = open(cache, encoding='utf8').read()
    out, seen = [], set(load_exclude())
    for row in csv.DictReader(io.StringIO(text)):
        h = (row.get('Instagram') or '').strip().lstrip('@').strip('/').split('/')[-1].lower()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append(dict(h=h, name=row.get('Бренд', '') or h, region=row.get('Регион', ''), country=row.get('Страна', ''),
                        seg=row.get('Сегмент', ''), scale=row.get('Масштаб', ''), note=row.get('Что в фокусе / заметка', ''),
                        site=row.get('Сайт', ''), client=row.get('Связь с нами', ''), src=row.get('Источник', ''),
                        own=int(h in {o['h'] for o in OWN})))
    for o in OWN:
        if o['h'] not in seen:
            out.insert(0, o)
    return out


# ---------------------------------------------------------------- сбор

GRAB_JS = r"""() => {
  const hdr = document.querySelector('header');
  const txt = hdr ? hdr.innerText.split('\n').filter(Boolean).join(' | ').slice(0, 700) : '';
  const seen = new Set(); const reels = [];
  document.querySelectorAll('a[href*="/reel/"]').forEach(a => {
    const code = (a.getAttribute('href').split('/reel/')[1] || '').split('/')[0];
    if (!code || seen.has(code)) return; seen.add(code);
    const pinned = !!a.querySelector('svg[aria-label*="акреп"], svg[aria-label*="inned"]');
    let th = '';
    const img = a.querySelector('img'); if (img && img.src) th = img.src;
    if (!th) { for (const d of a.querySelectorAll('div')) { const b = getComputedStyle(d).backgroundImage;
      if (b && b.startsWith('url(')) { th = b.slice(5, -2); break; } } }
    reels.push([code, a.innerText.replace(/\s+/g, ' ').trim(), pinned ? 1 : 0, th]);
  });
  const body = document.body.innerText.slice(0, 3000);
  return { txt, reels,
    nf: /Страница недоступна|Sorry, this page|isn't available/.test(body),
    priv: /Это закрытый аккаунт|This account is private/.test(body),
    limit: /Подождите несколько минут|Try again later|Повторите попытку позже/.test(body) };
}"""


def load_proxy():
    """Прокси для браузера: MONITOR_PROXY или первая строка proxy.txt. None - без прокси."""
    if not USE_PROXY:
        return None
    s = os.environ.get('MONITOR_PROXY', '').strip()
    if not s:
        try:
            with open(PROXY_FILE, encoding='utf8') as f:
                s = next((l.strip() for l in f if l.strip() and not l.lstrip().startswith('#')), '')
        except FileNotFoundError:
            s = ''
    if not s:
        return None
    u = urllib.parse.urlsplit(s)
    if u.scheme.startswith('socks') and u.username:
        sys.exit('Браузер не умеет SOCKS5 с паролем - укажи HTTP-адрес прокси (http://user:pass@host:port)')
    pr = {'server': '%s://%s:%d' % (u.scheme, u.hostname, u.port)}
    if u.username:
        pr['username'] = urllib.parse.unquote(u.username)
        pr['password'] = urllib.parse.unquote(u.password or '')
    return pr


def launch(p, profile, headless=True, **kw):
    """Окно Edge с прокси (если задан). Один профиль - один процесс."""
    pr = load_proxy()
    if pr:
        kw['proxy'] = pr
    return p.chromium.launch_persistent_context(profile, channel='msedge', headless=headless, locale='ru-RU', **kw)


def open_browser(p, headless=False):
    os.makedirs(PROFILE, exist_ok=True)
    ctx = launch(p, PROFILE, headless, viewport={'width': 1280, 'height': 900})
    print('Прокси: %s' % (load_proxy() or {}).get('server', 'нет'))
    have = any(c['name'] == 'sessionid' for c in ctx.cookies('https://www.instagram.com'))
    if not have and os.path.exists(COOKIES):
        ctx.add_cookies(rjson(COOKIES, []))
    return ctx


def logged_in(ctx):
    return any(c['name'] == 'sessionid' and c['value'] for c in ctx.cookies('https://www.instagram.com'))


def ensure_login(ctx, pg, wait_sec=300):
    pg.goto('https://www.instagram.com/', wait_until='domcontentloaded', timeout=30000)
    pg.wait_for_timeout(2500)
    if logged_in(ctx) and '/accounts/login' not in pg.url:
        return True
    print('\n>>> Войди в Instagram в открывшемся окне Edge (лучше технический аккаунт, не основной).')
    print('>>> Жду до %d минут...' % (wait_sec // 60))
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        pg.wait_for_timeout(3000)
        if logged_in(ctx) and '/accounts/login' not in pg.url:
            print('Вход выполнен')
            return True
    return False


def grab_one(pg, h, scrolls=2):
    pg.goto('https://www.instagram.com/' + h + '/reels/', wait_until='domcontentloaded', timeout=25000)
    if '/accounts/login' in pg.url or '/challenge' in pg.url:
        return {'login': True}
    try:
        pg.wait_for_selector('a[href*="/reel/"]', timeout=9000)
    except Exception:
        pass
    pg.wait_for_timeout(700)
    pg.mouse.move(5, 5)
    if scrolls <= 2:
        for _ in range(scrolls):
            pg.evaluate('() => scrollBy(0, 3500)')
            pg.wait_for_timeout(1200)
        return pg.evaluate(GRAB_JS)
    # глубокий сбор: крутим до конца и копим плитки по ходу (сетка может выгружать верхние)
    res, seen, still = None, {}, 0
    for _ in range(scrolls * 3):
        r = pg.evaluate(GRAB_JS)
        res = res or r
        before = len(seen)
        for rr in r['reels']:
            seen.setdefault(rr[0], rr)
        still = still + 1 if len(seen) == before else 0
        if still >= 3:
            break
        pg.evaluate('() => scrollTo(0, document.body.scrollHeight)')
        pg.wait_for_timeout(1600)
    res['reels'] = list(seen.values())
    return res


# ---------------------------------------------------------------- обложки
# ссылки IG CDN живут несколько дней, поэтому обложки храним у себя (thumbs/<код>.jpg, ~15 КБ)

def thumb_path(code):
    return os.path.join(THUMBS, code + '.jpg')


def save_thumb(ctx, code, url):
    if not url:
        return False
    try:
        r = ctx.request.get(url, timeout=15000)
        if not r.ok:
            return False
        from PIL import Image
        im = Image.open(io.BytesIO(r.body())).convert('RGB')
        im.thumbnail((270, 480))
        im.save(thumb_path(code), 'JPEG', quality=74)
        return True
    except Exception:
        return False


def embed_cover(pg, code):
    """Обложка со страницы встраивания ролика (страница рисуется скриптом, поэтому через окно браузера)."""
    try:
        pg.goto('https://www.instagram.com/reel/%s/embed/captioned/' % code, wait_until='domcontentloaded', timeout=20000)
        try:
            pg.wait_for_selector('img.EmbeddedMediaImage, video', timeout=6000)
        except Exception:
            pass
        return pg.evaluate("""() => { const i = document.querySelector('img.EmbeddedMediaImage');
            const v = document.querySelector('video'); return (i && i.src) || (v && v.poster) || ''; }""")
    except Exception:
        return ''


def fill_thumbs(ctx, limit=0):
    """Докачивает обложки роликов из базы, у которых ещё нет файла."""
    os.makedirs(THUMBS, exist_ok=True)
    hist = rjson(os.path.join(DATA, 'history.json'), {})
    todo = [c for c, e in hist.items() if c != '_seeded' and not os.path.exists(thumb_path(c))]
    todo.sort(key=lambda c: bool(hist[c].get('th')))   # сначала ролики совсем без обложки
    if limit:
        todo = todo[:limit]
    if not todo:
        return
    print('Докачиваю обложки: %d' % len(todo), flush=True)
    ok, pg = 0, ctx.new_page()
    for i, code in enumerate(todo, 1):
        if os.path.exists(thumb_path(code)):
            continue
        if save_thumb(ctx, code, hist[code].get('th')):
            ok += 1
        elif save_thumb(ctx, code, embed_cover(pg, code)):
            ok += 1
            time.sleep(random.uniform(0.3, 0.9))
        if i % 100 == 0:
            print('  обложки %d/%d, получено %d' % (i, len(todo), ok), flush=True)
    pg.close()
    print('Обложек получено: %d из %d' % (ok, len(todo)))


def collect(brands, args):
    from playwright.sync_api import sync_playwright
    os.makedirs(RAW, exist_ok=True)
    day = datetime.date.today().isoformat()
    path = os.path.join(RAW, day + '.json')
    snap = {} if args.fresh else rjson(path, {})
    if args.deep:
        todo = [b['h'] for b in brands if not snap.get(b['h'], {}).get('deep')]
    else:
        todo = [b['h'] for b in brands if b['h'] not in snap or snap[b['h']].get('err')]
    if args.only:
        want = [x.strip().lstrip('@').lower() for x in args.only.split(',')]
        todo = [h for h in want]
    if args.limit:
        todo = todo[:args.limit]
    print('Срез %s: уже собрано %d, осталось %d' % (day, len(snap), len(todo)))
    if not todo:
        return True
    with sync_playwright() as p:
        ctx = open_browser(p, args.hidden)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not ensure_login(ctx, pg):
            print('Не вошли в Instagram - сбор остановлен')
            ctx.close()
            return False
        empty_row, t0 = 0, time.time()
        for i, h in enumerate(todo, 1):
            try:
                r = grab_one(pg, h, DEEP_SCROLLS if args.deep else 2)
            except Exception as e:
                r = {'err': str(e)[:120]}
            if r.get('login'):
                print('\nInstagram разлогинил сессию. Запусти с --login и войди заново.')
                break
            if r.get('limit'):
                print('\nInstagram просит подождать. Сохранил, запусти позже - продолжит с места.')
                break
            os.makedirs(THUMBS, exist_ok=True)
            for rr in r.get('reels') or []:
                if len(rr) > 3 and rr[3] and not os.path.exists(thumb_path(rr[0])):
                    save_thumb(ctx, rr[0], rr[3])
            r['t'] = datetime.datetime.now().isoformat(timespec='seconds')
            if args.deep:
                r['deep'] = 1
            snap[h] = r
            wjson(path, snap)
            n = len(r.get('reels') or [])
            empty_row = empty_row + 1 if (n == 0 and not r.get('nf') and not r.get('priv')) else 0
            el = time.time() - t0
            print('[%d/%d] @%s: %s   (~%d мин осталось)' % (i, len(todo), h,
                  'не найден' if r.get('nf') else 'закрытый' if r.get('priv') else 'ошибка' if r.get('err') else '%d Reels' % n,
                  el / i * (len(todo) - i) / 60))
            if empty_row >= 6:
                print('\n6 пустых аккаунтов подряд - похоже, Instagram притормозил. Пауза 3 минуты...')
                pg.wait_for_timeout(180000)
                empty_row = 0
                for k in list(snap)[-6:]:
                    if not (snap[k].get('reels')):
                        snap.pop(k)
                wjson(path, snap)
            pg.wait_for_timeout(int(random.uniform(*PAUSE) * 1000))
        fill_thumbs(ctx, 400)
        ctx.close()
    return True


# ---------------------------------------------------------------- расчёт

def seed_history(hist):
    """Один раз подливаем замер 14.10 из первой сборки таблицы, чтобы сразу был прирост."""
    if hist.get('_seeded') == 2:
        return
    old = rjson(os.path.join(ROOT, '..', '..', '..', '.playwright-mcp', 'ig_ideas', 'grab_all_fixed.json'), {})
    for h, v in old.items():
        for rr in v.get('reels', []):
            # в замере 11.10 просмотры 1 000-9 999 сломаны - берём только от 10 000
            if isinstance(rr[1], int) and rr[1] >= 10000:
                e = hist.setdefault(rr[0], {'h': h, 'v': {}})
                e['v'].setdefault('2026-10-11', rr[1])
    src = os.path.join(ROOT, '..', '..', '..', '.playwright-mcp', 'brands_an2', 'grab3.json')
    o = rjson(src, None)
    if isinstance(o, str):
        o = json.loads(o)
    for h, v in (o or {}).items():
        for rr in v.get('reels', []):
            code, t = rr[0], rr[1]
            val = num(t)
            if val is None:
                continue
            e = hist.setdefault(code, {'h': h, 'v': {}})
            e['v'].setdefault('2026-10-14', val)
    hist['_seeded'] = 2


def build():
    os.makedirs(DATA, exist_ok=True)
    brands = load_brands()
    meta = {b['h']: b for b in brands}
    days = sorted(f[:-5] for f in os.listdir(RAW) if f.endswith('.json')) if os.path.isdir(RAW) else []
    if not days:
        print('Нет ни одного среза - сначала запусти сбор')
        return None
    day = days[-1]
    snap = rjson(os.path.join(RAW, day + '.json'), {})
    hist_path = os.path.join(DATA, 'history.json')
    hist = rjson(hist_path, {})
    seed_history(hist)
    # история по всем срезам (на случай пересборки)
    for d in days:
        s = snap if d == day else rjson(os.path.join(RAW, d + '.json'), {})
        for h, r in s.items():
            for rr in r.get('reels') or []:
                code, t, pin = rr[0], rr[1], rr[2]
                th = rr[3] if len(rr) > 3 else ''
                val = num(t)
                if val is None:
                    continue
                e = hist.setdefault(code, {'h': h, 'v': {}})
                e['h'] = h
                e['v'][d] = val
                e['pin'] = pin
                if th and d == day:
                    e['th'] = th
    wjson(hist_path, hist)

    def th_of(code, e):
        return 'thumbs/%s.jpg' % code if os.path.exists(thumb_path(code)) else e.get('th', '')

    today = datetime.date.fromisoformat(day)
    notes = rjson(os.path.join(ROOT, 'notes.json'), {})
    accounts, reels = [], []
    by_h = {}
    for code, e in hist.items():
        if code != '_seeded':
            by_h.setdefault(e['h'], []).append(code)
    for h, b in meta.items():
        r = snap.get(h)
        acc = dict(h=h, name=b['name'], region=b['region'], country=b['country'], seg=b['seg'], scale=b['scale'],
                   note=b['note'], site=b['site'], client=b['client'], src=b['src'], own=b.get('own', 0))
        if not r:
            acc['state'] = 'нет в срезе'
            accounts.append(acc)
            continue
        acc['state'] = 'не найден' if r.get('nf') else 'закрытый' if r.get('priv') else 'ошибка' if r.get('err') else 'ок'
        acc['f'] = followers(r.get('txt', ''))
        items = []
        for rr in r.get('reels') or []:
            code, t, pin = rr[0], rr[1], rr[2]
            val = num(t)
            if val is None:
                continue
            pdt = posted_at(code)
            items.append(dict(code=code, views=val, pin=pin, posted=pdt.isoformat(timespec='minutes'),
                              age=(today - pdt.date()).days))
        free = [x for x in items if not x['pin']]
        # медиана - по 18 последним Reels, чтобы не зависела от глубины сбора
        last18 = sorted(free, key=lambda x: x['posted'], reverse=True)[:18]
        mature = [x['views'] for x in last18 if x['age'] >= MATURE_DAYS] or [x['views'] for x in last18]
        med = int(statistics.median(mature)) if len(mature) >= 3 else 0
        acc['med'] = med
        acc['n'] = len(items)
        acc['n30'] = sum(1 for x in free if x['age'] <= 30)
        acc['last'] = max((x['posted'][:10] for x in free), default='')
        # «новое» = выложено не раньше прошлого замера этого аккаунта
        acc_prev = max((d for x in free for d in hist.get(x['code'], {}).get('v', {}) if d < day), default='')
        best, hot30 = 0, 0
        for x in free:
            x['x'] = round(x['views'] / med, 2) if med else None
            e = hist.get(x['code'], {})
            prev = sorted(d for d in e.get('v', {}) if d < day)
            if prev:
                pd = prev[-1]
                gap = max((today - datetime.date.fromisoformat(pd)).days, 1)
                x['gain'] = int((x['views'] - e['v'][pd]) / gap)
                x['prev'] = pd
            if x['age'] <= 30 and x['x']:
                best = max(best, x['x'])
                hot30 += x['x'] >= 3
            if True:
                rec = dict(code=x['code'], h=h, views=x['views'], x=x['x'], age=x['age'], posted=x['posted'],
                           gain=x.get('gain'), prev=x.get('prev'), th=th_of(x['code'], e), new=(x['posted'][:10] >= acc_prev) if acc_prev else x['age'] <= 1)
                if x['code'] in notes:
                    rec['note'] = notes[x['code']]
                reels.append(rec)
        # ролики, которые были в прошлых замерах, но уже не попали в сетку сегодняшнего среза
        now_codes = {x['code'] for x in items}
        for code in by_h.get(h, []):
            if code in now_codes:
                continue
            e = hist[code]
            if e.get('pin'):
                continue
            last_d = max(e['v'])
            pdt = posted_at(code)
            v = e['v'][last_d]
            rec = dict(code=code, h=h, views=v, x=round(v / med, 2) if med else None, age=(today - pdt.date()).days,
                       posted=pdt.isoformat(timespec='minutes'), gain=None, prev=None, th=th_of(code, e), new=False,
                       seen=last_d)
            if code in notes:
                rec['note'] = notes[code]
            reels.append(rec)
        mine = [x for x in reels if x['h'] == h]
        acc['nall'] = len(mine)
        acc['since'] = min((x['posted'][:10] for x in mine), default='')
        acc['best30'] = best
        acc['hot30'] = hot30
        acc['mx30'] = max((x['views'] for x in free if x['age'] <= 30), default=0)
        accounts.append(acc)
    ok = sum(1 for a in accounts if a.get('state') == 'ок')
    times = [r.get('t', '') for r in snap.values() if r.get('t')]
    out = dict(day=day, built=datetime.datetime.now().isoformat(timespec='minutes'), started=min(times, default=''),
               finished=max(times, default=''), total=len(meta), ok=ok, days=days,
               accounts=accounts, reels=reels)
    wjson(os.path.join(DATA, 'latest.json'), out)
    with open(os.path.join(ROOT, 'dashboard_data.js'), 'w', encoding='utf8') as f:
        f.write('window.MONITOR = ')
        json.dump(out, f, ensure_ascii=False)
        f.write(';\n')
    # CSV залётов - удобно открыть в Excel/Google Таблицах
    with open(os.path.join(DATA, 'zalety.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Дата ролика', 'Бренд', 'Instagram', 'Регион', 'Сегмент', 'Просмотры', 'Медиана', 'Кратность',
                    'Прирост в сутки', 'Ссылка'])
        for x in sorted((x for x in reels if x['x'] and x['x'] >= 3), key=lambda x: -x['x']):
            a = meta[x['h']]
            w.writerow([x['posted'][:10], a['name'], '@' + x['h'], a['region'], a['seg'], x['views'],
                        int(x['views'] / x['x']), x['x'], x.get('gain') or '', 'https://www.instagram.com/reel/%s/' % x['code']])
    print('Дашборд собран: %d аккаунтов в срезе, %d роликов в базе' % (ok, len(reels)))
    return out


# ---------------------------------------------------------------- локальный сервер дашборда
# Отдаёт папку и /api/video?code=<код> - прямую ссылку на mp4 ролика. Ссылку берём со страницы
# встраивания в скрытом браузере без входа в аккаунт, сами видео не храним (кэшируем только ссылки).

PORT = 5612
LOGGED_VIDEO_JS = r"""async (code) => {
  const t = await (await fetch('/reel/' + code + '/', {headers: {Accept: 'text/html'}})).text();
  const i = t.indexOf('"code":"' + code + '"');
  const vs = []; let k = -1; while ((k = t.indexOf('"video_versions":[', k + 1)) >= 0) vs.push(k);
  if (!vs.length) return '';
  const j = i >= 0 ? vs.reduce((a, b) => Math.abs(b - i) < Math.abs(a - i) ? b : a) : vs[0];
  const m = t.slice(j, j + 3000).match(/"url":"([^"]+)"/);
  return m ? JSON.parse('"' + m[1] + '"') : '';
}"""
CODE_RE = re.compile(r'^[A-Za-z0-9_-]{5,20}$')


def serve():
    import http.server, threading, queue, tempfile, urllib.parse, socket
    url_main = 'http://localhost:%d/dashboard.html' % PORT
    def busy():
        with socket.socket() as sk:
            return sk.connect_ex(('127.0.0.1', PORT)) == 0

    if busy():
        # старый сервер мог остаться со вчера (после сна его браузеры мертвы) - просим его закрыться
        print('Закрываю прошлый запуск дашборда...')
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/api/quit' % PORT, timeout=5).read()
        except Exception:
            pass
        for _ in range(20):
            if not busy():
                break
            time.sleep(0.5)
        if busy():
            print('Порт %d занят другим окном. Закрой старое чёрное окно дашборда и запусти снова.' % PORT)
            webbrowser.open(url_main)
            return
    cache_path = os.path.join(DATA, 'video_cache.json')
    cache = rjson(cache_path, {})
    lock = threading.Lock()
    jobs = queue.LifoQueue()      # последний наведённый ролик - первым
    inflight = {}

    def expiry(u):
        m = re.search(r'[?&]oe=([0-9A-Fa-f]+)', u)
        return int(m.group(1), 16) if m else time.time() + 6 * 3600

    alive = [0]

    def worker():
        # браузер упал (сон компьютера, обновление Edge) - поднимаем заново; сдаёмся после 5 быстрых падений подряд
        fails = 0
        try:
            while fails < 5:
                t0 = time.time()
                try:
                    run_worker()
                except Exception as e:
                    print('Превью: скрытый браузер упал (%s), перезапускаю' % str(e)[:120], flush=True)
                fails = fails + 1 if time.time() - t0 < 120 else 1
                time.sleep(3)
            print('Превью не работает: браузер падает при запуске. Закрой окно и запусти ДАШБОРД.bat снова.', flush=True)
        finally:
            with lock:
                alive[0] -= 1

    def run_worker():
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = launch(p, tempfile.mkdtemp())
            pg = ctx.new_page()
            # само видео и картинки в скрытом браузере не качаем - нужна только ссылка
            skip = lambda r: r.abort() if r.request.resource_type in ('media', 'image', 'font') else r.continue_()
            pg.route('**/*', skip)
            lp = None                 # запасной путь под входом - для роликов, где бренд запретил встраивание

            def logged_url(code):
                nonlocal lp
                if lp is None:
                    if not os.path.exists(COOKIES):
                        return ''
                    c2 = launch(p, tempfile.mkdtemp())
                    c2.add_cookies(rjson(COOKIES, []))
                    lp = c2.new_page()
                    lp.route('**/*', skip)
                    lp.goto('https://www.instagram.com/robots.txt', timeout=20000)
                return lp.evaluate(LOGGED_VIDEO_JS, code) or ''

            errs = 0
            while True:
                code, box, ev = jobs.get()
                if time.time() - box['t'] > 10:       # пользователь уже ушёл с карточки
                    ev.set()
                    continue
                u = ''
                try:
                    pg.goto('https://www.instagram.com/reel/%s/embed/' % code, wait_until='domcontentloaded', timeout=20000)
                    try:
                        pg.wait_for_selector('video', timeout=6000)
                    except Exception:
                        pass
                    u = pg.evaluate("() => { const v = document.querySelector('video'); return v ? (v.currentSrc || v.src || '') : ''; }")
                    errs = 0
                except Exception as e:
                    errs += 1
                    if errs >= 3 or pg.is_closed():   # браузер больше не отвечает - пусть worker() поднимет новый
                        box['url'] = ''
                        ev.set()
                        raise RuntimeError(str(e)[:120])
                if not u.startswith('http'):
                    try:
                        u = logged_url(code)
                    except Exception:
                        u = ''
                if u.startswith('http'):
                    with lock:
                        cache[code] = [u, expiry(u)]
                        try:
                            wjson(cache_path, cache)
                        except OSError:       # файл кэша занят (антивирус) - не страшно, запишем со следующей ссылкой
                            pass
                box['url'] = u
                ev.set()

    for _ in range(2):            # два скрытых браузера - ссылки достаются параллельно
        alive[0] += 1
        threading.Thread(target=worker, daemon=True).start()

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ROOT, **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            # страницу и данные браузер всегда сверяет с диском, иначе после обновления показывает старую версию
            if not self.path.startswith('/thumbs/'):
                self.send_header('Cache-Control', 'no-cache')
            super().end_headers()

        def do_GET(self):
            if self.path.startswith('/api/quit'):     # новый запуск ДАШБОРД.bat закрывает старый
                self.send_response(200)
                self.end_headers()
                threading.Thread(target=srv.shutdown, daemon=True).start()
                return
            if not self.path.startswith('/api/video'):
                return super().do_GET()
            code = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('code', [''])[0]
            u = ''
            if CODE_RE.match(code):
                with lock:
                    c = cache.get(code)
                if c and c[1] > time.time() + 600:
                    u = c[0]
                elif alive[0] > 0:
                    with lock:
                        job = inflight.get(code)
                        if not job:
                            job = ({'t': time.time()}, threading.Event())
                            inflight[code] = job
                            jobs.put((code, job[0], job[1]))
                    job[1].wait(25)
                    with lock:
                        inflight.pop(code, None)
                    u = job[0].get('url', '')
            body = json.dumps({'url': u}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H)
    print('\nДашборд открыт: %s' % url_main)
    print('Пока смотришь дашборд, не закрывай это окно (в нём работает превью роликов).')
    webbrowser.open(url_main)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--login', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--only', default='')
    ap.add_argument('--fresh', action='store_true')
    ap.add_argument('--deep', action='store_true', help='прокрутить вкладку Reels глубже (~100 роликов на бренд)')
    ap.add_argument('--hidden', action='store_true', help='без окна браузера')
    ap.add_argument('--no-open', action='store_true')
    ap.add_argument('--thumbs', action='store_true', help='только докачать обложки')
    ap.add_argument('--serve', action='store_true', help='только открыть дашборд с превью роликов')
    ap.add_argument('--no-proxy', action='store_true', help='не использовать прокси из proxy.txt')
    args = ap.parse_args()
    global USE_PROXY
    USE_PROXY = not args.no_proxy
    os.makedirs(DATA, exist_ok=True)
    if args.serve:
        serve()
        return
    if args.login:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = open_browser(p)
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            print('OK' if ensure_login(ctx, pg, 600) else 'Вход не выполнен')
            ctx.close()
        return
    if args.thumbs:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = open_browser(p, args.hidden)
            fill_thumbs(ctx, args.limit)
            ctx.close()
    elif not args.build:
        collect(load_brands(), args)
    if build() and not args.no_open:
        serve()


if __name__ == '__main__':
    main()
