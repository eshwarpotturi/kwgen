#!/usr/bin/env python3
"""
kwgen: build a detailed search-keyword list for an anime title (IP).

Stages
  1. fetch    - structured facts from AniList (free, no key): names, synonyms, characters,
                Japanese voice actors, staff, studios, related works, tags, similar titles, official X account.
  2. expand   - OPTIONAL LLM stage (off by default): story arcs, events, iconic terms, fan slang, music, games
                (including PlayStation), merchandise and collaborations, real-world places, live events,
                similar shows, ambiguity warnings. Costs money, so it needs --llm and --approve-spend.
  3. build    - merges everything into one table, adds ambiguity flags and ready-to-send query strings.
  4. validate - reads a sample of real posts (CSV from the provider), measures which keywords hit,
                and suggests new hashtags and terms from the posts (precision / recall check).

Examples
  python kwgen.py build "Kaiju No. 8" "Demon Slayer" --out keywords.xlsx
  python kwgen.py build "Laid-Back Camp" --llm openrouter --model anthropic/claude-sonnet-4.5 --estimate
  python kwgen.py build "Laid-Back Camp" --llm openrouter --model anthropic/claude-sonnet-4.5 --approve-spend
  python kwgen.py validate --keywords keywords.xlsx --posts sample_posts.csv --title "Kaiju No. 8"
"""
import argparse, csv, datetime, json, os, re, sys, time, urllib.request, urllib.error
from collections import Counter, OrderedDict

ANILIST = "https://graphql.anilist.co"
QUERY = """
query($search:String){ Media(search:$search,type:ANIME,sort:SEARCH_MATCH){
 id idMal title{romaji english native} synonyms format seasonYear season episodes status genres
 tags{name rank isMediaSpoiler}
 studios(isMain:true){nodes{name}}
 staff(perPage:25,sort:RELEVANCE){edges{role node{name{full native}}}}
 characters(perPage:25,sort:[ROLE,RELEVANCE]){edges{role node{name{full native alternative}}
   voiceActors(language:JAPANESE){name{full native}}}}
 relations{edges{relationType node{title{romaji english native} format type}}}
 externalLinks{site url type}
 recommendations(perPage:8,sort:RATING_DESC){nodes{mediaRecommendation{title{romaji english}}}}
 streamingEpisodes{title}
}}"""

