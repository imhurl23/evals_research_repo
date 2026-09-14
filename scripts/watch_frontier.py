#!/usr/bin/env python3
"""
Daily frontier watch: new models, new eval harness releases, new benchmark
papers, new eval repos.

Same division of labour as the citation map. Every fact here — version
numbers, upload dates, star counts, arXiv ids — comes from an API and is
reproducible. The model only decides what is worth your attention and writes
one line saying why. It is never asked whether something was released.

Sources, all structured, no scraping:
  hugging face   new model repos from labs that ship weights. Model cards
                 routinely appear before the announcement, which is the
                 earliest public signal there is.
  pypi + github  harness releases. PyPI is primary because inspect_ai — one
                 of the most active harnesses — publishes no GitHub releases
                 at all, so a releases-only watch would never see it.
  arxiv          new benchmark and harness papers, which land before the code
                 and long before anyone cites them.
  github search  recently created, fast-growing eval repos, for harnesses
                 nobody has told you about yet.
  reddit + hn    launch chatter, which often runs ahead of any of the above.
                 Reddit needs a free app id/secret and is skipped until those
                 exist. X is absent on purpose: it has no free read tier, and
                 a source that can only ever return nothing is worse than an
                 honest gap.

State lives in data/frontier_seen.json so a thing is reported once. Items the
triage rejected are recorded too, so they are not re-judged every morning.
"""

import base64, json, os, re, sys, time, datetime, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import have_model, claude, json_array

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEN    = os.path.join(ROOT, "data", "frontier_seen.json")
DIGESTS = os.path.join(ROOT, "digests")

GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
WINDOW   = int(os.environ.get("WATCH_WINDOW_DAYS", "3"))   # overlap; seen[] dedupes
FORGET   = 120        # days before a seen key is pruned
MAX_REPORT = 25       # never open an unreadable issue

# Labs that publish weights. Anthropic and OpenAI mostly do not, so their new
# models surface here through arXiv and the harness/repo sources instead.
HF_ORGS = ["meta-llama", "google", "Qwen", "deepseek-ai", "mistralai",
           "allenai", "microsoft", "nvidia", "openai", "HuggingFaceTB",
           "ai21labs", "CohereLabs", "moonshotai", "zai-org"]

PYPI_PKGS = ["inspect-ai", "lm-eval", "lighteval", "crfm-helm", "evalplus",
             "deepeval", "ragas", "promptfoo", "openai-evals"]

GH_RELEASE_REPOS = ["EleutherAI/lm-evaluation-harness", "stanford-crfm/helm",
                    "huggingface/lighteval", "openai/simple-evals",
                    "UKGovernmentBEIS/inspect_ai", "METR/vivaria",
                    "allenai/olmes"]

ARXIV_CATS  = ["cs.CL", "cs.AI", "cs.LG"]
# Matched against title + abstract of each day's new submissions. Plain
# substrings, not API query syntax: we filter the feed ourselves now.
# Deliberately specific. Bare "benchmark", "we evaluate" and
# "interpretability" matched 191 papers in a single day — nearly every ML
# preprint says one of them — which is triage cost, not signal.
ARXIV_TERMS = ["evaluation harness", "eval harness", "evaluation framework",
               "evaluation suite", "evaluation protocol", "leaderboard",
               "new benchmark", "we introduce a benchmark",
               "we present a benchmark", "benchmark for evaluating",
               "benchmark suite", "reward hacking", "reward model",
               "sparse autoencoder", "llm-as-a-judge", "llm as a judge",
               "mechanistic interpretability", "agent benchmark",
               "evaluating agents", "contamination"]

REPO_QUERY = "eval OR benchmark OR harness OR evals in:name,description"

# --- chatter -----------------------------------------------------------------
# X is deliberately absent: it has no free read tier, so wiring it would mean
# a source that always returns nothing. Reddit and HN cover much of the same
# launch chatter, and both say so when they fail.
REDDIT_ID     = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")
# Reddit throttles generic agents; their convention is platform:id:version.
REDDIT_UA = os.environ.get(
    "REDDIT_USER_AGENT",
    "github-actions:evals-research-repo-frontier:1.1 (+https://github.com/imhurl23/evals_research_repo)")
SUBS = ["LocalLLaMA", "MachineLearning"]
REDDIT_MIN_SCORE = 25

