# Update the desktop brain → unlock all 19 BertOS tools

Your desktop's `bertosV2` brain is running an **older build** that's missing some
routes (Council, Deep Build, plan, refute, grounded-objective, recommend, usage,
foreman). The body + bridge are already wired for all 19 tools — they just need
the brain updated. This is **code-only** (your projects/run-history in
`.bertos-runtime` and your `node_modules` are preserved — they're not in the
package). Do it when **awake** (it restarts the live brain; confirm it comes back).

## On the LAPTOP — serve the package over Tailscale
```bash
cd ~/bertos-serve 2>/dev/null || mkdir -p ~/bertos-serve && cp ~/bertos-migration/bertos-brain.tgz ~/bertos-serve/ && cd ~/bertos-serve
python3 -m http.server 8077 --bind 100.103.129.83
```
(sha256 of bertos-brain.tgz: `7e05f3463e3b35f9657940e9330699462d901121ce775cdbe6fac0fde73614be`)

## On the DESKTOP (PowerShell)
```powershell
# 0) Find where the brain lives (the folder you run `npm run bertos:host` from).
#    Common: C:\Users\owner\Documents\bertosV2  — set $brain to it:
$brain = "C:\Users\owner\Documents\bertosV2"   # <-- adjust if different

# 1) Download + verify
cd $brain\..
curl.exe -o bertos-brain.tgz http://100.103.129.83:8077/bertos-brain.tgz
(Get-FileHash bertos-brain.tgz -Algorithm SHA256).Hash   # must equal the sha256 above

# 2) Extract over the existing brain (preserves node_modules + .bertos-runtime)
tar -xzf bertos-brain.tgz

# 3) Pick up any new deps
cd $brain
npm install

# 4) Restart the brain: stop the current `npm run bertos:host` (Ctrl+C its window,
#    or close it), then start it fresh:
npm run bertos:host
```

## Verify (either machine)
```bash
# Council + Deep Build routes should now exist (was 404):
curl -s -o /dev/null -w "council: %{http_code}\n"  -X POST -H "content-type: application/json" -d "{}" http://100.127.213.97:3000/api/chat/council
curl -s -o /dev/null -w "deepjobs: %{http_code}\n" -X POST -H "content-type: application/json" -d "{}" http://100.127.213.97:3000/api/deep/jobs
# 400 = route exists ✓   404 = still old
```
The body picks up the new routes immediately (the bridge re-probes per call — no
body rebuild needed). Then all 19 `mcp__brain__*` tools work in BertOS.

> Laptop-side: stop the http.server (Ctrl+C) when the download's done.
