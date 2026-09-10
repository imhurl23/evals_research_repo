#!/usr/bin/env python3
"""
Daily frontier watch: new models, new eval harness releases, new benchmark
papers, new eval repos.

Same division of labour as the citation map. Every fact here — version
numbers, upload dates, star counts, arXiv ids — comes from an API and is
reproducible. The model only decides what is worth your attention and writes
one line saying why. It is never asked whether something was released.

Four sources, all structured, no scraping:
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

State lives in data/frontier_seen.json so a thing is reported once. Items the
triage rejected are recorded too, so they are not re-judged every morning.
"""

import json, os, sys, time, datetime, urllib.request, urllib.parse, urllib.error
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

ARXIV_TERMS = ['abs:"evaluation harness"', 'abs:"new benchmark"',
               'abs:"agent benchmark"', 'abs:"benchmark for evaluating"',
               'abs:"evaluation framework"', 'abs:"we introduce a benchmark"']

REPO_QUERY = "eval OR benchmark OR harness OR evals in:name,description"


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
    """arXiv only answers over https; the http endpoint returns an empty feed."""
    terms = " OR ".join(ARXIV_TERMS)
    q = f"(cat:cs.CL OR cat:cs.AI OR cat:cs.LG) AND ({terms})"
    url = ("https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q) +
           "&sortBy=submittedDate&sortOrder=descending&max_results=40")
    raw = get(url, parse="raw")
    if not raw:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"  ! arxiv parse failed: {e}", file=sys.stderr)
        return []
    for e in root.findall("a:entry", ns):
        pub = (e.findtext("a:published", "", ns) or "")
        if not recent(pub, days):
            continue
        aid = (e.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
        out.append({
            "key": "arxiv:" + aid, "kind": "paper",
            "title": " ".join((e.findtext("a:title", "", ns) or "").split()),
            "url": "https://arxiv.org/abs/" + aid,
            "when": pub[:10],
            "detail": " ".join((e.findtext("a:summary", "", ns) or "").split())[:420],
        })
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
             "paper": "Benchmarks & methods", "repo": "New eval repos"}
KIND_ORDER = ["model", "harness", "paper", "repo"]


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


def write_digest(kept, dropped, screened, today):
    os.makedirs(DIGESTS, exist_ok=True)
    L = [f"# Frontier watch — {today}", ""]
    for kind in KIND_ORDER:
        rows = [k for k in kept if k["kind"] == kind]
        if not rows:
            continue
        L += [f"## {KIND_NAME[kind]}", ""]
        for r in rows:
            L += [f"- **[{r['title']}]({r['url']})** · {r['when']}", f"  {r['why']}"]
        L += [""]
    L += ["---",
          f"Screened {screened} items across four sources; {len(kept)} worth a look, "
          f"{dropped} filtered out."]
    if not have_model():
        L += ["", "_No model key set: everything new is listed untriaged._"]
    path = os.path.join(DIGESTS, f"frontier-{today}.md")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
    return path


def main():
    today = datetime.date.today().isoformat()
    seen = load(SEEN, {})

    print("collecting…")
    items = []
    for name, fn in (("hugging face", hf_models), ("pypi", pypi_releases),
                     ("github releases", gh_releases), ("arxiv", arxiv_papers),
                     ("github search", trending_repos)):
        got = fn(WINDOW)
        print(f"  {name}: {len(got)}")
        items += got

    # one key, one report, ever
    uniq = {}
    for it in items:
        uniq.setdefault(it["key"], it)
    fresh = [it for k, it in uniq.items() if k not in seen]
    print(f"  {len(uniq)} distinct, {len(fresh)} not seen before")

    if not fresh:
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

    if not kept:
        print(f"screened {len(fresh)}, nothing worth reporting")
        return 0

    path = write_digest(kept, dropped, len(fresh), today)
    print(f"wrote {path}: {len(kept)} reported, {dropped} filtered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
