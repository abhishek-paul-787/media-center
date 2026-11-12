# Media Center

A private, Netflix-style web page for the movies and TV shows on your own computer or drive.
You run one small Python program, open a page in your browser, and browse posters, read
descriptions, pick audio tracks and subtitles, and press play.

- **Nothing to install except Python and ffmpeg.** No pip packages, no Node, no database.
- **Your files stay where they are.** Media Center only reads them. It never moves or edits them.
- **Private by default.** It listens on your own computer only (`127.0.0.1`).
- **Plays almost anything.** Files the browser can play are sent as they are. Others are converted on the fly.

> Setting this up with an AI coding agent (Claude Code, etc.)? Point it at
> [`AGENTS.md`](AGENTS.md). It has step-by-step instructions written for agents.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install](#2-install)
3. [Tell it where your media is](#3-tell-it-where-your-media-is)
4. [Start it](#4-start-it)
5. [How to name your files](#5-how-to-name-your-files)
6. [Using the page](#6-using-the-page)
7. [Optional: better posters, cast photos and subtitles](#7-optional-better-posters-cast-photos-and-subtitles)
8. [Settings reference](#8-settings-reference)
9. [Keeping it running](#9-keeping-it-running)
10. [Troubleshooting](#10-troubleshooting)
11. [Privacy and safety](#11-privacy-and-safety)
12. [Updating and uninstalling](#12-updating-and-uninstalling)

---

## 1. What you need

| Need | Required? | Notes |
|---|---|---|
| Python 3.8 or newer | Yes | Standard library only. |
| `ffmpeg` and `ffprobe` | Yes | Used to read file info and convert video the browser can't play. |
| A web browser | Yes | Chrome, Brave, Edge and Firefox all work. |
| `git` | To download it | Or use GitHub's "Download ZIP" button instead. |
| VLC | No | Only for the "Open in VLC" button. |
| A GPU with VAAPI (Linux) | No | Makes conversion much faster. Detected automatically at `/dev/dri/renderD128`. |
| TMDB key / OpenSubtitles account | No | See [section 7](#7-optional-better-posters-cast-photos-and-subtitles). |

Check what you already have:

```bash
python3 --version
ffmpeg -version
ffprobe -version
```

If all three print a version, skip to [Install](#2-install).

## 2. Install

### 2a. Install Python and ffmpeg

**Linux**

| Distro | Command |
|---|---|
| Fedora / Bazzite / Nobara | `sudo dnf install python3 ffmpeg` (see the note below if `ffmpeg` is not found) |
| Ubuntu / Debian / Mint | `sudo apt install python3 ffmpeg` |
| Arch | `sudo pacman -S python ffmpeg` |
| Immutable distros (Silverblue, Bazzite) | `brew install ffmpeg`, or layer the package with `rpm-ostree install ffmpeg` and reboot |

> **Fedora note:** the stock `ffmpeg-free` package may lack some codecs. If playback of some
> files fails, install the full build from RPM Fusion.

**macOS** (with [Homebrew](https://brew.sh)): `brew install python ffmpeg`

**Windows** (PowerShell): `winget install Python.Python.3.12 Gyan.FFmpeg`, then close and
reopen PowerShell so the new programs are found. On Windows, use `python` instead of
`python3` in every command below.

### 2b. Download Media Center

```bash
git clone https://github.com/abhishek-paul-787/media-center.git
cd media-center
```

Replace `abhishek-paul-787/media-center` with the address of the repository you are using. No git?
Click **Code → Download ZIP** on the GitHub page, unzip it, and open a terminal in that folder.

## 3. Tell it where your media is

1. Copy the example settings file:
   ```bash
   cp config.example.json config.json        # Windows: copy config.example.json config.json
   ```
2. Open `config.json` in any text editor and change the `libraries` section to your folders:
   ```json
   "libraries": [
     { "path": "/home/you/Videos/Movies",  "kind": "movie" },
     { "path": "/home/you/Videos/TV Shows", "kind": "tv" }
   ]
   ```
   - `path` is the folder that holds your files. `~` means your home folder.
     On Windows, write paths with forward slashes: `"D:/Media/Movies"`.
   - `kind` is `"movie"`, `"tv"`, or `"auto"`. `auto` treats files named like `S01E02` as TV
     episodes and everything else as movies.
   - You can list as many folders as you like. Subfolders are searched too.
3. If the drive is external, **plug it in and mount it first**. A missing folder shows up on
   the page as "Library folder not found".

`config.json` holds your personal settings and any API keys. It is listed in `.gitignore`, so
it is never uploaded if you push your own changes.

> Skipping step 1 works too. On first start the program creates a `config.json` that points at
> `~/Media/Movies` and `~/Media/TV Shows`. If your folders are there, no editing is needed.

## 4. Start it

```bash
python3 server.py
```

Then open **http://127.0.0.1:8765** in your browser.

- The first start scans your folders and downloads posters and descriptions in the background.
  A big library can take several minutes. Posters appear as they arrive.
- To rescan after adding files, use the rescan option on the page, or restart with
  `python3 server.py --scan`.
- To stop, press `Ctrl+C` in the terminal.

## 5. How to name your files

Titles and years are guessed from file names, so clean names give better matches.

**Movies**: `Movie Name (Year).ext`, either loose or in their own folder.

```
Movies/
  The Matrix (1999).mkv
  Inception (2010)/Inception (2010).mp4
```

**TV shows**: put each show in a folder and put `SxxExx` in the episode file names.

```
TV Shows/
  Breaking Bad/
    Season 1/
      Breaking Bad S01E01.mkv
      Breaking Bad S01E02.mkv
```

Supported video types: `.mkv .mp4 .avi .m4v .webm .mov .ts .wmv`. Files with "sample" or "trailer" in the name are skipped. Files under 20 MB are ignored (they are assumed to be clips or samples). Junk like `1080p`,
`BluRay` or release-group tags in the name is stripped automatically.

**Subtitles:** put `.srt` or `.vtt` files next to the video with the same base name
(`Movie (2010).en.srt`). Subtitles inside the video file are also detected.

**If a title is matched wrongly**, use the edit option on its page ([section 6](#6-using-the-page)).

> Each title's ID comes from its file path. If you rename or move a file, the program treats it as
> new, and any manual edits you made to the old entry are lost.

## 6. Using the page

- **Tabs:** Home, Movies and TV Shows.
- **Cards:** click one to open its page with the banner, description, cast, crew, similar titles
  and subtitle options.
- **Player:**
  - Choose the audio track and subtitles.
  - Change subtitle size with the **A−/A+** buttons or the `-` and `+` keys.
  - The badge shows how it is playing: *Direct* (the original file), *Remux* (repackaged, no
    quality loss) or *Transcode* (converted).
  - Playback resumes where you left off. The position is stored in your browser only.
- **Edit a title (movies):** open the editor, paste an IMDb ID (`tt1234567`) or URL to re-identify
  the movie, fix the title, year or description, or give a poster or banner link. The link can
  be a direct image or a web page.
- **Open in VLC:** starts VLC on your computer at the current position, audio and subtitle.
  VLC must be installed.
- **Page looks stale after an update?** Hard-refresh with `Ctrl+Shift+R`.

## 7. Optional: better posters, cast photos and subtitles

It works without any accounts. These add more:

| Add this | You get | Free? |
|---|---|---|
| **TMDB API key** | Real backdrops, cast photos, crew, similar titles | Yes |
| **OpenSubtitles account + API key** | Subtitles downloaded automatically | Yes, with a daily limit |

**TMDB:** make a free account at <https://www.themoviedb.org>, then go to Settings → API and
request a key. Paste it into `config.json` as `"tmdb_key"`.

**OpenSubtitles:** make an account at <https://www.opensubtitles.com>, create an API consumer
at <https://www.opensubtitles.com/consumers>, then fill in `opensubtitles_key`,
`opensubtitles_user` and `opensubtitles_pass`.

Restart the server after editing `config.json`. Never share or commit `config.json`.

## 8. Settings reference

All settings live in `config.json`. Anything you leave out uses its default.

| Setting | Default | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | Address to listen on. Keep it as is unless you read [section 11](#11-privacy-and-safety). |
| `port` | `8765` | Port number. Change it if something else already uses 8765. |
| `site_name` | `Media Center` | The name shown in the page header and browser tab. |
| `libraries` | `~/Media/Movies`, `~/Media/TV Shows` | List of `{ "path", "kind" }` entries. |
| `tmdb_key` | empty | TMDB v3 key or v4 token. |
| `opensubtitles_key`, `_user`, `_pass` | empty | OpenSubtitles credentials. |
| `subtitle_languages` | `["en","es","fr"]` | Languages offered as subtitle download buttons (ISO codes). |
| `auto_subtitle_languages` | `["en"]` | Languages fetched automatically when you open a title. |
| `hardware_transcode` | `true` (used only if a GPU is found) | Set `false` to force software conversion. |
| `max_stream_width` | `3840` | Largest video width sent to the browser when converting. |

## 9. Keeping it running

**Run it in the background (Linux/macOS):**

```bash
nohup python3 server.py > server.log 2>&1 &
```

Stop it with `kill <PID>`. Find the PID with `ss -ltnp | grep 8765` (Linux) or
`lsof -i :8765` (macOS).

**Start at login on Linux (systemd user service):** create `~/.config/systemd/user/media-center.service`:

```ini
[Unit]
Description=Media Center

[Service]
WorkingDirectory=/full/path/to/media-center
ExecStart=/usr/bin/python3 server.py
Restart=on-failure

[Install]
WantedBy=default.target
```

Then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now media-center
```

If your media is on an external drive, the service may start before it is mounted. Restart the
service after mounting.

## 10. Troubleshooting

| What you see | What to do |
|---|---|
| `python3: command not found` | Install Python ([section 2a](#2a-install-python-and-ffmpeg)). On Windows use `python`. |
| Page is empty or says "Library folder not found" | Check the `path` values in `config.json`, and that any external drive is mounted. Then run `python3 server.py --scan`. |
| `Address already in use` | Another program uses the port. Change `port` in `config.json`, or stop the other copy of Media Center. |
| Can't open the page | Use `http://127.0.0.1:8765`, matching your `port`. Make sure the terminal still shows the server running. |
| Video won't play, or has no sound | Make sure `ffmpeg` is installed and on your PATH (`ffmpeg -version`). |
| Playback is slow to start or stutters | The file is being converted. A GPU with VAAPI helps. Direct play needs no conversion. |
| Wrong poster or title | Use the editor on that title's page and paste the correct IMDb ID. |
| New files don't appear | Rescan from the page, or restart with `--scan`. |
| No subtitles downloaded | Add OpenSubtitles details ([section 7](#7-optional-better-posters-cast-photos-and-subtitles)). Local and embedded subtitles work without it. |
| No cast photos or backdrops | Add a TMDB key. |
| Page didn't change after an update | Hard-refresh with `Ctrl+Shift+R`. |
| Messages mentioning `EBML` in the terminal | The source `.mkv` file is slightly damaged. It often still plays. |
| Want to start fresh | Stop the server and delete the `data/` folder. Your media is untouched. Manual title edits are lost. |

## 11. Privacy and safety

- By default only your own computer can reach the page. The "Open in VLC" feature only answers
  requests from your own computer.
- The only things sent to the internet are title lookups (IMDb suggestions, Wikipedia, TVmaze, and
  TMDB if you add a key) and subtitle searches (OpenSubtitles, if you add an account).
- Do **not** change `host` to `0.0.0.0` unless you understand the risk. There is no login, so
  anyone on your network could then watch your library.
- To watch on another device at home, the safe route is a private network tool such as
  Tailscale or WireGuard, or an SSH tunnel: `ssh -L 8765:127.0.0.1:8765 your-computer`.
- Keep `config.json` private. It can contain your API keys and passwords.

## 12. Updating and uninstalling

**Update:**

```bash
cd media-center
git pull
```

Your `config.json` and `data/` are not touched. Restart the server and hard-refresh the page.

**Uninstall:** stop the server and delete the `media-center` folder. Your media files are
never changed by this program.

---

## For contributors

- Backend: `server.py` (Python standard library only). UI: `index.html` (plain JavaScript, no build step).
- Keep it dependency-free.
- Details for AI agents and contributors are in [`AGENTS.md`](AGENTS.md).
- Licence: MIT, see [`LICENSE`](LICENSE).
