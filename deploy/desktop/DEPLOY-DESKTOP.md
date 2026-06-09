# Deploy BertOS to the always-on desktop (Docker, Tailscale-only)

Goal: BertOS runs **24/7 on the desktop** (`desktop-u3m3uq1`, i9 / RTX 3070),
reachable only from your tailnet (laptop + phone), so the 7am brief fires and
you can open the UI from anywhere — without the laptop being on.

**Shape of this deploy**
- **Docker Compose** stack: BertOS body + ChromaDB + SearXNG + ntfy, with
  `restart: unless-stopped` (survives reboots once Docker Desktop starts on login).
- **Native Windows Ollama** (your 12 models, RTX 3070) — the container reaches
  it via `host.docker.internal`; no GPU passthrough needed.
- **Your data migrated** from the laptop (Gmail, Google Calendar, memory, the
  encryption key) so everything works immediately — no re-entering the app password.
- **Tailscale Serve** exposes the UI to your tailnet only. The LAN never sees it,
  and it gets automatic HTTPS at `https://desktop-u3m3uq1.tail3ae957.ts.net`.

---

## Prerequisites (on the desktop)

1. **Docker Desktop** installed and running. Settings → General →
   **"Start Docker Desktop when you log in"** = ON (this is what makes the stack
   come back after a reboot).
2. **Tailscale** installed, signed in, connected (you already reach the brain
   over it).
3. **Native Ollama** running with your models. The container reaches it over the
   Docker bridge, so Ollama must listen beyond loopback:
   - Set a system env var **`OLLAMA_HOST=0.0.0.0`**, then restart Ollama
     (quit from the tray and relaunch). Verify: `curl http://localhost:11434/api/tags`
     lists your models.
4. *(Optional, for brain tools)* the **bertosV2 brain** running on `:3000`
   (`npm run bertos:host`). Not required for email/calendar/brief.

---

## Part A — On the LAPTOP: package + send your data

From the `odysseus` repo on the laptop:

```bash
bash deploy/desktop/package-migration.sh
```

It stops the local server (so the DB snapshot is clean and you don't end up with
two instances both pushing the brief), tars `data/` (**including the `.app_key`
encryption key**) and the memory vault, and prints a Taildrop command. Run it:

```bash
tailscale file cp ~/bertos-migration/bertos-data.tgz ~/bertos-migration/bertos-vault.tgz desktop-u3m3uq1:
```

> The `.app_key` Fernet key must travel — without it the migrated Gmail/CalDAV
> passwords can't be decrypted on the desktop. Taildrop runs over WireGuard
> between your own devices, so the key stays inside your tailnet.

Leave the laptop server **off** after this — the desktop is now the primary.

---

## Part B — On the DESKTOP (PowerShell)

The `bertos` branch is local-only (never pushed), so the **code travels as a
Taildrop tarball** (`bertos-code.tgz`) alongside the data + vault — no git needed.

### 1. Receive the three Taildrop files

