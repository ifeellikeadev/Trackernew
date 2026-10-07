"""Replay saved scrape results onto latest tracker, without rebasing binary files."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import yaml
from src.tracker import update_tracker, update_swiss_tracker
from src.generate_html import generate

def apply_results(root, results_file):
    data = json.loads(results_file.read_text(encoding="utf-8"))
    profile = yaml.safe_load((root / "config/cv_profile.yaml").read_text(encoding="utf-8")) or {}
    tracker = root / "data/job_tracker.xlsx"
    update_tracker(tracker, data["main_jobs"], min_score=profile.get("main_min_score", 0))
    update_swiss_tracker(tracker, data["swiss_jobs"], min_score=profile.get("swiss_min_score", 0))
    generate(tracker, root / "docs/index.html")

def publish(root, results_file, branch, attempts=3):
    results_file = results_file.resolve()
    def git(*args, cwd=root, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)
    git("check-ref-format", "--branch", branch)
    for attempt in range(attempts):
        git("fetch", "origin", branch)
        with tempfile.TemporaryDirectory(prefix="tracker-publish-") as tmp:
            worktree = Path(tmp) / "repo"
            git("worktree", "add", "--detach", str(worktree), "FETCH_HEAD")
            try:
                env = dict(os.environ)
                env.pop("PYTHONPATH", None)
                subprocess.run([os.sys.executable, "-m", "src.publish_results", str(results_file), "--apply-only"], cwd=worktree, env=env, check=True)
                git("add", "data/job_tracker.xlsx", "docs/index.html", cwd=worktree)
                if git("diff", "--cached", "--quiet", cwd=worktree, check=False).returncode == 0:
                    print("No changes to publish")
                    return
                git("-c", "user.name=job-scraper-bot", "-c", "user.email=actions@users.noreply.github.com", "commit", "-m", "Daily job scrape update", cwd=worktree)
                result = git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree, check=False)
                if result.returncode == 0:
                    print("Published tracker and HTML successfully")
                    return
                print(result.stderr)
            finally:
                git("worktree", "remove", "--force", str(worktree), check=False)
    raise RuntimeError("Publication failed after retries; saved results remain available. No force push used.")

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("results_file", type=Path)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--apply-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.apply_only:
        apply_results(root, args.results_file)
    else:
        publish(root, args.results_file, args.branch)

if __name__ == "__main__":
    run()
