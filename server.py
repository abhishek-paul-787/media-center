#!/usr/bin/env python3
"""Media Center - local OTT-style browser for your movie and TV library. Stdlib only (needs ffmpeg/ffprobe)."""
import hashlib, json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

APP = Path(__file__).resolve().parent
DATA = APP / "data"
for d in ("img", "subs", "subcache"):
    (DATA / d).mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP / "config.json"
DB_PATH = DATA / "library.json"

DEFAULT_CONFIG = {
    "libraries": [   # kind: "movie", "tv" or "auto" (auto = decide by SxxExx in filename)
        {"path": "~/Media/Movies", "kind": "movie"},
        {"path": "~/Media/TV Shows", "kind": "tv"},
    ],
    "host": "127.0.0.1",
    "port": 8765,
    "tmdb_key": "",              # TMDB API key (v3) or read-access token (v4)
    "opensubtitles_key": "",     # OpenSubtitles.com API key
    "opensubtitles_user": "",
    "opensubtitles_pass": "",
    "site_name": "Media Center",
    "subtitle_languages": ["en", "es", "fr"],   # shown as fetch buttons
    "auto_subtitle_languages": ["en"],          # auto-fetched when you open a movie
}
if not CONFIG_PATH.exists():
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
CFG = {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
if isinstance(CFG.get("library"), str) and "libraries" not in json.loads(CONFIG_PATH.read_text()):
    CFG["libraries"] = [{"path": CFG["library"], "kind": "auto"}]


HW_OK = os.path.exists("/dev/dri/renderD128")
CS_NUM = {"bt709": 1, "smpte170m": 6, "bt470bg": 5, "smpte240m": 7, "bt2020nc": 9, "bt2020c": 10}
PR_NUM = {"bt709": 1, "smpte170m": 6, "bt470bg": 5, "bt470m": 4, "smpte240m": 7, "bt2020": 9}
TR_NUM = {"bt709": 1, "smpte170m": 6, "bt470bg": 5, "bt470m": 4, "iec61966-2-1": 13, "bt2020-10": 14, "bt2020-12": 15}


def probe_color(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=color_space,color_transfer,color_primaries,color_range", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try: st = json.loads(r.stdout)["streams"][0]
    except Exception: st = {}
    g = lambda k: st.get(k) if st.get(k) not in (None, "unknown", "") else None
    return {"space": g("color_space"), "trc": g("color_transfer"), "prim": g("color_primaries"), "range": g("color_range")}
MAXW = int(CFG.get("max_stream_width", 3840))


def lib_path(e):
    return Path(e["root"]) / e["rel"]

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".webm", ".mov", ".ts", ".wmv"}
SUB_EXT = {".srt", ".vtt"}
TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
LANGS = {
    "en": "English", "te": "Telugu", "hi": "Hindi", "ta": "Tamil", "ml": "Malayalam", "kn": "Kannada",
    "bn": "Bengali", "mr": "Marathi", "pa": "Punjabi", "ur": "Urdu", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "fr": "French", "de": "German", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "ru": "Russian", "ar": "Arabic", "tr": "Turkish", "id": "Indonesian", "th": "Thai", "nl": "Dutch",
    "sv": "Swedish", "pl": "Polish", "he": "Hebrew", "el": "Greek", "vi": "Vietnamese", "ms": "Malay",
}
ISO3 = {"eng": "en", "tel": "te", "hin": "hi", "tam": "ta", "mal": "ml", "kan": "kn", "ben": "bn", "mar": "mr",
        "pan": "pa", "urd": "ur", "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh", "fre": "fr", "fra": "fr",
        "ger": "de", "deu": "de", "spa": "es", "ita": "it", "por": "pt", "rus": "ru", "ara": "ar", "tur": "tr",
        "ind": "id", "tha": "th", "dut": "nl", "nld": "nl", "swe": "sv", "pol": "pl", "heb": "he", "gre": "el",
        "ell": "el", "vie": "vi", "may": "ms", "msa": "ms"}
NAME2CODE = {v.lower(): k for k, v in LANGS.items()}


def norm_lang(s):
    s = (s or "").lower().strip()
    if s in LANGS: return s
    if s in ISO3: return ISO3[s]
    if s in NAME2CODE: return NAME2CODE[s]
    return s[:2] if len(s) >= 2 and s[:2] in LANGS else ""


def lang_name(code): return LANGS.get(code, code.upper() if code else "Unknown")


# ---------------------------------------------------------------- database
LOCK = threading.RLock()
DB = {}
STATUS = {"state": "idle", "done": 0, "total": 0, "msg": ""}
if DB_PATH.exists():
    try: DB = json.loads(DB_PATH.read_text())
    except Exception: DB = {}


def save_db():
    with LOCK:
        tmp = DB_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(DB))
        tmp.replace(DB_PATH)


# ---------------------------------------------------------------- scanning
QUALITY = re.compile(r"\b(2160p|1080p|720p|480p|4k|hdrip|web[- ]?dl|webrip|web|bluray|blu-ray|brrip|bdrip|dvdrip|dvd9|hdtv|"
                     r"x264|x265|h264|h265|hevc|10bit|aac|ddp?\d|dd|esubs?|msub|telugu|hindi|tamil|malayalam|kannada|"
                     r"english|dual audio|multi|yify|yts|proper|remastered|extended|imax)\b", re.I)


def parse_name(name):
    name = os.path.splitext(name)[0] if os.path.splitext(name)[1].lower() in VIDEO_EXT else name
    s = re.sub(r"[._]+", " ", name)
    s = re.sub(r"^www\s+\S+(\s+\w+)?\s+-\s+", "", s, flags=re.I)
    s = re.sub(r"^(@\S+|\[[^\]]*\]|www\S+)\s*", "", s).strip()
    year = None
    m = None
    for m in re.finditer(r"(?<!\d)((?:19|20)\d\d)(?!\d)", s):
        if m.start() > 0: break
    else:
        m = None
    if m and m.start() > 0:
        year = int(m.group(1)); title = s[:m.start()]
    else:
        q = QUALITY.search(s)
        title = s[:q.start()] if q and q.start() > 0 else s
    title = re.sub(r"[\(\[\{\-\s]+$", "", re.sub(r"\s+", " ", title)).strip()
    return title or name, year


EP_RE = re.compile(r"\bS(\d{1,2})[\s._-]*(?:E|EP)[\s._-]*(\d{1,3})\b", re.I)


