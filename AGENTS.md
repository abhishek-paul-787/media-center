# AGENTS.md — Media Center

Instructions for AI coding agents (Claude Code, Codex, Cursor, etc.). Human readers: see `README.md`.

Media Center is a local Netflix-style web UI for a personal movie/TV library.
- `server.py`: Python 3, standard library only. Scans folders, fetches metadata, streams video via ffmpeg, serves the HTTP API.
- `index.html`: the whole UI, vanilla JS, no build step.
- `config.json`: per-user settings and secrets. It is gitignored and created from `config.example.json`.
- `data/`: generated cache (library JSON, posters, subtitles). Gitignored.

There are two jobs. **Job A** sets the app up for a user. **Job B** publishes or changes the repo.
Do only the job you were asked to do.

---

## Job A — Set up Media Center for a user

Work through these steps in order. Run the checks yourself. Ask the user only where marked **ASK**.

### A1. Check requirements

```bash
python3 --version      # need 3.8+
ffmpeg -version
ffprobe -version
```

If something is missing, tell the user what you would install and the command. Install it only with their agreement. Package commands:

| System | Command |
|---|---|
| Debian/Ubuntu | `sudo apt install python3 ffmpeg` |
| Fedora | `sudo dnf install python3 ffmpeg` (`ffmpeg-free` may lack codecs; RPM Fusion has the full build) |
| Immutable Fedora (Bazzite, Silverblue) | `brew install ffmpeg` or `rpm-ostree install ffmpeg` (needs reboot) |
| Arch | `sudo pacman -S python ffmpeg` |
| macOS | `brew install python ffmpeg` |
| Windows | `winget install Python.Python.3.12 Gyan.FFmpeg` (use `python`, not `python3`) |

Optional: `vlc` (for the "Open in VLC" button), `/dev/dri/renderD128` (VAAPI, auto-detected).

### A2. Get the code

If you are not already in the repo:

```bash
git clone <repo-url> media-center && cd media-center
```

### A3. Find the media folders — **ASK**

Ask the user where their movies and TV shows are, and whether they are on an external drive. Do not guess. Do not scan their whole disk. If they don't know, you may list likely locations (`~/Videos`, `~/Movies`, `/run/media/$USER`, `/media`, `/mnt`) and ask which to use.

Check each folder exists and holds video files (`.mkv .mp4 .avi .m4v .webm .mov .ts .wmv`):

```bash
ls "<path>" | head
```

If a folder is on an unmounted drive, say so and stop until it is mounted.

### A4. Write `config.json`

```bash
cp config.example.json config.json
```

Edit **only** `libraries` (plus `port`, `site_name` or languages if the user asked):

```json
"libraries": [
  { "path": "/abs/path/Movies",   "kind": "movie" },
  { "path": "/abs/path/TV Shows", "kind": "tv" }
]
```

Rules:
- `kind` is `movie`, `tv` or `auto` (`auto` = `SxxExx` in the file name means TV). Use `auto` for one mixed folder.
- Use absolute paths, or `~/...`. Use forward slashes on Windows.
- Keep `"host": "127.0.0.1"`. Never set `0.0.0.0` unless the user explicitly asks, and warn them there is no login.
- Validate the JSON afterwards: `python3 -c "import json;json.load(open('config.json'))"`.

### A5. Optional API keys — **ASK, never invent**

Offer these; the app works without them:
- `tmdb_key`: free from themoviedb.org → Settings → API. Gives backdrops, cast photos and similar titles.
- `opensubtitles_key`, `opensubtitles_user`, `opensubtitles_pass`: free account at opensubtitles.com plus an API consumer. Gives automatic subtitles.

Only write values the user gives you. **Never print, log, echo or commit these values.** Never paste them into a command line that is recorded elsewhere.

### A6. Start and verify

If the default port (8765) is busy, pick another and set `port`. Check first: `ss -ltnp | grep :8765` (Linux) or `lsof -i :8765` (macOS).

```bash
python3 server.py > server.log 2>&1 &
echo $! > server.pid
sleep 3
curl -s http://127.0.0.1:8765/api/status
curl -s http://127.0.0.1:8765/api/library | head -c 300
```

`/api/status` `state` goes `scanning` → `metadata` (posters and details, may take minutes) → `idle` (done). The library is already browsable during `metadata`. Poll every few seconds until `idle`. If `state` is `error`, read `msg`. It is usually a missing library folder. Then check the library has titles:

```bash
python3 -c "import json,urllib.request as u;j=json.load(u.urlopen('http://127.0.0.1:8765/api/library'));print(len(j['movies']),'movies',len(j['shows']),'shows')"
```

Zero titles with `idle` means wrong paths or unsupported file types. Re-check A3 and A4.

### A7. Hand over