def log(msg, done=None, total=None):
    prog = f" [{done}/{total}]" if done is not None else ""
    print(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{prog} {msg}", flush=True)

def post_json(url, payload, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "kwgen/1.0 (keyword research tool)", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def script(term):
    return "ja" if re.search(r"[぀-ヿ一-鿿]", term) else "en"

def fetch(title):
    for attempt in range(3):
        try:
            return post_json(ANILIST, {"query": QUERY, "variables": {"search": title}})["data"]["Media"]
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(20); continue
            sys.exit(f"AniList error {e.code}: {e.read().decode()[:400]}")
    raise RuntimeError("AniList rate limit")

def clean_hashtag(s):
    s = re.sub(r"[^\w぀-ヿ一-鿿 ]", " ", s)
    parts = [p for p in s.split() if p]
    return "#" + "".join(p if script(p) == "ja" else p.capitalize() for p in parts) if parts else None

KEEP_STAFF = ("Director", "Original Creator", "Story", "Music", "Series Composition", "Character Design", "Sound Director", "Chief Animation")
def structured(media, max_chars=15):
    rows = []
    def add(cat, term, lang=None, src="AniList", conf="high", note=""):
        if term and term.strip() and not re.search(r"[\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff\u0e00-\u0e7f]", term):
            rows.append(OrderedDict(category=cat, term=term.strip(), language=lang or script(term), source=src, confidence=conf, note=note))
    t = media["title"]
    names = [t.get("english"), t.get("romaji"), t.get("native")] + (media.get("synonyms") or [])
    for n in names: add("Title names", n)
    for n in list(names):
        if n and ":" in n: add("Title names", n.split(":")[0], note="short form", conf="medium")
    for n in [t.get("english"), t.get("romaji"), t.get("native")]:
        if n: add("Hashtags", clean_hashtag(n), note="generated candidate; confirm in pilot", conf="medium")
    for l in media.get("externalLinks") or []:
        if l["site"] in ("Twitter", "X") and l.get("url"):
            h = l["url"].rstrip("/").split("/")[-1]
            add("Official account", "@" + h, "en", conf="high", note="official X account")
    ch = media["characters"]["edges"][:max_chars]
    for i, e in enumerate(ch):
        n = e["node"]["name"]
        role = e["role"].title()
        add("Characters", n["full"], "en", note=role)
        if n.get("native"): add("Characters", n["native"], "ja", note=role)
        for a in (n.get("alternative") or [])[:2]: add("Characters", a, None, conf="medium", note=f"alias, {role}")
        first = (n["full"] or "").split()[0] if n["full"] else ""
        if first and len(first) >= 4 and role == "Main": add("Characters", first, "en", conf="low", note="given name only; needs title co-term")
        for va in e.get("voiceActors") or []:
            add("Voice actors", va["name"]["full"], "en", note=f"voices {n['full']}")
            if va["name"].get("native"): add("Voice actors", va["name"]["native"], "ja", note=f"voices {n['full']}")
        if (i + 1) % 5 == 0: log(f"characters processed for {t.get('romaji')}", i + 1, len(ch))
    for e in media["staff"]["edges"]:
        if any(k in e["role"] for k in KEEP_STAFF):
            add("Creators and crew", e["node"]["name"]["full"], "en", note=e["role"])
            if e["node"]["name"].get("native"): add("Creators and crew", e["node"]["name"]["native"], "ja", note=e["role"])
    for s in media["studios"]["nodes"]: add("Creators and crew", s["name"], "en", note="studio")
    for e in media["relations"]["edges"]:
        n = e["node"]["title"]
        for v in (n.get("english"), n.get("romaji"), n.get("native")):
            add("Franchise and related works", v, note=f"{e['relationType'].lower()} ({e['node'].get('format')})", conf="medium")
    for tg in media["tags"]:
        if tg["rank"] >= 50 and not tg["isMediaSpoiler"]: add("Genre and theme", tg["name"], "en", conf="medium", note=f"rank {tg['rank']}; for theme analysis, not title search")
    for g in media["genres"]: add("Genre and theme", g, "en", conf="medium", note="genre")
    for r in media["recommendations"]["nodes"]:
        m = r.get("mediaRecommendation")
        if m: add("Similar titles", m["title"].get("english") or m["title"]["romaji"], "en", conf="medium", note="for overlap and co-mention analysis")
    for ep in (media.get("streamingEpisodes") or [])[:12]:
        add("Episodes", ep["title"], "en", conf="low", note="streaming episode title")
    return rows

# ---------------- LLM stage (optional, paid) ----------------
CATS = OrderedDict([
 ("Story arcs and events", "arcs, seasons, movies, key episodes and moments fans name"),
 ("Iconic terms and quotes", "signature moves, catchphrases, in-world terms fans quote"),
 ("Fan slang and memes", "nicknames, ship names, meme phrases, fandom names (English and Japanese)"),
 ("Hashtags", "official and popular fan hashtags, English and Japanese, including season or arc hashtags"),
 ("Music", "opening and ending songs, artists, soundtrack terms"),
 ("Games and adaptations", "video games (name the platform, include PlayStation titles), mobile games, stage plays, live-action"),
 ("Merchandise and collaborations", "known product lines, brand and restaurant collaborations, figure lines, retailers"),
 ("Live events and places", "exhibitions, concerts, conventions, real-world pilgrimage places"),
 ("Voice actors other roles", "other very popular roles of the main voice actors that draw the same fans"),
 ("Platform and gaming overlap", "PlayStation, PS5, console and gaming terms that link to this title (collabs, in-game items)"),
 ("Ambiguity warnings", "terms in the list that also mean something else; give words to exclude"),
])
def prompt_for(media, facts):
    t = media["title"]
    brief = {c: [r["term"] for r in facts if r["category"] == c][:12] for c in ("Title names", "Characters", "Voice actors", "Creators and crew")}
    return ("You build search keyword lists for X (Twitter) pulls about the anime '%s' (%s). "
            "Known facts (from AniList): %s\n\nReturn ONLY JSON: an object whose keys are exactly these categories, each a list of objects "
            "{\"term\":..., \"language\":\"en\"|\"ja\", \"confidence\":\"high\"|\"medium\"|\"low\", \"note\":...}. Categories: %s. "
            "Rules: only terms you are confident exist; no invented titles; give Japanese terms in Japanese script; at most 15 per category; "
            "for 'Ambiguity warnings' put the risky term in 'term' and the words to exclude in 'note'."
            % (t.get("english") or t.get("romaji"), t.get("native"), json.dumps(brief, ensure_ascii=False),
               "; ".join(f"{k} = {v}" for k, v in CATS.items())))

def llm_call(provider, model, prompt):
    if provider == "anthropic":
        key = os.environ["ANTHROPIC_API_KEY"]
        out = post_json("https://api.anthropic.com/v1/messages", {"model": model, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]},
                        {"x-api-key": key, "anthropic-version": "2023-06-01"}, 120)
        return out["content"][0]["text"]
    url = {"openrouter": "https://openrouter.ai/api/v1/chat/completions"}.get(provider, os.environ.get("LLM_BASE_URL", "") + "/chat/completions")
    key = os.environ.get("OPENROUTER_API_KEY" if provider == "openrouter" else "LLM_API_KEY", "")
    out = post_json(url, {"model": model, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]}, {"Authorization": "Bearer " + key}, 120)
    return out["choices"][0]["message"]["content"]