def parse_episode(fname):
    """Return (show_title, season, episode, episode_title) for TV episode filenames, else None."""
    base = os.path.splitext(fname)[0]
    s = re.sub(r"[._]+", " ", base)
    m = EP_RE.search(s)
    if not m: return None
    show = s[:m.start()]
    show = re.sub(r"^\s*(?:[A-Z0-9]{2,}\s+)+(?=[A-Za-z0-9\']+\s+[A-Za-z0-9\']+)", "", show) if re.match(r"^\s*[A-Z0-9]{2,}\s+[A-Z0-9]{2,}\s", show) else show
    show = re.sub(r"\s*[\(\[]?(?:19|20)\d\d[\)\]]?\s*$", "", show)
    show = re.sub(r"[\s\-:]+$", "", re.sub(r"\s+", " ", show)).strip() or "Unknown show"
    rest = s[m.end():]
    q = QUALITY.search(rest)
    rest = rest[:q.start()] if q else rest
    rest = re.sub(r"\(.*", "", rest)
    et = re.sub(r"^[\s\-:]+|[\s\-:\[]+$", "", rest)
    return show, int(m.group(1)), int(m.group(2)), et


def show_key(title): return re.sub(r"[^a-z0-9]", "", title.lower())


def show_id(title): return hashlib.sha1(show_key(title).encode()).hexdigest()[:10]


def ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, timeout=120)
    return json.loads(r.stdout or "{}")


def probe_entry(path):
    j = ffprobe(path)
    out = {"duration": float(j.get("format", {}).get("duration") or 0), "video": None, "audio": [], "subs": []}
    ai = si = 0
    for st in j.get("streams", []):
        tags = {k.lower(): v for k, v in st.get("tags", {}).items()}
        t = st.get("codec_type")
        if t == "video" and not out["video"] and st.get("disposition", {}).get("attached_pic", 0) == 0 \
                and st.get("codec_name") not in ("png", "mjpeg"):
            out["video"] = {"codec": st.get("codec_name"), "pix_fmt": st.get("pix_fmt"), "w": st.get("width"),
                            "h": st.get("height"), "profile": st.get("profile")}
        elif t == "audio":
            out["audio"].append({"idx": ai, "lang": norm_lang(tags.get("language")), "codec": st.get("codec_name"),
                                 "ch": st.get("channels"), "title": tags.get("title", "")})
            ai += 1
        elif t == "subtitle":
            out["subs"].append({"idx": si, "lang": norm_lang(tags.get("language")), "codec": st.get("codec_name"),
                                "title": tags.get("title", ""), "forced": bool(st.get("disposition", {}).get("forced")),
                                "text": st.get("codec_name") in TEXT_SUB_CODECS})
            si += 1
    return out


def guess_ext_lang(fname):
    for tok in re.split(r"[^a-zA-Z]+", os.path.splitext(fname)[0].lower())[::-1]:
        c = norm_lang(tok)
        if c: return c
    return ""


def find_movies(root):
    root = Path(root)
    movies = []  # (path, single_in_dir)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ("sample", "samples", "subs", "extras")]
        vids = []
        for f in files:
            p = Path(dirpath) / f
            if p.suffix.lower() in VIDEO_EXT and "sample" not in f.lower() and "trailer" not in f.lower():
                try: vids.append((p, p.stat().st_size))
                except OSError: pass
        if not vids: continue
        big = max(s for _, s in vids)
        keep = [p for p, s in vids if s >= big * 0.4 and s > 20_000_000]
        for p in keep:
            movies.append((p, len(keep) == 1 and Path(dirpath) != root))
    return movies


def external_subs(path, single):
    folder = path.parent
    found = []
    if single:
        for dp, _, fs in os.walk(folder):
            for f in fs:
                if Path(f).suffix.lower() in SUB_EXT: found.append(Path(dp) / f)
    else:
        found = [folder / f for f in os.listdir(folder) if Path(f).suffix.lower() in SUB_EXT
                 and f.startswith(path.stem[:20])]
    return found


def scan(force_probe=False):
    items = []
    for lib in CFG["libraries"]:
        root = Path(os.path.expanduser(lib["path"]))
        if not root.exists():
            STATUS.update(state="error", msg=f"Library folder not found: {root}")
            continue
        STATUS.update(state="scanning", done=0, total=0, msg=f"Scanning {root.name}…")
        items += [(p, single, root, lib.get("kind", "auto")) for p, single in find_movies(root)]
    if not items:
        if STATUS.get("state") != "error":
            STATUS.update(state="idle", msg="No videos found in your library folders (files under 20 MB are ignored)")
        return
    STATUS.update(total=len(items), msg="Reading media info…")
    seen = set()

    def work(item):
        path, single, root, kind = item
        rel = str(path.relative_to(root))
        mid = hashlib.sha1(rel.encode()).hexdigest()[:10]
        seen.add(mid)
        st = path.stat()
        with LOCK: e = DB.get(mid)
        if not e or e.get("size") != st.st_size or force_probe or "audio" not in e:
            try: info = probe_entry(path)
            except Exception as ex:
                info = {"duration": 0, "video": None, "audio": [], "subs": []}
            e = {**(e or {}), "id": mid, "rel": rel, "size": st.st_size, "added": int(st.st_mtime), **info}
        e["root"] = str(root)
        epi = parse_episode(path.name) if kind in ("tv", "auto") else None
        if epi:
            e.update(kind="episode", show=epi[0], show_id=show_id(epi[0]), season=epi[1], ep=epi[2],
                     ep_title=epi[3], title=epi[0], year=None)
            e.pop("meta", None)
        else:
            e["kind"] = "movie"
            if not e.get("meta"):
                e["title"], e["year"] = parse_name(path.parent.name if single else path.name)
        e["ext_subs"] = [{"rel": str(p.relative_to(root)), "lang": guess_ext_lang(p.name)}
                         for p in external_subs(path, single)]
        with LOCK: DB[mid] = e
        STATUS["done"] += 1

    with ThreadPoolExecutor(4) as ex: list(ex.map(work, items))
    with LOCK:
        for k in [k for k in DB if k not in seen]: del DB[k]
    save_db()
    fetch_all_metadata()
    STATUS.update(state="idle", msg="")


# ---------------------------------------------------------------- TMDB
def http_json(url, headers=None, data=None, method=None, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"User-Agent": "MediaCenter/1.0", "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if data is not None else {}),
                                          **(headers or {})}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tmdb(path, **params):
    key = CFG["tmdb_key"].strip()
    if not key: raise RuntimeError("Set tmdb_key in config.json")
    headers = {}
    if len(key) > 40: headers["Authorization"] = "Bearer " + key
    else: params["api_key"] = key
    return http_json("https://api.themoviedb.org/3" + path + "?" + urllib.parse.urlencode(params), headers)


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "MediaCenter/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r: Path(dest).write_bytes(r.read())


