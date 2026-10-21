import json
import os
import sys
import math
import datetime
import collections
import urllib.request

API = "https://api.github.com/graphql"

FONT = "SFMono-Regular, Menlo, Consolas, monospace"
LABEL = "#8b949e"
VALUE = "#ffffff"
BORDER = "#30363d"
CARD_W, CARD_H = 380, 280
PAD = 24

ACCOUNTS = [
    (os.environ["ACCOUNT_1_LOGIN"], os.environ["ACCOUNT_1_TOKEN"]),
    (os.environ["ACCOUNT_2_LOGIN"], os.environ["ACCOUNT_2_TOKEN"]),
]

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
README_PATH = os.path.join(os.path.dirname(__file__), "..", "README.md")


def bust_cache(readme_path, filenames):
    import re
    import time

    with open(readme_path) as f:
        content = f.read()

    ts = int(time.time())
    for name in filenames:
        pattern = re.compile(r'(src="assets/' + re.escape(name) + r')(\?[^"]*)?"')
        content = pattern.sub(rf'\1?t={ts}"', content)

    with open(readme_path, "w") as f:
        f.write(content)


def gql(token, query, variables):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


REPOS_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    followers { totalCount }
    repositories(first: 50, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        isEmpty
        stargazerCount
        defaultBranchRef { name }
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""

HISTORY_QUERY = """
query($login: String!, $name: String!, $since: GitTimestamp!, $cursor: String) {
  repository(owner: $login, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, since: $since, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes { committedDate }
          }
        }
      }
    }
  }
}
"""


def fetch_account(login, token, since_iso):
    stars = 0
    followers = 0
    languages = collections.Counter()
    commit_days = collections.Counter()
    repo_names = []

    cursor = None
    while True:
        data = gql(token, REPOS_QUERY, {"login": login, "cursor": cursor})
        u = data["data"]["user"]
        followers = u["followers"]["totalCount"]
        page = u["repositories"]
        for n in page["nodes"]:
            if n["isEmpty"] or not n["defaultBranchRef"]:
                continue
            stars += n["stargazerCount"]
            repo_names.append(n["name"])
            for edge in n["languages"]["edges"]:
                languages[edge["node"]["name"]] += edge["size"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    for repo in repo_names:
        cursor = None
        while True:
            data = gql(token, HISTORY_QUERY, {"login": login, "name": repo, "since": since_iso, "cursor": cursor})
            branch = data["data"]["repository"]["defaultBranchRef"]
            if branch is None or branch["target"] is None:
                break
            hist = branch["target"]["history"]
            for node in hist["nodes"]:
                commit_days[node["committedDate"][:10]] += 1
            if not hist["pageInfo"]["hasNextPage"]:
                break
            cursor = hist["pageInfo"]["endCursor"]

    repos = len(repo_names)
    return {
        "stars": stars,
        "repos": repos,
        "followers": followers,
        "languages": dict(languages),
        "commit_days": dict(commit_days),
    }


def build_calendar(merged_days, out_path):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=370)
    start -= datetime.timedelta(days=(start.weekday() + 1) % 7)

    dates = []
    d = start
    while d <= today:
        dates.append(d)
        d += datetime.timedelta(days=1)

    n_weeks = (len(dates) + 6) // 7
    cell, gap = 10, 3
    pad_left, pad_top, pad_right, pad_bottom = 24, 14, 2, 2
    width = pad_left + n_weeks * (cell + gap) + pad_right
    height = pad_top + 7 * (cell + gap) + pad_bottom

    max_count = max(merged_days.values()) if merged_days else 1
    max_count = max(max_count, 1)

    def color(count):
        if count == 0:
            return "#30363d"
        ratio = count / max_count
        if ratio < 0.15:
            return "#595959"
        elif ratio < 0.35:
            return "#8c8c8c"
        elif ratio < 0.6:
            return "#bfbfbf"
        return "#ffffff"

    rects, month_labels = [], []
    last_month = None
    for i, dt in enumerate(dates):
        wi = i // 7
        dow = (dt.weekday() + 1) % 7
        if dow == 0 and dt.month != last_month:
            last_month = dt.month
            x = pad_left + wi * (cell + gap)
            month_labels.append(
                f'<text x="{x}" y="{pad_top - 4}" font-size="9" font-family="{FONT}" fill="{LABEL}">{dt.strftime("%b")}</text>'
            )
        x = pad_left + wi * (cell + gap)
        y = pad_top + dow * (cell + gap)
        count = merged_days.get(dt.isoformat(), 0)
        rects.append(
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{color(count)}"><title>{dt.isoformat()}: {count}</title></rect>'
        )

    weekday_labels = []
    for row, label in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        y = pad_top + row * (cell + gap) + cell - 1
        weekday_labels.append(f'<text x="0" y="{y}" font-size="9" font-family="{FONT}" fill="{LABEL}">{label}</text>')

    svg = f'''<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
{"".join(month_labels)}
{"".join(weekday_labels)}
{"".join(rects)}
</svg>'''
    with open(out_path, "w") as f:
        f.write(svg)


def build_stats_card(stars, commits, repos, followers, longest_streak, current_streak, languages_used, out_path):
    width, height = CARD_W, CARD_H
    r, stroke, ring_gap = 42, 10, 46
    cx = width - PAD - r
    ring_left_edge = cx - r - stroke / 2
    col_right = ring_left_edge - ring_gap

    rows = [
        ("Stars", stars),
        ("Commits (1y)", commits),
        ("Repos", repos),
        ("Followers", followers),
        ("Longest Streak", f"{longest_streak}d"),
        ("Current Streak", f"{current_streak}d"),
        ("Languages", languages_used),
    ]
    lines = []
    top, bottom = 58, 250
    row_h = (bottom - top) / (len(rows) - 1)
    y = top
    for label, value in rows:
        lines.append(f'<text x="{PAD}" y="{y}" font-size="13" font-family="{FONT}" fill="{LABEL}">{label}</text>')
        lines.append(
            f'<text x="{col_right}" y="{y}" font-size="13" font-family="{FONT}" fill="{VALUE}" text-anchor="end">{value}</text>'
        )
        y += row_h

    pct = min(99, round(commits / 1500 * 100))
    cy = 154
    circumference = 2 * math.pi * r
    arc = circumference * pct / 100
    gap = circumference - arc

    ring = f'''
<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{BORDER}" stroke-width="{stroke}"/>
<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{VALUE}" stroke-width="{stroke}"
  stroke-dasharray="{arc:.2f} {gap:.2f}" stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>
<text x="{cx}" y="{cy - 2}" font-size="20" font-family="{FONT}" fill="{VALUE}" text-anchor="middle">{pct}%</text>
<text x="{cx}" y="{cy + 16}" font-size="9" font-family="{FONT}" fill="{LABEL}" text-anchor="middle">ACTIVITY</text>
'''

    svg = f'''<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="8" fill="none" stroke="{BORDER}"/>
<text x="{PAD}" y="28" font-size="13" font-family="{FONT}" fill="{VALUE}">STATS</text>
{"".join(lines)}
{ring}
</svg>'''
    with open(out_path, "w") as f:
        f.write(svg)


def build_langs_card(languages, out_path, top_n=6):
    width, height = CARD_W, CARD_H
    total = sum(languages.values()) or 1
    items = sorted(languages.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    bar_x = PAD
    bar_w = width - PAD * 2
    top, bottom = 58, 238
    row_h = (bottom - top) / (len(items) - 1) if len(items) > 1 else 0

    lines = []
    y = top
    for name, size in items:
        pct = size / total * 100
        lines.append(f'<text x="{bar_x}" y="{y}" font-size="12" font-family="{FONT}" fill="{VALUE}">{name}</text>')
        lines.append(
            f'<text x="{bar_x + bar_w}" y="{y}" font-size="12" font-family="{FONT}" fill="{LABEL}" text-anchor="end">{pct:.1f}%</text>'
        )
        bar_y = y + 8
        lines.append(f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="6" rx="3" fill="{BORDER}"/>')
        lines.append(f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w * pct / 100:.1f}" height="6" rx="3" fill="{VALUE}"/>')
        y += row_h

    svg = f'''<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="8" fill="none" stroke="{BORDER}"/>
<text x="{PAD}" y="28" font-size="13" font-family="{FONT}" fill="{VALUE}">TOP LANGUAGES</text>
{"".join(lines)}
</svg>'''
    with open(out_path, "w") as f:
        f.write(svg)


def main():
    since_iso = (datetime.datetime.utcnow() - datetime.timedelta(days=370)).strftime("%Y-%m-%dT00:00:00Z")

    results = {}
    for login, token in ACCOUNTS:
        results[login] = fetch_account(login, token, since_iso)
        print(f"{login}: {results[login]['repos']} repos, {sum(results[login]['commit_days'].values())} commits", file=sys.stderr)

    stars = sum(r["stars"] for r in results.values())
    repos = sum(r["repos"] for r in results.values())
    followers = sum(r["followers"] for r in results.values())

    merged_days = collections.Counter()
    for r in results.values():
        merged_days.update(r["commit_days"])
    commits = sum(merged_days.values())

    langs = collections.Counter()
    for r in results.values():
        langs.update(r["languages"])

    today = datetime.date.today()
    start = today - datetime.timedelta(days=370)
    longest, run = 0, 0
    cursor = start
    while cursor <= today:
        if merged_days.get(cursor.isoformat(), 0) > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
        cursor += datetime.timedelta(days=1)

    cursor = today
    current_streak = 0
    while merged_days.get(cursor.isoformat(), 0) > 0:
        current_streak += 1
        cursor -= datetime.timedelta(days=1)

    languages_used = len(langs)

    os.makedirs(ASSETS_DIR, exist_ok=True)
    build_calendar(merged_days, os.path.join(ASSETS_DIR, "calendar.svg"))
    build_stats_card(stars, commits, repos, followers, longest, current_streak, languages_used, os.path.join(ASSETS_DIR, "stats-card.svg"))
    build_langs_card(dict(langs), os.path.join(ASSETS_DIR, "langs-card.svg"))
    bust_cache(README_PATH, ["calendar.svg", "stats-card.svg", "langs-card.svg"])


if __name__ == "__main__":
    main()