HN_MIN_POINTS = 25
# Must name something in this field. The first pass used "release", "launch"
# and "open source" as triggers and surfaced a GCC point release and a news
# story about a police crackdown — generic verbs are not a topic filter.
CHATTER_TERMS = ["llm", "language model", "gpt-", "gpt4", "gpt5", "claude",
                 "gemini", "llama", "qwen", "deepseek", "mistral", "grok",
                 "open-weight", "open weights", "model weights", "benchmark",
                 "eval harness", "evals", "leaderboard", "rlhf", "rlvr",
                 "reward model", "reward hack", "fine-tun", "interpretability",
                 "sparse autoencoder", "mixture-of-experts", "anthropic",
                 "openai", "hugging face", "inference engine", "agentic",
                 "frontier model", "foundation model", "multimodal"]


class SourceDown(Exception):
    """A source could not be reached. Raised rather than returning [] so a
    dead source is never indistinguishable from a quiet one — arXiv failed
    silently on four consecutive scheduled runs before this existed."""


# ----------------------------------------------------------------- http helpers

def get(url, headers=None, tries=3, parse="json"):
    h = {"User-Agent": "evals-research-repo-frontier/1.0"}
    h.update(headers or {})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
            return json.loads(raw) if parse == "json" else raw
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None                       # a missing package is not an error
            if e.code in (403, 429, 500, 502, 503) and i < tries - 1:
                time.sleep(4 * (i + 1))
                continue
            print(f"  ! {e.code} {url[:90]}", file=sys.stderr)
            return None
        except Exception as e:
            if i < tries - 1:
                time.sleep(3)
                continue
            print(f"  ! {type(e).__name__} {url[:90]}", file=sys.stderr)
            return None
    return None


def gh(url):
    h = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    return get(url, h)


def load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default
    return default


def recent(iso, days):
    if not iso:
        return False
    try:
        d = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except Exception:
        return False
    return (datetime.date.today() - d).days <= days


# ---------------------------------------------------------------------- sources

def hf_models(days):
    out = []
    for org in HF_ORGS:
        url = ("https://huggingface.co/api/models?author=" + urllib.parse.quote(org) +
               "&sort=createdAt&direction=-1&limit=20")
        for m in (get(url) or []):
            mid = m.get("modelId") or m.get("id")
            if not mid or m.get("private") or not recent(m.get("createdAt"), days):
                continue
            out.append({
                "key": "hf:" + mid, "kind": "model", "title": mid,
                "url": "https://huggingface.co/" + mid,
                "when": (m.get("createdAt") or "")[:10],
                "detail": f"{m.get('pipeline_tag') or 'model'}, "
                          f"{m.get('downloads', 0)} downloads, {m.get('likes', 0)} likes; "
                          f"tags: {', '.join((m.get('tags') or [])[:8])}",
            })
        time.sleep(0.4)
    return out


def pypi_releases(days):
    out = []
    for pkg in PYPI_PKGS:
        d = get(f"https://pypi.org/pypi/{pkg}/json")
        if not d:
            continue
        info = d.get("info") or {}
        ver = info.get("version")
        files = (d.get("releases") or {}).get(ver) or []
        when = (files[0].get("upload_time_iso_8601") if files else None)
        if not ver or not recent(when, days):
            continue
        out.append({
            "key": f"pypi:{pkg}:{ver}", "kind": "harness", "title": f"{pkg} {ver}",
            "url": f"https://pypi.org/project/{pkg}/{ver}/",
            "when": (when or "")[:10],
            "detail": (info.get("summary") or "")[:200],
        })
        time.sleep(0.2)
    return out


def gh_releases(days):
    out = []
    for repo in GH_RELEASE_REPOS:
        for r in (gh(f"https://api.github.com/repos/{repo}/releases?per_page=3") or []):
            if r.get("draft") or not recent(r.get("published_at"), days):
                continue
            out.append({
                "key": f"ghrel:{repo}:{r.get('tag_name')}", "kind": "harness",
                "title": f"{repo} {r.get('tag_name')}",
                "url": r.get("html_url") or f"https://github.com/{repo}/releases",
                "when": (r.get("published_at") or "")[:10],
                "detail": " ".join((r.get("body") or "").split())[:280],
            })
        time.sleep(0.2)
    return out


