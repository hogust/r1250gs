#!/usr/bin/env python3
"""Add build/version metadata and live workflow status to the generated static site."""

from __future__ import annotations

import argparse
import html
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", default="public/index.html")
    args = parser.parse_args()

    html_path = Path(args.html)
    content = html_path.read_text(encoding="utf-8")

    repo = os.getenv("GITHUB_REPOSITORY", "hogust/r1250gs")
    sha = os.getenv("GITHUB_SHA", "").strip()
    short_sha = sha[:7] if sha else "lokal"
    branch = os.getenv("GITHUB_REF_NAME", "main")
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    now = datetime.now(ZoneInfo("Europe/Stockholm"))
    built_at = now.strftime("%Y-%m-%d %H:%M %Z")

    repo_url = f"https://github.com/{repo}"
    commit_url = f"{repo_url}/commit/{sha}" if sha else repo_url
    workflow_url = f"{repo_url}/actions/workflows/update-analysis.yml"
    run_url = f"{repo_url}/actions/runs/{run_id}" if run_id else workflow_url
    badge_url = f"{repo_url}/actions/workflows/update-analysis.yml/badge.svg?branch=main"

    css = """
    <style>
      .build-health {
        margin: 10px 18px 0; padding: 10px 14px; border: 1px solid #d9dee7;
        border-radius: 8px; display: flex; gap: 12px; align-items: center;
        flex-wrap: wrap; background: #fff;
      }
      .build-health img { height: 20px; vertical-align: middle; }
      .build-health a, .site-footer a { color: #0b57d0; text-decoration: none; }
      .build-health a:hover, .site-footer a:hover { text-decoration: underline; }
      .site-footer {
        margin: 48px 18px 18px; padding: 14px 0; border-top: 1px solid #ddd;
        color: #666; font-size: 12px; line-height: 1.7;
      }
    </style>
    """

    health = f"""
    <div class="build-health">
      <b>Automatisk uppdatering:</b>
      <a href="{html.escape(workflow_url)}" target="_blank" rel="noopener">
        <img src="{html.escape(badge_url)}" alt="GitHub Actions status">
      </a>
      <span>Senaste publicerade bygge: <a href="{html.escape(run_url)}" target="_blank" rel="noopener">Actions-körning</a></span>
    </div>
    """

    footer = f"""
    <footer class="site-footer">
      Byggd {html.escape(built_at)} · branch <b>{html.escape(branch)}</b> ·
      version <a href="{html.escape(commit_url)}" target="_blank" rel="noopener"><code>{html.escape(short_sha)}</code></a> ·
      <a href="{html.escape(run_url)}" target="_blank" rel="noopener">visa GitHub Actions-körning</a>
    </footer>
    """

    content = content.replace("</head>", css + "</head>", 1)

    # enhance_site.py already inserts .site-meta directly after <body>.
    marker = "</div>"
    meta_pos = content.find('class="site-meta"')
    if meta_pos >= 0:
        end_pos = content.find(marker, meta_pos)
        if end_pos >= 0:
            end_pos += len(marker)
            content = content[:end_pos] + health + content[end_pos:]
        else:
            content = content.replace("<body>", "<body>" + health, 1)
    else:
        content = content.replace("<body>", "<body>" + health, 1)

    content = content.replace("</body>", footer + "</body>", 1)
    html_path.write_text(content, encoding="utf-8")
    print(f"Buildstatus och versionsmetadata tillagd i {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