def expand(media, facts, args, done, total):
    p = prompt_for(media, facts)
    est_in, est_out = len(p) / 3.5, 2500
    cost = (est_in * args.price_in + est_out * args.price_out) / 1e6
    log(f"LLM estimate for this title: about {est_in:.0f} input and {est_out} output tokens, about USD {cost:.3f} (INR {cost*100:.1f}) at your stated prices", done, total)
    if args.estimate: return []
    if not args.approve_spend: sys.exit("Stopped: add --approve-spend to run the paid LLM stage.")
    raw = llm_call(args.llm, args.model, p)
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0))
    rows = []
    for cat, items in data.items():
        for it in items:
            rows.append(OrderedDict(category=cat, term=it.get("term", "").strip(), language=it.get("language") or script(it.get("term", "")), source=f"LLM ({args.model})", confidence=it.get("confidence", "low"), note=it.get("note", "")))
    return [r for r in rows if r["term"]]

SHARED = [
 ("Merchandise words", "en", "merch, figure, plush, plushie, hoodie, t-shirt, keychain, acrylic stand, nendoroid, poster, pre-order, preorder, where to buy, collab, limited edition, controller skin"),
 ("Merchandise words", "ja", "グッズ, フィギュア, ぬいぐるみ, パーカー, Tシャツ, キーホルダー, アクスタ, 予約, 買った, 欲しい, コラボ, 限定"),
 ("Platform and gaming overlap", "en", "PlayStation, PS5, PS4, PS Plus, DualSense, State of Play, PlayStation Store, PSN avatar, game adaptation, anime game"),
 ("Platform and gaming overlap", "ja", "プレイステーション, PS5, ゲーム化, アニメゲーム"),
 ("Streaming and platforms", "en", "Crunchyroll, Funimation, Netflix, Hulu, Prime Video, HIDIVE, simulcast, English dub, sub vs dub"),
 ("Brands and retailers", "en", "Hot Topic, BoxLunch, Funko, Good Smile, Bandai, Crunchyroll Store, Amazon, Target, Walmart, Uniqlo UT"),
]
def shared_rows():
    out = []
    for cat, lang, terms in SHARED:
        for t in [x.strip() for x in terms.split(",")]:
            out.append(OrderedDict(category=cat, term=t, language=lang, source="shared list (same for every title)", confidence="high", note="used with a title word; not a title keyword"))
    return out

