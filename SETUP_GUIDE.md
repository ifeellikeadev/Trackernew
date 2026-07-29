# Setup Guide — zero GitHub knowledge required

This walks through every single click. It should take about 20–30
minutes the first time. You will not need to install anything or use
a command line — everything happens in your web browser.

---

## Part 1 — Create a GitHub account

1. Go to **https://github.com/signup**
2. Enter an email, create a password, pick a username (anything —
   it's not shown publicly unless you want it to be).
3. Verify your email if GitHub asks you to (check your inbox for a
   confirmation code).
4. You now have a GitHub account. GitHub is free for what we're doing.

---

## Part 2 — Create a new repository (your project's "folder" on GitHub)

1. Once logged in, click the **+** icon top-right → **New repository**.
2. **Repository name**: type `job-tracker` (or anything you like).
3. **Description**: optional, leave blank or write "personal job scraper."
4. Set it to **Private** — this is your personal job search data, no
   reason for it to be public. Click the "Private" radio button.
5. Leave everything else unchecked (no README, no .gitignore, no
   license — we already have our own files for that).
6. Click the green **Create repository** button.

You'll land on an empty repository page with a box of setup
instructions — ignore all of that, we're not using the command line.

---

## Part 3 — Upload the project files

1. On your computer, unzip the file I gave you (`job-scraper.zip`).
   You should see a folder called `job-scraper` containing things
   like `config`, `src`, `.github`, `README.md`, etc.

   > ⚠️ **Important**: some unzip tools hide folders that start with a
   > dot, like `.github`. Make sure your file browser is set to show
   > hidden files, or check that `.github` actually appears after
   > unzipping — that folder holds the automation and is essential.
   > On Windows, in File Explorer: View → Show → Hidden items. On
   > Mac, in Finder: press Cmd+Shift+. (period) while the folder is open.

2. Back on your empty GitHub repository page, click
   **"uploading an existing file"** (a blue link in the middle of the
   page — or use **Add file → Upload files** near the top right).

3. Open the unzipped `job-scraper` folder on your computer, select
   **everything inside it** (not the `job-scraper` folder itself —
   go one level in, select all the files and subfolders you see:
   `config`, `src`, `.github`, `data`, `archive`, `README.md`,
   `SETUP_GUIDE.md`, `requirements.txt`, `.gitignore`).

   > This list has 161 companies, deliberately — the more companies
   > covered, the more likely a solid chunk work cleanly on the first
   > try even before you fix anything. See Part 5 for what "some
   > fail" looks like and why that's expected, not a sign of a
   > broken setup.

4. Drag all of that into the GitHub upload box (the dashed rectangle
   that says "Drag files here to add them to your repository").

   > If `.github` doesn't seem to upload via drag-and-drop, GitHub's
   > uploader sometimes skips folders that were dragged individually.
   > If that happens: drag the files one folder at a time instead of
   > all at once — start with `.github`, wait for it to appear in the
   > list below the box, then drag the rest.

5. Scroll down, and in the **"Commit changes"** box at the bottom,
   you can leave the default message ("Add files via upload") or type
   something like "Initial upload." Click the green
   **Commit changes** button.

6. Refresh the page — you should now see all your folders and files
   listed in the repository.

---

## Part 4 — Give the automation permission to save its results

By default, GitHub Actions (the automation that runs the scraper) is
only allowed to *read* your repository, not write to it. We need to
switch that on so it can save the updated Excel file each day.

1. In your repository, click **Settings** (top menu, far right — you
   may need to click the "..." if the window is narrow).
2. In the left sidebar, click **Actions** → **General**.
3. Scroll down to **"Workflow permissions."**
4. Select **"Read and write permissions."**
5. Click **Save**.

---

## Part 5 — Run it for the first time (manually)

Let's test it once by hand before trusting the daily schedule.

1. Click the **Actions** tab (top menu of your repository).
2. You should see two workflows listed on the left: **"Daily job
   scrape"** and **"Monthly tracker reset."**
3. Click **"Daily job scrape."**
4. On the right, click the **"Run workflow"** dropdown button, then
   click the green **"Run workflow"** button that appears.
5. Wait about 10–20 seconds, then refresh the page. You'll see a new
   run appear with a yellow dot (running) that turns into either a
   green check (success) or a red X (something failed).
6. Click on the run to see the logs — it prints one line per company
   ("OK", "EMPTY", or "FAILED") so you can see what happened.

   > It's completely normal to see some "EMPTY" or "FAILED" lines —
   > not every company's career page is set up the same way, and a
   > few of the URLs I filled in may need small fixes over time (see
   > Part 7 below). As long as most companies say "OK," the tracker
   > is working.

7. Once it finishes successfully, go back to the **Code** tab (top
   left), open the `data` folder, and click on `job_tracker.xlsx`.
   GitHub will show you a preview of the spreadsheet with whatever it
   found.

---

## Part 6 — Your daily routine

That's it — from now on, this runs automatically every morning
(around 6–7am Munich time) without you doing anything.

To check it each day:

1. Go to your repository → **Code** tab → `data` folder →
   `job_tracker.xlsx` to preview it in the browser.
2. Or click the **⬇ Download raw file** button (top right of the
   preview) to save it to your computer and open it in Excel — this
   is the better option if you want filtering/sorting to work
   properly.
3. New rows added that day are highlighted in light green, so you can
   see at a glance what's new versus what you've already checked.
4. The "Last Seen" column updates every day a posting is still live,
   so if a row hasn't updated in a while, that job listing may have
   been taken down.

If a scheduled run ever fails, GitHub automatically emails you at the
address you signed up with — you don't need to keep checking the
Actions tab.

---

## Part 7 — Adding or fixing a company

Over time you'll want to add companies, or fix one that keeps
returning "EMPTY."

**To add a company:**
1. Go to `config/companies.yaml` in your repository, click the
   pencil icon (✏️, top right of the file view) to edit.
2. Copy one existing block (the lines starting with `- name:`) and
   fill in the new company's details.
3. Scroll down, commit the change (same green button as before).
   Tomorrow's run will pick it up automatically.

**To fix a company that returns nothing:**
1. Visit that company's actual careers page in your browser.
2. Look at the page — is it a normal list of jobs, or does it say
   "powered by Greenhouse / Lever / Personio / SmartRecruiters" at
   the bottom, or does the URL contain `myworkdayjobs.com`?
   - If you spot one of those names, edit that company's `ats:` field
     in `companies.yaml` to match (`greenhouse`, `lever`, `personio`,
     `smartrecruiters`, or `workday`).
   - For Greenhouse/Lever/SmartRecruiters, the `board_token` is
     usually visible in the URL of an individual job posting — e.g.
     `job-boards.greenhouse.io/COMPANYTOKEN/jobs/...` — copy that
     token into the `board_token:` field.
   - For Personio, the token is the subdomain — e.g.
     `companytoken.jobs.personio.de`.
3. If it's none of those (a custom-built careers page), leave
   `ats: auto` — it'll use the best-effort fallback scraper, which
   works reasonably well but isn't perfect for every custom site.

**To remove a company:** delete its block from `companies.yaml`
entirely, or put a `#` in front of each of its lines to disable it
without deleting it.

---

## Part 8 — Understanding the monthly reset

On the 1st of each month, a second automation runs automatically: it
moves the current `job_tracker.xlsx` into the `archive` folder
(renamed with that month's date, e.g. `job_tracker_2026-07.xlsx`) and
starts a fresh, empty tracker. Nothing is deleted — old months just
move out of your way into `archive/` in case you want to look back.

### Resetting it manually, any time

You don't have to wait for the 1st. To clear the tracker right now:

1. Go to the **Actions** tab.
2. Click **"Monthly tracker reset"** on the left.
3. Click **"Run workflow"** (dropdown) → green **"Run workflow"** button.
4. Wait ~10 seconds, refresh — once it shows a green check, `data/job_tracker.xlsx`
   is fresh and empty, and the previous version has moved into `archive/`.

This is the same automation, just triggered by you instead of by the
calendar — nothing extra to set up.

---

## Part 9 — Companies that may never work well (and that's OK)

Some companies build their career pages as JavaScript apps that load
job listings *after* the page opens in a browser. Our scraper fetches
the raw page (it doesn't run a browser), so for these companies it
will see an empty shell no matter how `companies.yaml` is configured.
This mainly affects large tech companies with custom career sites —
Apple, Amazon, Google, Microsoft, SAP, Oracle, Salesforce, Adobe,
ServiceNow, and similar. It's a real, structural limitation, not
something Part 7's fixes can solve. If you notice a cluster of big
names consistently coming back "EMPTY," this is almost always why —
no further troubleshooting needed on those specific ones. Everything
running on Greenhouse, Lever, Personio, SmartRecruiters, or a real
Workday tenant subdomain is unaffected by this and should work as
designed.

---

## Troubleshooting

**"Run workflow" button doesn't appear on the Actions tab**
Make sure you completed Part 4 (workflow permissions) and that the
`.github/workflows` folder actually uploaded — check the Code tab.

**A run fails with a permission/403 error when committing**
Go back to Part 4 and double check "Read and write permissions" is
selected and saved.

**Most companies say "EMPTY" or "FAILED"**
That likely means the generic fallback scraper isn't finding job
links on those particular pages. Start with the companies you care
about most and fix them one at a time using Part 7 — you don't need
all ~85 working to get value from this.

**I want to pause it temporarily**
Actions tab → Daily job scrape → "..." menu (top right) → Disable
workflow. Re-enable the same way when you're ready.
