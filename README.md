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