```powershell
cd $env:USERPROFILE\Downloads
tailscale file get .          # pulls bertos-code.tgz, bertos-data.tgz, bertos-vault.tgz here
dir bertos-*.tgz              # confirm all three (use the plain names if you see numbered dupes)
```
(If `tailscale` isn't on PATH: `& "C:\Program Files\Tailscale\tailscale.exe" file get .`)

### 2. Extract the code, then the data + vault into it

```powershell
mkdir C:\BertOS -Force
cd C:\BertOS
tar -xzf "$env:USERPROFILE\Downloads\bertos-code.tgz"               # -> C:\BertOS\odysseus\
cd C:\BertOS\odysseus
tar -xzf "$env:USERPROFILE\Downloads\bertos-data.tgz"   -C .        # -> .\data\
tar -xzf "$env:USERPROFILE\Downloads\bertos-vault.tgz"  -C .        # -> .\BertOS-Vault\
```

After this you should have `.\data\.app_key`, `.\data\app.db`,
`.\data\user_prefs.json`, and `.\BertOS-Vault\Memory\bertos\`.

### 3. Configure

```powershell
copy deploy\desktop\.env.desktop.example .env
```

The defaults are correct for this host (loopback bind + native Ollama +
`./BertOS-Vault`). Open `.env` only if your vault landed somewhere other than
`.\BertOS-Vault` (set `BERTOS_VAULT_DIR` — the overlay maps it to
`BERTOS_OBSIDIAN_VAULT=/vault` inside the container).

### 4. Start the stack

```powershell
docker compose -f docker-compose.yml -f deploy\desktop\docker-compose.bertos.yml up -d --build
```

First build takes a few minutes. Check it's healthy:

```powershell
docker compose ps
curl http://127.0.0.1:7777/api/auth/status      # -> {"configured":true,"authenticated":true,...}
```

### 5. Expose it to your tailnet (Tailscale-only, with HTTPS)

```powershell
tailscale serve --bg 7777
tailscale serve status
```

Now open **https://desktop-u3m3uq1.tail3ae957.ts.net** from your phone or laptop
(while on Tailscale). `--bg` persists across reboots.

> To take it down later: `tailscale serve --https=443 off`.

---

## Part C — Verify it's a real daily driver

1. **From your phone** (Tailscale on): open
   `https://desktop-u3m3uq1.tail3ae957.ts.net` — you should land straight in the
   app (no login), with your email + calendar already configured.
2. **Calendar synced:** in the app, Calendar → it shows your Google events /
   US holidays. Or: `curl -X POST http://127.0.0.1:7777/api/calendar/sync` on
   the desktop → `{"calendars":2,"events":35,...}`.
3. **Daily brief armed:** the "Daily Brief" task shows next run **07:00**. To
   test the phone push now without waiting, trigger it once from the Tasks UI
   (or the task's "Run now") — the brief should land on your phone via ntfy.
4. **Survives reboot:** restart the desktop, log in, wait ~1 min, re-open the
   tailnet URL. `restart: unless-stopped` + Docker-start-on-login + `serve --bg`
   bring it all back.

---

## Optional — enable brain tools (Deep Build / Council)

Only if the bertosV2 brain runs on this host (`:3000`):

1. In `docker-compose.bertos.yml`, uncomment the `/brain` mount and the
   `BERTOS_BRAIN_DIR=/brain` env line.
2. Set `BERTOS_BRAIN_DIR_HOST` in `.env` to the bertosV2 path (e.g.
   `C:\BertOS\bertosV2`).
3. Recreate: `docker compose -f docker-compose.yml -f deploy\desktop\docker-compose.bertos.yml up -d`.
4. Logs should show `BertOS Brain (brain) - 6 tools via stdio`. (If the script
   isn't found the body just logs a warning and skips it — brain tools off, body fine.)

---

## Troubleshooting

- **Models don't respond / "no endpoint":** the container can't reach Ollama.
  Confirm `OLLAMA_HOST=0.0.0.0` is set and Ollama was restarted; from the
  container: `docker compose exec odysseus curl http://host.docker.internal:11434/api/tags`.
- **Port 7777 already in use:** change `APP_PORT` in `.env` and re-run, then
  `tailscale serve --bg <newport>`.
- **Two daily briefs / double email drafts:** an old instance is still running
  (the laptop, or a stray native run). Stop it — only the desktop should be live.
- **`tailscale serve` says HTTPS unavailable:** enable HTTPS/MagicDNS in the
  Tailscale admin console (DNS → enable MagicDNS + HTTPS certificates).
- **Calendar empty after migration:** confirm `.\data\.app_key` exists (the key
  travelled). Without it, encrypted passwords decrypt to empty → "not configured".

---

## What's where

| Piece | Location |
|---|---|
| Overlay compose | `deploy/desktop/docker-compose.bertos.yml` |
| Env template | `deploy/desktop/.env.desktop.example` → repo-root `.env` |
| Laptop packager | `deploy/desktop/package-migration.sh` |
| Data (key, DB, prefs) | `./data/` (bind-mounted to `/app/data`) |
| Memory vault | `./BertOS-Vault/` (bind-mounted to `/vault`) |
| UI (tailnet) | `https://desktop-u3m3uq1.tail3ae957.ts.net` |
| UI (local) | `http://127.0.0.1:7777` |
