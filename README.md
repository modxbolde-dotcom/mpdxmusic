# 🎶 Telegram Music Bot

A fast, production-ready Telegram music bot that streams YouTube audio into
group voice chats. Queue, controls, playlists, lyrics, thumbnails and an
owner panel are all included.

**Your bot's name is not hard-coded anywhere.** Set the `BOT_NAME`
environment variable once and that name appears in every menu, card, button
and log line.

---

## Features

| Area | Commands |
|---|---|
| Playback | `/play` `/vplay` `/pause` `/resume` `/skip` `/stop` `/replay` |
| Queue | `/queue` `/np` `/remove` `/clearqueue` `/shuffle` |
| Controls | `/volume` `/mute` `/unmute` `/loop` `/loopall` `/noloop` `/seek` `/seekback` `/speed` `/playmode` |
| Playlists | `/playlist` `/addplaylist` `/myplaylist` `/playplaylist` `/delplaylist` |
| Streams | `/live` `/radio` `/channelplay` |
| Extras | `/lyrics` `/search` `/download` `/ping` `/alive` `/about` `/stats` |
| Owner | `/panel` `/broadcast` `/logs` `/restart` `/reboot` `/update` `/setpic` |

- Direct CDN streaming with a local download fallback
- Autoplay when the queue runs dry
- Generated "now playing" thumbnail cards
- MongoDB-backed settings, playlists and caches
- Optional lean profile (`MUSIC_ONLY_MODE=true`) that loads only music handlers

---

## What you need before deploying

| Value | Where to get it |
|---|---|
| `API_ID`, `API_HASH` | https://my.telegram.org |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| Assistant login | Open `/panel` → **Assistant Login** and choose phone + OTP/2FA or session login; the session is saved in MongoDB and no session env variable is required. |
| `MONGO_DB_URI` | a free cluster at [MongoDB Atlas](https://www.mongodb.com/atlas) |
| `OWNER_ID` | your numeric Telegram user ID |
| `LOG_GROUP_ID` | a private group where the bot posts logs |

Optional: `YT_COOKIES` (strongly recommended on cloud hosts),
`YOUTUBE_API_KEY`, `GENIUS_API_TOKEN`.

---

## Deploy on Render

1. Push this repository to your own GitHub account.
2. In Render: **New → Blueprint** and select the repository.
   Render reads [`render.yaml`](render.yaml) and creates one
   **Background Worker** running the Docker image.
3. Fill in the environment variables when prompted (see the table above and
   [`.env.example`](.env.example) for the full list).
4. Deploy. The first build takes a few minutes because ffmpeg, Deno and the
   YouTube PO-token provider are installed inside the image.

The bot is a worker, not a website — it has no public URL and opens no port.
The `starter` plan (512 MB) is enough for a small group; use `standard` for
heavier use.

> **Note on the free plan:** Render's free tier does not include background
> workers, and free web services sleep when idle. A music bot must stay
> awake, so a paid worker plan is required.

---

## Run locally

```bash
git clone <your-repo-url>
cd <your-repo>
cp .env.example .env        # then fill in your values
pip install -r requirements.txt
bash start
```

ffmpeg must be installed on your machine.

---

## Buyer handover checklist

This package contains source code and deployment templates only. It does not
contain Telegram credentials, MongoDB credentials, session strings, cookies,
or a preconfigured repository URL.

Before first launch, the new owner should:

1. Copy `.env.example` to `.env` and fill in the required values.
2. Generate a **new** assistant session with `python3 genstring.py`; never
   reuse a session string from another deployment.
3. Set a unique `BOT_NAME`, `BOT_USERNAME`, `OWNER_ID`, and `OWNER_NAME`.
4. Keep `.env`, `*.session`, YouTube cookies, and database URLs private.
5. Make the bot and assistant admin in the target group with the permissions
   required for voice-chat playback.
6. If the owner does not need remote administration, disable or remove the
   `/shell`, `/eval`, and `/update` owner commands before delegating access.

The Docker/Render path is the recommended deployment path. The `setup` script
is an optional local installer and only clones a repository when
`MUSICBOT_REPO_URL` is explicitly provided.

---

## Branding

Everything the user sees comes from two variables:

```env
BOT_NAME=Music Bot
BOT_TAGLINE=High quality music, zero lag 🎧
```

`BOT_NAME` is read once at startup in [`utils/branding.py`](utils/branding.py)
and used everywhere else. To rename the bot, change the variable and restart —
no code edits.

---

## Project layout

```
melody/           bot package
  config.py       all environment variables
  core/           voice-call, queue, autoplay and yt-dlp engine
  plugins/        command handlers (music / misc / owner)
utils/            database, theming, thumbnails, helpers
assets/fonts/     bundled fonts used by generated cards
bin/post_compile  installs Deno + the YouTube PO-token provider
Dockerfile        image used by Render
render.yaml       Render blueprint
```

---


### YouTube cookies

For Heroku/cloud deployments, open `/panel` → **Add Cookies** and send a fresh logged-in `cookies.txt` file (or its Netscape/base64 text); do not put cookies in Heroku env. The owner-only handler deletes the incoming message after processing, activates cookies immediately, and appends each file to a MongoDB-backed cookie pool for restart recovery. Send multiple files one by one: requests rotate through the pool and a bot-wall response cools down only the affected cookie instead of disabling every cookie. Cookie expiration is controlled by YouTube/browser sessions and cannot be made permanent by software; export a fresh cookie file when YouTube invalidates one.