def arxiv_papers(days):
    """arXiv's daily RSS, not its search API.

    The search API 429s and times out from GitHub's shared runner IPs — it
    failed on every scheduled run while working fine from a laptop, and
    returned nothing rather than an error, so the watch silently lost a
    source. The RSS feeds are static files on a CDN, and for a daily job they
    are also the better semantics: they *are* the new-submissions list.
    """
    out, seen_ids = [], set()
    for cat in ARXIV_CATS:
        raw = get(f"https://rss.arxiv.org/rss/{cat}", parse="raw")
        if raw is None:
            raise SourceDown(f"arxiv rss {cat} unreachable")
        try:
            root = ET.fromstring(raw)
        except Exception as e:
            raise SourceDown(f"arxiv rss {cat} unparsable: {e}")
        for it in root.findall(".//item"):
            # 'replace' is a revision of an existing paper, not news
            if (it.findtext("announce_type") or "").strip() == "replace":
                continue
            link = (it.findtext("link") or "").strip()
            aid = link.rsplit("/", 1)[-1]
            if not aid or aid in seen_ids:
                continue
            title = " ".join((it.findtext("title") or "").split())
            desc = " ".join((it.findtext("description") or "").split())
            hay = (title + " " + desc).lower()
            if not any(t in hay for t in ARXIV_TERMS):
                continue
            seen_ids.add(aid)
            out.append({
                "key": "arxiv:" + aid, "kind": "paper", "title": title,
                "url": link or ("https://arxiv.org/abs/" + aid),
                "when": datetime.date.today().isoformat(),
                "detail": re.sub(r"^arXiv:\S+\s+Announce Type:\s*\w+\s*", "", desc)[:420],
            })
        time.sleep(1.0)      # arXiv asks for a gap between requests
    return out


def trending_repos(days):
    since = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    q = f"{REPO_QUERY} created:>{since} stars:>40"
    url = ("https://api.github.com/search/repositories?q=" + urllib.parse.quote(q) +
           "&sort=stars&order=desc&per_page=20")
    d = gh(url) or {}
    out = []
    for r in d.get("items", []):
        # new to *us*: created a while ago is fine, we just have not seen it
        out.append({
            "key": "repo:" + r["full_name"], "kind": "repo",
            "title": f"{r['full_name']} ({r.get('stargazers_count', 0)}★)",
            "url": r.get("html_url"),
            "when": (r.get("created_at") or "")[:10],
            "detail": (r.get("description") or "")[:220],
        })
    return out


def _reddit_token():
    """App-only OAuth. client_credentials works for confidential clients, so
    this needs the app's id and secret and never a Reddit password."""
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    basic = base64.b64encode(f"{REDDIT_ID}:{REDDIT_SECRET}".encode()).decode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token", data=body,
        headers={"Authorization": "Basic " + basic, "User-Agent": REDDIT_UA,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("access_token")
    except urllib.error.HTTPError as e:
        raise SourceDown(f"reddit auth {e.code} — check the app id/secret")
    except Exception as e:
        raise SourceDown(f"reddit auth {type(e).__name__}")


def reddit_posts(days):
    if not (REDDIT_ID and REDDIT_SECRET):
        # Not configured is a choice, not a failure; don't cry wolf daily.
        print("  reddit: skipped (REDDIT_CLIENT_ID/SECRET not set)")
        return []
    tok = _reddit_token()
    if not tok:
        raise SourceDown("reddit auth returned no token")
    out = []
    for sub in SUBS:
        d = get(f"https://oauth.reddit.com/r/{sub}/new?limit=50",
                {"Authorization": "Bearer " + tok, "User-Agent": REDDIT_UA})
        if d is None:
            raise SourceDown(f"reddit r/{sub} unreachable")
        for ch in (d.get("data") or {}).get("children", []):
            p = ch.get("data") or {}
            if p.get("stickied") or (p.get("score") or 0) < REDDIT_MIN_SCORE:
                continue
            created = datetime.datetime.utcfromtimestamp(
                p.get("created_utc") or 0).date()
            if (datetime.date.today() - created).days > days:
                continue
            title = p.get("title") or ""
            if not any(t in title.lower() for t in CHATTER_TERMS):
                continue
            out.append({
                "key": "reddit:" + (p.get("id") or title[:40]), "kind": "chatter",
                "title": f"r/{sub}: {title}",
                "url": "https://reddit.com" + (p.get("permalink") or ""),
                "when": created.isoformat(),
                "detail": f"{p.get('score')} points, {p.get('num_comments')} comments. "
                          + " ".join((p.get("selftext") or "").split())[:300],
            })
        time.sleep(1.0)
    return out


def hn_posts(days):
    """Hacker News via Algolia: free, no key, and reliable from CI."""
    since = int(time.time()) - days * 86400
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
           f"&numericFilters=created_at_i>{since},points>{HN_MIN_POINTS}"
           "&hitsPerPage=100")
    d = get(url)
    if d is None:
        raise SourceDown("hacker news unreachable")
    out = []
    for h in d.get("hits", []):
        title = h.get("title") or ""
        if not any(t in title.lower() for t in CHATTER_TERMS):
            continue
        oid = h.get("objectID")
        out.append({
            "key": "hn:" + str(oid), "kind": "chatter", "title": "HN: " + title,
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            "when": (h.get("created_at") or "")[:10],
            "detail": f"{h.get('points')} points, {h.get('num_comments')} comments. "
                      f"discussion: https://news.ycombinator.com/item?id={oid}",
        })
    return out