Tell the user:
- the address to open (`http://127.0.0.1:<port>`);
- whether TMDB and OpenSubtitles are configured;
- how to stop it (`kill $(cat server.pid)`) and how to start it again (`python3 server.py`);
- any problems found (missing codecs, titles with no poster, etc.).

Do not leave stray processes. Stop any test servers you started yourself, unless the user wants it kept running.

### Stopping the server safely

- Kill by PID: `kill $(cat server.pid)`, or find it with `ss -ltnp | grep :<port>`.
- **Never** `pkill -f server.py` or `killall python3`. They can match your own shell or the user's other programs.

### Fixing common failures

| Symptom | Cause / fix |
|---|---|
| `status.state == "error"`, "Library folder not found" | Wrong path or drive not mounted. |
| `Address already in use` | Change `port` or stop the other process (by PID). |
| `ffmpeg: not found` in the log, video fails | Install ffmpeg (A1). |
| Titles but no posters | Metadata still fetching (watch `/api/status`), or no internet. A TMDB key improves results. |
| Wrong title matched | Use the UI editor and paste the IMDb ID, or `POST /api/movie/<id>/identify`. |
| New files missing | `GET /api/scan` or restart with `--scan`. |
| Browser shows old UI | Hard-refresh (Ctrl+Shift+R). |

---

## Job B — Publish or change the repo

### B1. What must never be committed

These are covered by `.gitignore`. Check before every commit:
- `config.json` (API keys, passwords, personal paths)
- `data/` (library cache, posters, subtitles, personal watch data)
- `__pycache__/`, `*.log`, `server.pid`, SSH keys, tokens

Run this before committing. It must print nothing:

```bash
git ls-files | grep -E '^(config\.json|data/|__pycache__/)|\.pem$|id_(rsa|ed25519)'
git diff --cached | grep -iE 'tmdb_key"\s*:\s*"[^"]+|opensubtitles_(key|pass)"\s*:\s*"[^"]+'
```

Keep `config.example.json` free of real values and personal paths.

### B2. Publish to GitHub

Pushing is outward-facing. **Confirm the repo name, owner and public/private visibility with the user first.**

1. Make sure the user has an empty GitHub repo, or can make one. The `gh` CLI may not be logged in; if so, ask them to create the repo in the browser (no README, no licence, no `.gitignore`, since the repo already has them).
2. Check authentication: `ssh -T git@github.com` should say `Hi <user>! You've successfully authenticated`. (Exit code 1 is normal.) If not, help the user add an SSH key. Do not create or overwrite keys without checking `~/.ssh` first.
3. Commit and push:

```bash
git init -b main                       # skip if already a repo
git add -A
git status                             # review the list
git commit -m "Initial commit: Media Center"
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```

4. Verify the remote has no secrets: `git ls-tree -r origin/main --name-only`.
5. Replace `OWNER/media-center` in `README.md` with the real address, then commit and push.

Never force-push or rewrite history on `main` unless the user asks for it.

### B3. Making code changes

- Keep it **stdlib-only** and **build-free**. Do not add pip, npm or any bundler.
- `index.html` has its JavaScript inside template literals. After editing CSS or HTML, check the page loads without JS errors. Do not insert CSS with `rindex('</style>')`, because it can land inside a JS string.
- The video path is in `stream()` in `server.py`. Aim for minimal processing: direct play (HTTP Range) first, then remux, then transcode. Video is copied when it is H.264 yuv420p. Keep `aresample=async=1:first_pts=0` on audio (it fixes an audio-early sync bug) and `-map 0:V:0` (it skips embedded cover art).
- Movie and show IDs are `sha1(path relative to the library root)[:10]`. Renaming files changes IDs. Do not change this casually.
- Image edits use `?v=IMGV` cache-busting plus ETags. Keep that when touching image routes.
- The VLC endpoint must stay restricted to loopback requests.
- Do not try to bypass IMDb's GraphQL 403. Use the suggestion API or TMDB.
- Test UI changes in a real or headless browser. Never leave browsers or test servers running. Kill by PID.
- Test on a throwaway library in a temp directory, with a temp `config.json` and a spare port, not the user's real data.

### B4. API quick reference (all local)

`GET /api/library`, `/api/status`, `/api/scan`, `/api/movie/<id>` (and `/subs`, `/match`, `/identify`, `/meta`, `/meta/reset`, `/vlc`), `/api/show/<id>`, `/api/tmdb/search`, `/api/log`, `/img/...`, `/sub/<id>/<key>.vtt?offset=`, `/file/<id>`, `/stream/<id>?start=&audio=&vcopy=`.

### B5. Keep the docs in step

If you change a setting, route, file layout or requirement, update `README.md` (people), this file (agents) and `config.example.json` in the same commit.