def apply_tmdb(mid, tmdb_id):
    d = tmdb(f"/movie/{tmdb_id}", append_to_response="credits,videos,external_ids,recommendations,similar", language="en-US")
    crew = d.get("credits", {}).get("crew", [])
    trailer = next((v["key"] for v in d.get("videos", {}).get("results", [])
                    if v["site"] == "YouTube" and v["type"] == "Trailer"), None)
    meta = {
        "tmdb_id": d["id"], "imdb_id": d.get("external_ids", {}).get("imdb_id"), "title": d.get("title"),
        "year": int((d.get("release_date") or "0")[:4] or 0) or None, "overview": d.get("overview"),
        "tagline": d.get("tagline"), "genres": [g["name"] for g in d.get("genres", [])],
        "rating": round(d.get("vote_average") or 0, 1), "votes": d.get("vote_count"), "runtime": d.get("runtime"),
        "orig_lang": d.get("original_language"),
        "spoken": [s.get("english_name") for s in d.get("spoken_languages", [])],
        "director": ", ".join(c["name"] for c in crew if c["job"] == "Director"),
        "cast": [{"name": c["name"], "role": c.get("character"), "pid": c["id"], "photo": False}
                 for c in d.get("credits", {}).get("cast", [])[:14]],
        "crew": [{"name": c["name"], "job": c["job"]} for c in crew
                 if c["job"] in ("Director", "Writer", "Screenplay", "Story", "Producer", "Original Music Composer",
                                 "Director of Photography", "Editor")][:12],
        "collection": (d.get("belongs_to_collection") or {}).get("name"),
        "rec_ids": [r["id"] for r in d.get("recommendations", {}).get("results", [])[:20]] +
                   [r["id"] for r in d.get("similar", {}).get("results", [])[:10]],
        "source": "tmdb",
        "trailer": trailer, "poster": False, "backdrop": False,
    }
    for kind, field, size in (("p", "poster_path", "w780"), ("b", "backdrop_path", "w1280")):
        if d.get(field):
            if grab(f"https://image.tmdb.org/t/p/{size}{d[field]}", DATA / "img" / f"{mid}_{kind}.jpg"):
                meta["poster" if kind == "p" else "backdrop"] = True
    for c, src in zip(meta["cast"], d.get("credits", {}).get("cast", [])[:14]):
        if src.get("profile_path"):
            c["photo"] = grab("https://image.tmdb.org/t/p/w185" + src["profile_path"], DATA / "img" / f"person_{c['pid']}.jpg")
    with LOCK: DB[mid]["meta"] = meta
    return meta


def has_tmdb(): return bool(CFG["tmdb_key"].strip())


def shrink(dest, width):
    """Downscale oversized artwork (never upscales) so cards stay crisp but load fast."""
    tmp = Path(str(dest) + ".tmp.jpg")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(dest), "-vf", f"scale='min({width},iw)':-2:flags=lanczos",
                        "-q:v", "2", str(tmp)], capture_output=True)
    if r.returncode == 0 and tmp.exists(): tmp.replace(dest)
    else: tmp.unlink(missing_ok=True)


def grab(url, dest):
    try:
        download(url, dest)
        shrink(dest, 900 if str(dest).endswith("_p.jpg") else 1600)
        return True
    except Exception: return False


def strip_html(t): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t or "")).strip()


def keyless_movie(mid):
    """No-API-key fallback: IMDb suggestion endpoint (poster, year, cast) + Wikipedia (overview)."""
    e = DB[mid]; title = e["title"]; year = e.get("year")
    q = urllib.parse.quote(title.lower())
    r = http_json(f"https://v2.sg.media-imdb.com/suggestion/{title[:1].lower() if title[:1].isalpha() else 'x'}/{q}.json")
    cands = [x for x in r.get("d", []) if x.get("qid") in ("movie", "tvMovie") and x.get("id", "").startswith("tt")]
    if year:
        close = [x for x in cands if x.get("y") and abs(x["y"] - year) <= 1]
        cands = close or cands
    if not cands: return None
    return keyless_apply(mid, cands[0])


def keyless_apply(mid, c):
    year = DB[mid].get("year")
    meta = {"tmdb_id": None, "imdb_id": c["id"], "title": c.get("l"), "year": c.get("y") or year, "overview": "",
            "genres": [], "rating": None, "cast": [{"name": n.strip(), "role": ""} for n in (c.get("s") or "").split(",")][:10],
            "director": "", "trailer": None, "poster": False, "backdrop": False, "source": "imdb+wikipedia"}
    if c.get("i", {}).get("imageUrl"):
        meta["poster"] = grab(c["i"]["imageUrl"], DATA / "img" / f"{mid}_p.jpg")
    for page in (f"{c.get('l')} ({c.get('y')} film)", f"{c.get('l')} (film)", c.get("l")):
        try:
            w = http_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(page.replace(" ", "_")))
            if w.get("type") == "standard" and ("film" in (w.get("description") or "").lower() or page != c.get("l")):
                meta["overview"] = w.get("extract", ""); break
        except Exception: continue
    with LOCK: DB[mid]["meta"] = meta
    return meta


def identify_imdb(mid, text):
    """Identify a movie from an IMDb id (tt1234567) or imdb.com link; TMDB if a key is set, else IMDb+Wikipedia."""
    m = re.search(r"tt\d{6,10}", text or "")
    if not m: raise RuntimeError("That doesn't look like an IMDb id (expected something like tt0133093)")
    iid = m.group(0)
    if has_tmdb():
        r = tmdb(f"/find/{iid}", external_source="imdb_id").get("movie_results", [])
        if not r: raise RuntimeError(f"TMDB has no movie for {iid}")
        apply_tmdb(mid, r[0]["id"]); return DB[mid]["meta"]
    r = http_json(f"https://v2.sg.media-imdb.com/suggestion/t/{iid}.json")
    c = next((x for x in r.get("d", []) if x.get("id") == iid), None)
    if not c: raise RuntimeError(f"IMDb returned nothing for {iid}")
    meta = keyless_apply(mid, c)
    with LOCK: meta["identified"] = True   # user-chosen: never replaced by automatic TMDB upgrade
    return meta


BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "image/*,text/html;q=0.8,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(30_000_000), (r.headers.get("Content-Type") or "").lower()