# ----------------------------------------------------------------------- triage

TRIAGE_CHUNK = 8


def triage(items):
    """Keep/drop plus one line of why. Facts are already fixed; this only ranks."""
    if not have_model():
        for it in items:
            it["keep"], it["why"] = True, "no model configured — not triaged"
        return items

    for start in range(0, len(items), TRIAGE_CHUNK):
        chunk = items[start:start + TRIAGE_CHUNK]
        idx = list(range(start, start + len(chunk)))
        got = _triage_chunk(items, idx)
        if got is None and len(idx) > 1:          # same salvage as the map job
            got = {}
            for i in idx:
                one = _triage_chunk(items, [i])
                if one:
                    got.update(one)
        for i in idx:
            v = (got or {}).get(i) or {}
            items[i]["keep"] = bool(v.get("keep", True))
            items[i]["why"] = v.get("why") or "not judged this run — shown by default"
    return items


def _triage_chunk(items, idx):
    listing = "\n\n".join(
        f"[{i}] kind={items[i]['kind']} | {items[i]['title']} | {items[i]['when']}\n"
        f"{items[i]['detail']}" for i in idx)
    prompt = f"""You watch for developments a researcher tracking LLM evaluation, mechanistic interpretability, RL/reward hacking, and human-AI interaction would want to know about the same day.

Keep an item if it is a genuinely new frontier model, a real eval harness release, a benchmark or evaluation method that could be adopted, or a new eval tool with traction. Drop routine quantisations, format conversions, finetunes of existing models, non-English-market repackaging, awesome-lists, unrelated domain benchmarks, and patch releases with no new capability.

Return JSON only, no prose, no markdown fences, reusing the bracketed index as "i":
[{{"i": {idx[0]}, "keep": true, "why": "<one specific sentence: what it is and why it matters to this researcher>"}}]

Be concrete and do not restate the title. If you drop something, "why" should say briefly what it actually is.

{listing}"""
    arr = json_array(claude(prompt, max_tokens=1600))
    if arr is None:
        print(f"  ! unparsable triage output for {idx}", file=sys.stderr)
        return None
    return {d["i"]: d for d in arr if isinstance(d, dict) and isinstance(d.get("i"), int)}


# ------------------------------------------------------------------------ output

KIND_NAME = {"model": "Models", "harness": "Harness releases",
             "paper": "Benchmarks & methods", "repo": "New eval repos",
             "chatter": "Chatter (Reddit & HN)"}
KIND_ORDER = ["model", "harness", "chatter", "paper", "repo"]


def allocate(kept, cap):
    """Round-robin across kinds. arXiv alone returned 60 of 103 items in
    testing, which would bury a model launch under a busy week of preprints;
    every source gets slots before any source gets seconds."""
    by = {k: [i for i in kept if i["kind"] == k] for k in KIND_ORDER}
    out = []
    while len(out) < cap and any(by.values()):
        for k in KIND_ORDER:
            if by[k] and len(out) < cap:
                out.append(by[k].pop(0))
    return out


def write_digest(kept, dropped, screened, today, down=()):
    os.makedirs(DIGESTS, exist_ok=True)
    L = [f"# Frontier watch — {today}", ""]
    if down:
        L += ["> **Sources unavailable this run — coverage is incomplete:** "
              + "; ".join(down), ""]
    for kind in KIND_ORDER:
        rows = [k for k in kept if k["kind"] == kind]
        if not rows:
            continue
        L += [f"## {KIND_NAME[kind]}", ""]
        for r in rows:
            L += [f"- **[{r['title']}]({r['url']})** · {r['when']}", f"  {r['why']}"]
        L += [""]
    L += ["---",
          f"Screened {screened} new items; {len(kept)} worth a look, "
          f"{dropped} filtered out."]
    if not have_model():
        L += ["", "_No model key set: everything new is listed untriaged._"]
    path = os.path.join(DIGESTS, f"frontier-{today}.md")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
    return path