# ---------------- build ----------------
COMMON = {"camp", "demon", "slayer", "case", "closed", "detective", "kaiju", "rin", "ran", "ai", "ena", "aoi"}
def finish(rows):
    seen, out = set(), []
    for r in rows:
        k = (r["category"], r["term"].lower())
        if k in seen: continue
        seen.add(k)
        t = r["term"]
        words = re.findall(r"\w+", t.lower())
        ambiguous = r["category"] in ("Characters", "Title names", "Voice actors") and (len(words) == 1 and words[0] in COMMON or (len(t) <= 3 and script(t) == "en" and not t.startswith(("#", "@"))))
        r["needs_title_coterm"] = "yes" if ambiguous or "needs title co-term" in r["note"] or r["category"] in ("Voice actors", "Genre and theme", "Similar titles", "Episodes", "Merchandise words", "Platform and gaming overlap", "Streaming and platforms", "Brands and retailers") else "no"
        out.append(r)
    return out

def query_strings(title, rows, maxlen=450):
    """Group terms into ready queries. Latin terms and Japanese terms are separate; terms that need a title co-term are joined to the core terms."""
    core = [r["term"] for r in rows if r["category"] in ("Title names", "Hashtags", "Official account") and r["needs_title_coterm"] == "no"]
    def q(term): return f'"{term}"' if " " in term else term
    out = []
    groups = OrderedDict([
        ("A core (names, hashtags, account)", [t for t in core]),
        ("B characters", [r["term"] for r in rows if r["category"] == "Characters" and r["needs_title_coterm"] == "no"]),
        ("C creators and crew", [r["term"] for r in rows if r["category"] == "Creators and crew"]),
        ("D arcs, terms, slang, music", [r["term"] for r in rows if r["category"] in ("Story arcs and events", "Iconic terms and quotes", "Fan slang and memes", "Music") and r["confidence"] != "low"]),
        ("E games, places, events", [r["term"] for r in rows if r["category"] in ("Games and adaptations", "Live events and places", "Franchise and related works")]),
        ("F voice actors (analyse as a separate stream)", [r["term"] for r in rows if r["category"] == "Voice actors"]),
        ("G platform and gaming overlap (use with a title word)", [r["term"] for r in rows if r["category"] == "Platform and gaming overlap"]),
        ("H merchandise (use with a title word)", [r["term"] for r in rows if r["category"] in ("Merchandise words", "Brands and retailers", "Merchandise and collaborations")]),
    ])
    for g, terms in groups.items():
        for lang in ("en", "ja"):
            ts = [q(t) for t in terms if script(t) == lang]
            cur = []
            for t in ts:
                if len(" OR ".join(cur + [t])) + 12 > maxlen and cur:
                    out.append((g, lang, "(" + " OR ".join(cur) + f") lang:{lang} -is:retweet")); cur = []
                cur.append(t)
            if cur: out.append((g, lang, "(" + " OR ".join(cur) + f") lang:{lang} -is:retweet"))
    return out

