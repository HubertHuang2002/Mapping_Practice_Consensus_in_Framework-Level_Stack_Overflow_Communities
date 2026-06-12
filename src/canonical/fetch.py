"""Fetch the out-of-window canonical question q54069253 + its answers from the SE API.

Module D probe fixture builder. The 132 in-window duplicates (+ their answers) already
live in the main DB (data/so_data_reactjs.db); only this 2019 canonical is outside the
2023-2026 window, so we pull it once and store it probe-local. We deliberately do NOT
insert into the main DB — Module A owns that window invariant (see PLAN ▼Q5b).

Re-runnable. Reads SE_API_KEY from .env. Public CC-BY-SA data.
"""
import json
import re
import html
import time
import urllib.request
import urllib.parse
from pathlib import Path

CANONICAL_ID = 54069253
SE_BASE = "https://api.stackexchange.com/2.3"
ROOT = Path(__file__).resolve().parents[3]
DUP_PAIRS = ROOT / "probe/2026-05-25-canonical-grouping-density/data/so_dup_pairs.json"
OUT = Path(__file__).resolve().parent / "data" / f"canonical_{CANONICAL_ID}.json"


def load_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SE_API_KEY"):
            return line.split("=", 1)[1].strip()
    raise SystemExit("SE_API_KEY not found in .env")


def clean(body_html: str) -> str:
    """HTML -> text, preserving code as fenced/inline markdown so code-as-evidence survives."""
    s = re.sub(r"<pre[^>]*>\s*<code[^>]*>([\s\S]*?)</code>\s*</pre>",
               lambda m: "\n```\n" + html.unescape(m.group(1)) + "\n```\n", body_html)
    s = re.sub(r"<code[^>]*>([\s\S]*?)</code>", lambda m: "`" + html.unescape(m.group(1)) + "`", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def get(path: str, params: dict, key: str) -> dict:
    params = {**params, "site": "stackoverflow", "key": key}
    url = f"{SE_BASE}{path}?{urllib.parse.urlencode(params)}"
    data = json.load(urllib.request.urlopen(url))
    if data.get("backoff"):
        time.sleep(int(data["backoff"]) + 1)
    return data


def main() -> None:
    key = load_key()
    q = get(f"/questions/{CANONICAL_ID}", {"filter": "withbody"}, key)["items"][0]
    ans = get(f"/questions/{CANONICAL_ID}/answers",
              {"filter": "withbody", "sort": "votes", "order": "desc", "pagesize": 100}, key)

    pairs = json.loads(DUP_PAIRS.read_text())
    dup_ids = sorted({d for d, t in pairs if t == CANONICAL_ID})

    def norm(item: dict) -> dict:
        owner = item.get("owner", {}) or {}
        return {
            "answer_id": item["answer_id"],
            "question_id": CANONICAL_ID,
            "score": item.get("score"),
            "is_accepted": int(bool(item.get("is_accepted"))),
            "creation_date": item.get("creation_date"),
            "owner_user_id": owner.get("user_id"),
            "owner_display_name": owner.get("display_name"),
            "body_html": item.get("body", ""),
            "body_text": clean(item.get("body", "")),
        }

    fixture = {
        "_meta": {
            "canonical_id": CANONICAL_ID,
            "note": "out-of-window (2019) canonical pulled probe-local; dups live in main DB",
            "source": "StackExchange API 2.3, CC-BY-SA",
        },
        "canonical_question": {
            "question_id": q["question_id"],
            "title": q["title"],
            "score": q.get("score"),
            "answer_count": q.get("answer_count"),
            "creation_date": q.get("creation_date"),
            "tags": q.get("tags", []),
            "link": q.get("link"),
            "body_html": q.get("body", ""),
            "body_text": clean(q.get("body", "")),
        },
        "canonical_answers": [norm(a) for a in ans["items"]],
        "dup_question_ids": dup_ids,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
    print(f"quota_remaining={ans.get('quota_remaining')}")
    print(f"canonical answers fetched: {len(fixture['canonical_answers'])}")
    print(f"dup_question_ids stored: {len(dup_ids)}")
    print(f"written: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