def fetch_image(url, dest, width):
    """Download an image from a direct link OR a web page (uses the page's og:image). Validates before replacing."""
    data, ctype = _fetch_bytes(url)
    if "html" in ctype or data[:300].lstrip().lower().startswith((b"<!doctype", b"<html")):
        html = data.decode("utf-8", "ignore")
        found = None
        for pat in (r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image)["\'][^>]*content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)["\']',
                    r'<link[^>]+rel=["\']image_src["\'][^>]*href=["\']([^"\']+)'):
            m = re.search(pat, html, re.I)
            if m: found = m.group(1); break
        if not found: raise RuntimeError("That link is a web page with no picture I can use. Open the image itself, right-click → Copy image address, and paste that.")
        found = urllib.parse.urljoin(url, found.replace("&amp;", "&"))
        data, ctype = _fetch_bytes(found)
        if "html" in ctype: raise RuntimeError("Could not find an image at that link")
    tmp = Path(str(dest) + ".dl")
    tmp.write_bytes(data)
    out = Path(str(dest) + ".new.jpg")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-frames:v", "1",
                        "-vf", f"scale='min({width},iw)':-2:flags=lanczos", "-q:v", "2", str(out)], capture_output=True)
    tmp.unlink(missing_ok=True)
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        out.unlink(missing_ok=True)
        raise RuntimeError("That link doesn't contain a usable image")
    out.replace(dest)      # only replaced after the new image is confirmed good


def edit_meta(mid, f):
    """Manual edits; edited entries are never overwritten by automatic matching."""
    with LOCK:
        e = DB[mid]
        meta = e.get("meta") or {"tmdb_id": None, "imdb_id": None, "genres": [], "cast": [], "crew": [], "poster": False,
                                 "backdrop": False, "trailer": None, "rating": None, "source": "manual"}
        def txt(k): return str(f.get(k, "")).strip() if k in f else None
        for k in ("title", "overview", "tagline", "director"):
            if txt(k) is not None: meta[k] = txt(k)
        if txt("year") is not None: meta["year"] = int(txt("year")) if txt("year").isdigit() else None
        if txt("rating") is not None:
            try: meta["rating"] = round(float(txt("rating")), 1) if txt("rating") else None
            except ValueError: raise RuntimeError("Rating must be a number")
        if txt("runtime") is not None: meta["runtime"] = int(txt("runtime")) if txt("runtime").isdigit() else None
        if txt("imdb_id") is not None:
            mm = re.search(r"tt\d{6,10}", txt("imdb_id")); meta["imdb_id"] = mm.group(0) if mm else None
        if "genres" in f: meta["genres"] = [g.strip() for g in str(f["genres"]).split(",") if g.strip()]
        if "cast" in f:
            old = {c["name"]: c for c in meta.get("cast", [])}
            meta["cast"] = [old.get(n.strip(), {"name": n.strip(), "role": "", "photo": False})
                            for n in str(f["cast"]).split(",") if n.strip()]
        if txt("director") is not None:
            meta["crew"] = ([{"name": n.strip(), "job": "Director"} for n in meta["director"].split(",") if n.strip()] +
                            [c for c in meta.get("crew", []) if c.get("job") != "Director"])
        if not meta.get("title"): meta["title"] = e["title"]
        meta["edited"] = True
        e["meta"] = meta
    for url_key, kind, flag in (("poster_url", "p", "poster"), ("backdrop_url", "b", "backdrop")):
        u = (f.get(url_key) or "").strip()
        if u and u == (DB[mid]["meta"].get(flag + "_src") or "") and (DATA / "img" / f"{mid}_{kind}.jpg").exists():
            continue      # unchanged link: keep the image we already have
        if u:
            if not u.startswith(("http://", "https://")): raise RuntimeError("Image links must start with http(s)://")
            try: fetch_image(u, DATA / "img" / f"{mid}_{kind}.jpg", 900 if kind == "p" else 1920)
            except RuntimeError: raise
            except Exception as ex: raise RuntimeError(f"Could not download that image ({ex})")
            DB[mid]["meta"][flag] = True; DB[mid]["meta"][flag + "_src"] = u
    save_db()
    return DB[mid]["meta"]


def auto_match(mid):
    e = DB[mid]
    if has_tmdb():
        try:
            for yr in ([e.get("year"), None] if e.get("year") else [None]):
                params = {"query": e["title"]}
                if yr: params["year"] = yr
                res = tmdb("/search/movie", **params).get("results", [])
                if res: return apply_tmdb(mid, res[0]["id"])
        except Exception: pass
    try:
        if keyless_movie(mid): return DB[mid]["meta"]
    except Exception: pass
    e["meta_tried"] = True
    return None


# ---- TV shows
SHOWS_PATH = DATA / "shows.json"
SHOWS = json.loads(SHOWS_PATH.read_text()) if SHOWS_PATH.exists() else {}


def show_title_pretty(t): return t.title() if t == t.lower() else t


def fetch_show_meta(sid, title):
    meta = {"title": title, "overview": "", "genres": [], "rating": None, "year": None, "poster": False,
            "backdrop": False, "eps": {}, "tried": True}
    got = False
    if has_tmdb():
        try:
            r = tmdb("/search/tv", query=title).get("results", [])
            if r:
                d = tmdb(f"/tv/{r[0]['id']}", language="en-US")
                meta.update(title=d.get("name") or title, overview=d.get("overview", ""),
                            genres=[g["name"] for g in d.get("genres", [])], rating=round(d.get("vote_average") or 0, 1),
                            year=int((d.get("first_air_date") or "0")[:4] or 0) or None, source="tmdb")
                if d.get("poster_path"):
                    meta["poster"] = grab("https://image.tmdb.org/t/p/w780" + d["poster_path"], DATA / "img" / f"{sid}_p.jpg")
                if d.get("backdrop_path"):
                    meta["backdrop"] = grab("https://image.tmdb.org/t/p/w1280" + d["backdrop_path"], DATA / "img" / f"{sid}_b.jpg")
                for sn in d.get("seasons", []):
                    try:
                        for ep in tmdb(f"/tv/{d['id']}/season/{sn['season_number']}").get("episodes", []):
                            meta["eps"][f"{ep['season_number']},{ep['episode_number']}"] = {
                                "name": ep.get("name"), "overview": ep.get("overview", "")}
                    except Exception: pass
                got = True
        except Exception: pass
    if not got:   # TVmaze: free, no key
        try:
            d = http_json("https://api.tvmaze.com/singlesearch/shows?" + urllib.parse.urlencode({"q": title, "embed": "episodes"}))
            meta.update(title=d.get("name") or title, overview=strip_html(d.get("summary")), genres=d.get("genres", []),
                        rating=(d.get("rating") or {}).get("average"), year=int((d.get("premiered") or "0")[:4] or 0) or None,
                        source="tvmaze")
            if d.get("image", {}).get("original"):
                meta["poster"] = grab(d["image"]["original"], DATA / "img" / f"{sid}_p.jpg")
            for ep in d.get("_embedded", {}).get("episodes", []):
                meta["eps"][f"{ep['season']},{ep['number']}"] = {"name": ep.get("name"), "overview": strip_html(ep.get("summary"))}
        except Exception: pass
    SHOWS[sid] = meta