def write_xlsx(path, per_title):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook(); ws = wb.active; ws.title = "Keywords"
    cols = ["title", "category", "term", "language", "source", "confidence", "needs_title_coterm", "note"]
    ws.append(cols)
    for t, rows in per_title.items():
        for r in rows: ws.append([t] + [r[c] for c in cols[1:]])
    wq = wb.create_sheet("Queries"); wq.append(["title", "query group", "language", "query (check the provider's length limit)"])
    for t, rows in per_title.items():
        for g, lang, q in query_strings(t, rows): wq.append([t, g, lang, q])
    wc = wb.create_sheet("Summary"); wc.append(["title", "category", "terms"])
    for t, rows in per_title.items():
        for c, n in Counter(r["category"] for r in rows).items(): wc.append([t, c, n])
    for w in wb:
        for c in w[1]: c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="2F5597")
        for col, wd in zip("ABCDEFGH", (24, 30, 38, 10, 22, 12, 18, 50)): w.column_dimensions[col].width = wd
        for row in w.iter_rows():
            for c in row: c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)

def cmd_build(a):
    per, total = OrderedDict(), len(a.titles)
    for i, t in enumerate(a.titles, 1):
        log(f"fetching {t}", i, total)
        m = fetch(t)
        log(f"matched AniList #{m['id']}: {m['title']['romaji']} / {m['title']['native']} ({m.get('seasonYear')})", i, total)
        rows = structured(m) + shared_rows()
        if a.llm: rows += expand(m, rows, a, i, total)
        per[t] = finish(rows)
        log(f"{t}: {len(per[t])} keywords in {len(set(r['category'] for r in per[t]))} categories", i, total)
        time.sleep(1.2)
    write_xlsx(a.out, per); log(f"written {a.out}")

def cmd_validate(a):
    from openpyxl import load_workbook
    ws = load_workbook(a.keywords)["Keywords"]
    terms = [(r[2], r[1], r[6]) for r in ws.iter_rows(min_row=2, values_only=True) if r[0] == a.title]
    posts = [row["text"] for row in csv.DictReader(open(a.posts, encoding="utf-8"))]
    log(f"{len(posts)} posts, {len(terms)} keywords for {a.title}")
    core = [t.lower() for t, c, n in terms if c in ("Title names", "Hashtags", "Official account") and n == "no"]
    hits, only_weak = Counter(), Counter()
    tags, words = Counter(), Counter()
    known = {t.lower() for t, _, _ in terms}
    for i, p in enumerate(posts, 1):
        low = p.lower()
        has_core = any(c in low for c in core)
        for t, c, n in terms:
            if t.lower() in low:
                hits[t] += 1
                if not has_core: only_weak[t] += 1
        for h in re.findall(r"[#＃][\w぀-ヿ一-鿿]+", p):
            if h.lower() not in known: tags[h] += 1
        if i % 1000 == 0: log("scanning posts", i, len(posts))
    print("\nKeyword hits (hits / hits without any core title term = precision risk)")
    for t, c, n in sorted(terms, key=lambda x: -hits[x[0]]):
        if hits[t]: print(f"  {t:35} {c:28} {hits[t]:5} / {only_weak[t]:5}")
    print("\nNo hits (drop or keep for recall):", ", ".join(t for t, c, n in terms if not hits[t])[:600])
    print("\nNew hashtag candidates (not in list):")
    for h, n in tags.most_common(25): print(f"  {h:30} {n}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("titles", nargs="+"); b.add_argument("--out", default="keywords.xlsx")
    b.add_argument("--llm", choices=["openrouter", "anthropic", "openai-compatible"]); b.add_argument("--model", default="")
    b.add_argument("--estimate", action="store_true", help="print cost estimate only; no paid call")
    b.add_argument("--approve-spend", action="store_true", help="explicit approval for paid LLM calls")
    b.add_argument("--price-in", type=float, default=3.0, help="USD per million input tokens"); b.add_argument("--price-out", type=float, default=15.0)
    v = sub.add_parser("validate"); v.add_argument("--keywords", required=True); v.add_argument("--posts", required=True); v.add_argument("--title", required=True)
    a = ap.parse_args()
    {"build": cmd_build, "validate": cmd_validate}[a.cmd](a)