FEED       = os.path.join(ROOT, "data", "frontier.json")
FEED_DAYS  = 90        # how much history the page shows
FEED_MAX   = 400       # and a hard ceiling, so the fetch stays small


def update_feed(kept, today, down):
    """Append this run to data/frontier.json for frontier.html.

    Same split as the map: the job writes JSON, the page renders it. Nothing
    here is the source of truth — the digests and issues are — but a static
    page cannot read those, and markdown served raw is not a reading
    experience.
    """
    feed = load(FEED, {"runs": []})
    runs = [r for r in feed.get("runs", []) if r.get("date") != today]
    runs.insert(0, {
        "date": today,
        "down": list(down),
        "items": [{k: it[k] for k in ("kind", "title", "url", "when", "why")}
                  for it in kept],
    })
    cutoff = (datetime.date.today() - datetime.timedelta(days=FEED_DAYS)).isoformat()
    runs = [r for r in runs if r["date"] >= cutoff]
    total = 0
    trimmed = []
    for r in runs:                       # keep whole days, drop the oldest first
        if total >= FEED_MAX:
            break
        trimmed.append(r)
        total += len(r["items"])
    with open(FEED, "w") as f:
        json.dump({"updated": today, "runs": trimmed}, f, indent=2, ensure_ascii=False)
    print(f"  feed: {len(trimmed)} days, {total} items")


def main():
    today = datetime.date.today().isoformat()
    seen = load(SEEN, {})

    print("collecting…")
    items, down = [], []
    for name, fn in (("hugging face", hf_models), ("pypi", pypi_releases),
                     ("github releases", gh_releases), ("arxiv", arxiv_papers),
                     ("github search", trending_repos), ("reddit", reddit_posts),
                     ("hacker news", hn_posts)):
        try:
            got = fn(WINDOW)
            print(f"  {name}: {len(got)}")
            items += got
        except SourceDown as e:
            down.append(f"{name}: {e}")
            print(f"  {name}: DOWN — {e}", file=sys.stderr)
        except Exception as e:
            down.append(f"{name}: {type(e).__name__}")
            print(f"  {name}: DOWN — {type(e).__name__}: {e}", file=sys.stderr)

    # one key, one report, ever
    uniq = {}
    for it in items:
        uniq.setdefault(it["key"], it)
    fresh = [it for k, it in uniq.items() if k not in seen]
    print(f"  {len(uniq)} distinct, {len(fresh)} not seen before")

    if not fresh:
        # A dead source must still reach you. "Nothing new" and "we couldn't
        # look" are different facts and used to look identical from outside.
        if down:
            print("nothing new, but sources are down — reporting that")
            print("wrote " + write_digest([], 0, 0, today, down))
            return 0
        print("nothing new")
        return 0

    # First run would otherwise dump a 90-day repo backlog and every paper in
    # the window as "new". Record what exists, report nothing, and let
    # tomorrow's run be a clean delta.
    if not seen:
        stamp = {"seen": today, "kept": False}
        with open(SEEN, "w") as f:
            json.dump({it["key"]: stamp for it in uniq.values()}, f,
                      indent=2, sort_keys=True)
        print(f"primed with {len(uniq)} existing items; no issue this run. "
              f"From here on only genuinely new things are reported.")
        return 0

    if len(fresh) > MAX_REPORT * 3:      # a source misbehaving should not spam
        fresh.sort(key=lambda i: i["when"], reverse=True)
        fresh = fresh[:MAX_REPORT * 3]

    fresh = triage(fresh)
    kept = allocate([i for i in fresh if i["keep"]], MAX_REPORT)
    dropped = len(fresh) - len(kept)

    # record everything judged, kept or not, so tomorrow does not re-ask
    for it in fresh:
        seen[it["key"]] = {"seen": today, "kept": bool(it["keep"])}
    cutoff = (datetime.date.today() - datetime.timedelta(days=FORGET)).isoformat()
    seen = {k: v for k, v in seen.items()
            if (v.get("seen", "9999") if isinstance(v, dict) else "9999") >= cutoff}

    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    with open(SEEN, "w") as f:
        json.dump(seen, f, indent=2, sort_keys=True)

    if not kept and not down:
        print(f"screened {len(fresh)}, nothing worth reporting")
        return 0

    path = write_digest(kept, dropped, len(fresh), today, down)
    print(f"wrote {path}: {len(kept)} reported, {dropped} filtered")
    update_feed(kept, today, down)
    return 0


if __name__ == "__main__":
    sys.exit(main())