HDR_TRC = {"smpte2084", "arib-std-b67"}
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def make_banner(path, duration, dest):
    """Grab a real still from the movie (skipping black bars, tone-mapping HDR) as a Netflix-style banner."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    hdr = p.stdout.strip().strip(",") in HDR_TRC
    best = None
    for i, frac in enumerate((0.18, 0.28, 0.38, 0.48, 0.58)):
        t = duration * (frac if duration > 300 else 0.4)
        chain = [TONEMAP] if hdr else []
        r = subprocess.run(["ffmpeg", "-v", "info", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "6",
                            "-vf", ",".join(chain + ["cropdetect=24:2:0"]), "-f", "null", "-"],
                           capture_output=True, text=True, timeout=120)
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
        vf = list(chain)
        if crops:
            w, h, x, y = map(int, crops[-1])
            m = re.search(r"Stream #0:\d+.*?Video:.*?, (\d+)x(\d+)", r.stderr)
            if m and h >= int(m.group(2)) * 0.45 and w >= int(m.group(1)) * 0.9:
                vf.append(f"crop={w}:{h}:{x}:{y}")
        vf.append("scale='min(1920,iw)':-2:flags=lanczos")
        tmp = Path(f"{dest}.c{i}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                        "-vf", ",".join(vf), "-q:v", "3", str(tmp)], capture_output=True, timeout=120)
        if tmp.exists() and (best is None or tmp.stat().st_size > best.stat().st_size):
            if best: best.unlink(missing_ok=True)
            best = tmp
        else:
            tmp.unlink(missing_ok=True)
    if best:
        best.replace(dest); return True
    return False


def generate_banners():
    jobs = []
    firsts = {}
    for k, e in DB.items():
        if e.get("kind") == "movie" and not (e.get("meta") or {}).get("backdrop") and not e.get("banner"):
            jobs.append(("m", k, lib_path(e), e.get("duration") or 0, DATA / "img" / f"{k}_b.jpg"))
        elif e.get("kind") == "episode":
            cur = firsts.get(e["show_id"])
            if not cur or (e["season"], e["ep"]) < (cur["season"], cur["ep"]): firsts[e["show_id"]] = e
    for sid, e in firsts.items():
        sm = SHOWS.get(sid)
        if sm and not sm.get("backdrop") and not sm.get("banner"):
            jobs.append(("s", sid, lib_path(e), e.get("duration") or 0, DATA / "img" / f"{sid}_b.jpg"))
    if not jobs: return
    STATUS.update(state="metadata", done=0, total=len(jobs), msg="Creating banners from your movies…")

    def run(j):
        kind, key, path, dur, dest = j
        try:
            if dur > 0 and make_banner(path, dur, dest):
                if kind == "m": DB[key]["banner"] = True
                else: SHOWS[key]["banner"] = True
        except Exception: pass
        STATUS["done"] += 1
        if STATUS["done"] % 8 == 0:
            save_db(); SHOWS_PATH.write_text(json.dumps(SHOWS))
    with ThreadPoolExecutor(2) as ex: list(ex.map(run, jobs))
    save_db(); SHOWS_PATH.write_text(json.dumps(SHOWS))


def fetch_all_metadata():
    todo = [k for k, e in DB.items() if e.get("kind") == "movie" and not e.get("meta_tried") and
            (not e.get("meta") or (has_tmdb() and e["meta"].get("source") != "tmdb" and not e["meta"].get("edited")
                                   and not e["meta"].get("identified")
                                   and not e.get("tmdb_upgrade_tried")))]
    for k in todo:
        if DB[k].get("meta"): DB[k]["tmdb_upgrade_tried"] = True
    stodo = {}
    for e in DB.values():
        if e.get("kind") == "episode" and (e["show_id"] not in SHOWS or not SHOWS[e["show_id"]].get("tried")):
            stodo.setdefault(e["show_id"], show_title_pretty(e["show"]))
    STATUS.update(state="metadata", done=0, total=len(todo) + len(stodo), msg="Fetching posters & details…")
    for sid, t in stodo.items():
        fetch_show_meta(sid, t); STATUS["done"] += 1
        SHOWS_PATH.write_text(json.dumps(SHOWS))
    for k in todo:
        try: auto_match(k)
        except Exception as ex: STATUS["msg"] = f"Metadata error: {ex}"
        STATUS["done"] += 1
        if STATUS["done"] % 10 == 0: save_db()
    save_db()
    generate_banners()
    STATUS.update(state="idle", msg="")


# ---------------------------------------------------------------- subtitles
def vtt_from_srt(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    text = re.sub(r"(\d\d:\d\d:\d\d),(\d{3})", r"\1.\2", text)
    return "WEBVTT\n\n" + text


def read_text(path):
    b = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try: return b.decode(enc)
        except Exception: continue
    return b.decode("utf-8", "ignore")


def _ts(s):
    p = s.split(":")
    if len(p) == 2: p.insert(0, "0")
    return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])


def _fmt(t):
    t = max(t, 0)
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}"


def shift_vtt(vtt, offset):
    if not offset: return vtt
    out = []
    for blk in re.split(r"\n\n+", vtt):
        m = re.search(r"((?:\d+:)?\d\d:\d\d\.\d{3}) --> ((?:\d+:)?\d\d:\d\d\.\d{3})", blk)
        if not m:
            out.append(blk); continue
        a, b = _ts(m.group(1)) - offset, _ts(m.group(2)) - offset
        if b <= 0: continue
        out.append(blk[:m.start()] + f"{_fmt(a)} --> {_fmt(b)}" + blk[m.end():])
    return "\n\n".join(out)


def subtitle_list(e):
    subs = []
    for s in e.get("subs", []):
        if not s["text"]: continue
        nm = lang_name(s["lang"]) + (f" – {s['title']}" if s["title"] else "") + (" (forced)" if s["forced"] else "")
        subs.append({"key": f"e{s['idx']}", "lang": s["lang"], "label": nm, "src": "embedded"})
    for i, s in enumerate(e.get("ext_subs", [])):
        subs.append({"key": f"x{i}", "lang": s["lang"], "label": lang_name(s["lang"]) + " (file)", "src": "file"})
    for p in sorted((DATA / "subs").glob(f"{e['id']}.*.srt")):
        lg = p.name.split(".")[-2]
        subs.append({"key": f"d{lg}", "lang": lg, "label": lang_name(lg) + " (downloaded)", "src": "downloaded"})
    return subs


def get_vtt(mid, key):
    e = DB[mid]; root = Path(e["root"])
    cache = DATA / "subcache" / f"{mid}.{key}.vtt"
    if cache.exists(): return cache.read_text()
    if key[0] == "e":
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(root / e["rel"]), "-map", f"0:s:{int(key[1:])}",
                            "-f", "webvtt", "-"], capture_output=True, text=True, timeout=600)
        vtt = r.stdout
    elif key[0] == "x":
        vtt = vtt_from_srt(read_text(root / e["ext_subs"][int(key[1:])]["rel"]))
    else:
        vtt = vtt_from_srt(read_text(DATA / "subs" / f"{mid}.{key[1:]}.srt"))
    cache.write_text(vtt)
    return vtt


OS_TOKEN = {"tok": None, "t": 0}


def os_headers(auth=False):
    h = {"Api-Key": CFG["opensubtitles_key"].strip()}
    if auth: h["Authorization"] = "Bearer " + OS_TOKEN["tok"]
    return h


def os_login():
    if OS_TOKEN["tok"] and time.time() - OS_TOKEN["t"] < 3600: return
    if not (CFG["opensubtitles_user"] and CFG["opensubtitles_pass"]):
        raise RuntimeError("Set opensubtitles_user / opensubtitles_pass in config.json")
    r = http_json("https://api.opensubtitles.com/api/v1/login", os_headers(),
                  {"username": CFG["opensubtitles_user"], "password": CFG["opensubtitles_pass"]})
    OS_TOKEN.update(tok=r["token"], t=time.time())


def fetch_subtitle(mid, lang):
    if not CFG["opensubtitles_key"].strip(): raise RuntimeError("Set opensubtitles_key in config.json")
    e = DB[mid]; meta = e.get("meta") or {}
    os_login()
    q = {"languages": lang, "order_by": "download_count", "order_direction": "desc", "type": "movie"}
    if e.get("kind") == "episode":
        q.update(type="episode", query=e["show"], season_number=e["season"], episode_number=e["ep"])
    elif meta.get("tmdb_id"): q["tmdb_id"] = meta["tmdb_id"]
    elif meta.get("imdb_id"): q["imdb_id"] = meta["imdb_id"].lstrip("t")
    else:
        q["query"] = e["title"]
        if e.get("year"): q["year"] = e["year"]
    res = http_json("https://api.opensubtitles.com/api/v1/subtitles?" + urllib.parse.urlencode(sorted(q.items())),
                    os_headers()).get("data", [])
    res = [r for r in res if r["attributes"].get("files")]
    if not res: raise RuntimeError(f"No {lang_name(lang)} subtitles found")
    for r in res[:3]:
        fid = r["attributes"]["files"][0]["file_id"]
        try:
            d = http_json("https://api.opensubtitles.com/api/v1/download", os_headers(True), {"file_id": fid})
            dest = DATA / "subs" / f"{mid}.{lang}.srt"
            download(d["link"], dest)
            for c in (DATA / "subcache").glob(f"{mid}.d{lang}.vtt"): c.unlink()
            return {"key": f"d{lang}", "remaining": d.get("remaining")}
        except urllib.error.HTTPError as ex:
            if ex.code in (406, 429): raise RuntimeError("OpenSubtitles daily download quota reached")
            if ex.code == 401: OS_TOKEN["tok"] = None
            last = ex
    raise RuntimeError("Download failed")


# ---------------------------------------------------------------- HTTP
def ep_code(e): return f"S{e['season']:02d}E{e['ep']:02d}"


def ep_name(e):
    sm = SHOWS.get(e["show_id"], {}).get("eps", {}).get(f"{e['season']},{e['ep']}", {})
    return sm.get("name") or e.get("ep_title") or ""


def show_summaries():
    groups = {}
    for e in DB.values():
        if e.get("kind") == "episode": groups.setdefault(e["show_id"], []).append(e)
    out = []
    for sid, eps in groups.items():
        m = SHOWS.get(sid, {})
        out.append({"id": sid, "title": m.get("title") or show_title_pretty(eps[0]["show"]), "year": m.get("year"),
                    "poster": bool(m.get("poster")), "backdrop": bool(m.get("backdrop") or m.get("banner")), "rating": m.get("rating"),
                    "genres": m.get("genres", []), "seasons": sorted({e["season"] for e in eps}), "count": len(eps),
                    "added": max(e["added"] for e in eps), "overview": (m.get("overview") or "")[:240],
                    "langs": sorted({a["lang"] for e in eps for a in e.get("audio", []) if a["lang"]})})
    return out


def show_detail(sid):
    eps = sorted((e for e in DB.values() if e.get("kind") == "episode" and e["show_id"] == sid),
                 key=lambda e: (e["season"], e["ep"]))
    if not eps: raise KeyError(sid)
    base = next(x for x in show_summaries() if x["id"] == sid)
    base["full_overview"] = SHOWS.get(sid, {}).get("overview", "")
    base["episodes"] = [{"id": e["id"], "season": e["season"], "ep": e["ep"], "title": ep_name(e),
                         "overview": SHOWS.get(sid, {}).get("eps", {}).get(f"{e['season']},{e['ep']}", {}).get("overview", ""),
                         "duration": e.get("duration"), "langs": sorted({a["lang"] for a in e.get("audio", []) if a["lang"]})}
                        for e in eps]
    return base


def similar_movies(mid, limit=14):
    e = DB[mid]; m = e.get("meta") or {}
    words = lambda t: {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2 and w not in ("the", "and", "part", "film")}
    my_words = words(m.get("title") or e["title"])
    my_cast = {c["name"] for c in m.get("cast", [])}
    my_langs = {a["lang"] for a in e.get("audio", []) if a["lang"]}
    my_dir = m.get("director")
    scored = []
    for oid, o in DB.items():
        if oid == mid or o.get("kind") != "movie": continue
        om = o.get("meta") or {}
        sc = 0.0
        if om.get("tmdb_id") and om["tmdb_id"] in m.get("rec_ids", []): sc += 5
        if m.get("collection") and m.get("collection") == om.get("collection"): sc += 8
        sc += 2 * len(set(m.get("genres", [])) & set(om.get("genres", [])))
        sc += 3 * len(my_cast & {c["name"] for c in om.get("cast", [])})
        if my_dir and my_dir == om.get("director"): sc += 3
        sc += 4 * min(2, len(my_words & words(om.get("title") or o["title"])))
        if my_langs & {a["lang"] for a in o.get("audio", []) if a["lang"]}: sc += 1
        y1, y2 = m.get("year") or e.get("year"), om.get("year") or o.get("year")
        if y1 and y2 and abs(y1 - y2) <= 3: sc += 1
        if om.get("poster"): sc += .2
        if sc >= 2: scored.append((sc, oid))
    scored.sort(reverse=True)
    me = ((m.get("title") or e["title"]).lower(), m.get("year") or e.get("year"))
    seen, out = {me}, []
    for _, i in scored:
        p = public(DB[i]); key = (p["title"].lower(), p["year"])
        if key in seen: continue
        seen.add(key); out.append(p)
        if len(out) >= limit: break
    return out


def public(e, detail=False):
    m = e.get("meta") or {}
    if e.get("kind") == "episode":
        sm = SHOWS.get(e["show_id"], {})
        title = f"{sm.get('title') or show_title_pretty(e['show'])} · {ep_code(e)}" + (f" · {ep_name(e)}" if ep_name(e) else "")
        out = {"id": e["id"], "kind": "episode", "show_id": e["show_id"], "title": title, "year": None,
               "poster": bool(sm.get("poster")), "backdrop": False, "rating": None, "genres": sm.get("genres", []),
               "added": e.get("added"), "duration": e.get("duration"), "langs": [], "matched": True,
               "filename": e["rel"], "overview": ""}
        if detail:
            out.update(meta={}, size=e["size"], video=e.get("video") or {}, audio=e.get("audio", []),
                       subtitles=subtitle_list(e), audio_labels=[audio_label(a) for a in e.get("audio", [])])
        return out
    out = {"id": e["id"], "kind": "movie", "title": m.get("title") or e["title"], "year": m.get("year") or e.get("year"),
           "poster": bool(m.get("poster")), "backdrop": bool(m.get("backdrop") or e.get("banner")), "rating": m.get("rating"),
           "genres": m.get("genres", []), "added": e.get("added"), "duration": e.get("duration"),
           "langs": sorted({a["lang"] for a in e.get("audio", []) if a["lang"]}),
           "matched": bool(m), "filename": e["rel"], "overview": (m.get("overview") or "")[:240]}
    if detail:
        v = e.get("video") or {}
        out.update(meta=m, size=e["size"], video=v, audio=e.get("audio", []), subtitles=subtitle_list(e),
                   audio_labels=[audio_label(a) for a in e.get("audio", [])], similar=similar_movies(e["id"]))
    return out


def audio_label(a):
    ch = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(a["ch"], f"{a['ch']}ch")
    name = lang_name(a["lang"]) if a["lang"] else f"Track {a['idx'] + 1}"
    return f"{name} · {a['codec'].upper()} {ch}" + (f" · {a['title']}" if a["title"] else "")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def send_bytes(self, body, ctype, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def send_json(self, obj, code=200): self.send_bytes(json.dumps(obj).encode(), "application/json", code)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); p = u.path
        try:
            if p == "/":
                return self.send_bytes((APP / "index.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/api/library":
                with LOCK:
                    items = [public(e) for e in DB.values() if e.get("kind") != "episode"]
                    eps = [{"id": e["id"], "title": public(e)["title"], "show_id": e["show_id"], "duration": e.get("duration")}
                           for e in DB.values() if e.get("kind") == "episode"]
                    shows = show_summaries()
                return self.send_json({"movies": items, "shows": shows, "episodes": eps, "status": STATUS,
                                       "tmdb": has_tmdb(),
                                       "subtitle_languages": CFG["subtitle_languages"], "site_name": CFG["site_name"],
                                       "auto_subtitle_languages": CFG["auto_subtitle_languages"],
                                       "lang_names": LANGS})
            if p == "/api/log":
                print("BROWSER:", q.get("m", [""])[0], flush=True); return self.send_json({})
            if p == "/api/status": return self.send_json(STATUS)
            m = re.fullmatch(r"/api/show/(\w+)", p)
            if m: return self.send_json(show_detail(m.group(1)))
            m = re.fullmatch(r"/api/movie/(\w+)", p)
            if m: return self.send_json(public(DB[m.group(1)], True))
            if p == "/api/tmdb/search":
                r = tmdb("/search/movie", query=q["q"][0], **({"year": q["year"][0]} if q.get("year") and q["year"][0] else {}))
                return self.send_json([{"id": x["id"], "title": x["title"], "year": (x.get("release_date") or "")[:4],
                                        "poster": x.get("poster_path"), "overview": (x.get("overview") or "")[:160]}
                                       for x in r.get("results", [])[:12]])
            m = re.fullmatch(r"/img/person_(\d+)\.jpg", p)
            if m:
                f = DATA / "img" / f"person_{m.group(1)}.jpg"
                if f.exists():
                    return self.send_bytes(f.read_bytes(), "image/jpeg", extra={"Cache-Control": "max-age=604800"})
                return self.send_bytes(b"", "image/jpeg", 404)
            m = re.fullmatch(r"/img/(\w+)_([pb])\.jpg", p)
            if m:
                f = DATA / "img" / f"{m.group(1)}_{m.group(2)}.jpg"
                if f.exists():
                    stt = f.stat(); etag = f'"{stt.st_mtime_ns}-{stt.st_size}"'
                    if self.headers.get("If-None-Match") == etag:
                        return self.send_bytes(b"", "image/jpeg", 304, {"ETag": etag, "Cache-Control": "no-cache"})
                    return self.send_bytes(f.read_bytes(), "image/jpeg", extra={"ETag": etag, "Cache-Control": "no-cache"})
                return self.send_bytes(b"", "image/jpeg", 404)
            m = re.fullmatch(r"/sub/(\w+)/(\w+)\.vtt", p)
            if m:
                vtt = shift_vtt(get_vtt(m.group(1), m.group(2)), float(q.get("offset", ["0"])[0]))
                return self.send_bytes(vtt.encode(), "text/vtt; charset=utf-8")
            m = re.fullmatch(r"/file/(\w+)", p)
            if m: return self.serve_file(m.group(1))
            m = re.fullmatch(r"/stream/(\w+)", p)
            if m: return self.stream(m.group(1), q)
            self.send_bytes(b"not found", "text/plain", 404)
        except KeyError: self.send_bytes(b"not found", "text/plain", 404)
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as ex: self.send_json({"error": str(ex)}, 500)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            if p == "/api/scan":
                if STATUS["state"] == "idle":
                    threading.Thread(target=scan, args=(False,), daemon=True).start()
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/movie/(\w+)/vlc", p)
            if m:
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "VLC can only be launched from this computer"}, 403)
                return self.send_json(open_in_vlc(m.group(1), self.body_json()))
            m = re.fullmatch(r"/api/movie/(\w+)/subs", p)
            if m:
                r = fetch_subtitle(m.group(1), self.body_json()["lang"])
                return self.send_json(r)
            m = re.fullmatch(r"/api/movie/(\w+)/identify", p)
            if m:
                identify_imdb(m.group(1), self.body_json().get("imdb", "")); save_db()
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/movie/(\w+)/meta", p)
            if m:
                edit_meta(m.group(1), self.body_json()); return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/movie/(\w+)/meta/reset", p)
            if m:
                with LOCK:
                    DB[m.group(1)].pop("meta", None); DB[m.group(1)].pop("meta_tried", None)
                auto_match(m.group(1)); save_db()
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/movie/(\w+)/match", p)
            if m:
                apply_tmdb(m.group(1), self.body_json()["tmdb_id"]); save_db()
                return self.send_json({"ok": True})
            self.send_bytes(b"not found", "text/plain", 404)
        except KeyError: self.send_json({"error": "not found"}, 404)
        except Exception as ex: self.send_json({"error": str(ex)}, 500)

    def serve_file(self, mid):
        """Direct play: raw file with HTTP Range support (instant start, native seeking, no transcoding)."""
        path = lib_path(DB[mid]); size = path.stat().st_size
        ctype = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm"}.get(
            path.suffix.lower(), "video/x-matroska")
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2): end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
        if start >= size:
            return self.send_bytes(b"", ctype, 416, {"Content-Range": f"bytes */{size}"})
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if m: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start); left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk: break
                    self.wfile.write(chunk); left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError): pass

    def stream(self, mid, q):
        e = DB[mid]
        path = lib_path(e)
        start = float(q.get("start", ["0"])[0]); audio = int(q.get("audio", ["0"])[0])
        v = e.get("video") or {}
        # copy the video untouched (zero quality loss) whenever the browser can decode it; only audio is converted
        copy_ok = q.get("vcopy", ["0"])[0] == "1" or (v.get("codec") == "h264" and v.get("pix_fmt") == "yuv420p")
        tail = ["-map", "0:V:0"] + (["-map", f"0:a:{audio}"] if e.get("audio") else [])
        out = ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-af", "aresample=async=1:first_pts=0", "-sn", "-dn",
               "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
        base = ["ffmpeg", "-v", "error"]
        if copy_ok:
            attempts = [base + ["-ss", str(start), "-i", str(path)] + tail + ["-c:v", "copy"] + (["-tag:v", "hvc1"] if v.get("codec") == "hevc" else []) + out]
        else:
            attempts = []
            col = probe_color(path)
            hdr = col["trc"] in HDR_TRC
            if hdr:       # HDR can't be shown as-is in a browser: map to SDR BT.709 and tag it so
                tags_sw = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
                bsf = "colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0"
            else:         # keep the source's own colour tags exactly (untagged stays untagged)
                tags_sw = []
                if col["space"]: tags_sw += ["-colorspace", col["space"]]
                if col["prim"]: tags_sw += ["-color_primaries", col["prim"]]
                if col["trc"]: tags_sw += ["-color_trc", col["trc"]]
                if col["range"]: tags_sw += ["-color_range", col["range"]]
                parts = []
                if col["prim"] in PR_NUM: parts.append(f"colour_primaries={PR_NUM[col['prim']]}")
                if col["trc"] in TR_NUM: parts.append(f"transfer_characteristics={TR_NUM[col['trc']]}")
                if col["space"] in CS_NUM: parts.append(f"matrix_coefficients={CS_NUM[col['space']]}")
                if col["range"]: parts.append(f"video_full_range_flag={1 if col['range'] == 'pc' else 0}")
                bsf = ":".join(parts)
            ten = "10" in (v.get("pix_fmt") or "")
            wide = (v.get("w") or 0) > MAXW
            if HW_OK and CFG.get("hardware_transcode", True):
                if hdr: vf = ["-vf", "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709"]
                elif ten or wide: vf = ["-vf", f"scale_vaapi=w='min({MAXW},iw)':h=-2:format=nv12"]
                else: vf = []
                attempts.append(base + ["-hwaccel", "vaapi", "-hwaccel_device", "/dev/dri/renderD128",
                                        "-hwaccel_output_format", "vaapi", "-ss", str(start), "-i", str(path)] + tail + vf +
                                ["-c:v", "h264_vaapi", "-qp", "17", "-g", "48"] +
                                (["-bsf:v", f"h264_metadata={bsf}"] if bsf else []) + out)
            chain = ([TONEMAP] if hdr else ["format=yuv420p"]) + ([f"scale='min({MAXW},iw)':-2"] if wide else [])
            attempts.append(base + ["-ss", str(start), "-i", str(path)] + tail +
                            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-g", "48",
                             "-vf", ",".join(chain)] + tags_sw + out)
        proc = first = None
        for cmd in attempts:      # hardware first; fall back to software if it produces nothing
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            first = proc.stdout.read(65536)
            if first: break
            proc.kill(); proc.wait()
        if not first:
            return self.send_bytes(b"stream failed", "text/plain", 500)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4"); self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close"); self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(first)
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk: break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError): pass
        finally:
            proc.kill(); proc.wait()


def open_in_vlc(mid, body):
    e = DB[mid]; path = lib_path(e)
    import shutil
    vlc = [shutil.which("vlc")] if shutil.which("vlc") else ["flatpak", "run", "org.videolan.VLC"]
    args = vlc + ["--fullscreen", "--no-video-title-show"]
    t = int(float(body.get("start", 0) or 0))
    if t > 5: args.append(f"--start-time={t}")
    aud = int(body.get("audio", 0) or 0)
    if len(e.get("audio", [])) > 1: args.append(f"--audio-track={aud}")
    lang = body.get("sub")                      # downloaded subtitle language, e.g. "en"
    if lang:
        f = DATA / "subs" / f"{mid}.{lang}.srt"
        if f.exists(): args.append(f"--sub-file={f}")
    args.append(str(path))
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True}


def optimize_existing():
    for f in (DATA / "img").glob("*.jpg"):
        if f.stat().st_size > 600_000 and f.name.endswith("_p.jpg"): shrink(f, 900)


def main():
    threading.Thread(target=optimize_existing, daemon=True).start()
    if not DB or "--scan" in sys.argv:
        threading.Thread(target=scan, daemon=True).start()
    else:
        threading.Thread(target=fetch_all_metadata, daemon=True).start()
    srv = ThreadingHTTPServer((CFG["host"], int(CFG["port"])), H)
    srv.daemon_threads = True
    print(f"Media Center running at http://{CFG['host']}:{CFG['port']}  (libraries: {', '.join(l['path'] for l in CFG['libraries'])})")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__":
    main()
