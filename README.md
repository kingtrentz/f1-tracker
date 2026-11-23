# F1 Tracker (unofficial fan project)

Live site: https://kingtrentz.github.io/f1-tracker/

Fan project. Not affiliated with Formula 1, the FIA, or any team.
by kingtrentz⚡️ x claude

## How it runs
- GitHub Actions wakes every 10 minutes (`.github/workflows/update.yml`).
- Race weekends (12 hours before FP1 until 12 hours after the race): updates every run.
- Other times: about once an hour.
- `build.py` pulls Jolpica-F1 + OpenF1 (no keys), builds 4 pages + `data.json`, and GitHub Pages publishes them.
- Next Race > Track info: race weekend forecast per day (Open-Meteo, free, no key) from 7 days before FP1; on race days it switches to current conditions plus OpenF1 track temperature.
- Next Race > weekend results: one button per session (FP1 to Race, plus Grid) with each session's P1; tap any for full results. Finished schedule rows jump there too. Shows this weekend, or last weekend until the next one starts.
  - Practice and sprint quali from OpenF1; quali, sprint and race switch to Jolpica's official result once posted.
  - Grid: OpenF1 starting grid vs quali position; drivers who dropped 2+ places get a ★ and a grid penalty footnote.
  - Race and sprint: tyre strategy per driver (OpenF1 stints/pit) and key moments (safety cars, red flags, penalties from race control).
  - A session counts as live until its nominal end, then "results soon" until results exist (races up to 3 h, for red flags). While waiting, the open page asks OpenF1 directly every 2 minutes so results show up before the next site update.
  - No live-timing box: OpenF1's free feed posts after each session.
- Top stories (`news.py`): each build reads BBC Sport, Formula 1, The Race, Autosport and Motorsport.com RSS, files every headline under its round (named in the headline/category, else by date), groups headlines about the same story and ranks by how many outlets cover it. Headlines and links only. Saved in `news/<season>.json`; a round's top list is frozen 10 days after its race. Shown on Next Race (this round + next round's build-up) and inside each race on Standings.
- Suggestion box at the bottom of every page: posts to a private ntfy.sh topic (name in templates/page.html, `IDEAS`). Subscribe to it in the ntfy app to get each one as a phone notification.
- Tabs: Next Race, Standings (with race results and teammate H2H), History, Ghost Lap.
- Ghost Lap: `ghost.py` saves every driver's best qualifying lap of every weekend since 2023 (OpenF1 car positions) into `ghost/`, and the page races any two of them on the track map (one small file per lap in `ghost/laps/`, loaded on demand). New laps are added about 2 hours after each qualifying; `python ghost.py` fetches anything missing.
- Pages pull the newest `data.json` every 2 minutes while open.
- Once a week it commits `heartbeat.txt` so GitHub doesn't pause the schedule (it pauses public-repo schedules after 60 quiet days).
- When a season ends it switches to the next calendar on its own once Jolpica-F1 publishes it.

## One-time setup
1. Create a **public** repo named `f1-tracker` on github.com (no README).
2. Upload everything in this folder: `build.py`, `README.md`, `.gitignore`, and the `templates` folder.
3. Add the workflow file: Add file > Create new file, name it `.github/workflows/update.yml`, paste in the contents of the same file from this folder, commit.
4. Settings > Pages > Build and deployment > Source: **GitHub Actions**.
5. Actions tab > "Update F1 tracker" > Run workflow. The site is live a minute or two later.

Optional: healthchecks.io email if updates stop. Make a check (period 1 hour, grace 1 hour), then Settings > Secrets and variables > Actions > New repository secret, name `HEALTHCHECK_URL`, value = the ping URL.

## Run by hand
`python build.py` builds into `_site/` (open `_site/index.html`). Nothing is uploaded from your PC.
