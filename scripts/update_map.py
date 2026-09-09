#!/usr/bin/env python3
"""
Weekly citation map updater.

Division of labour, deliberately:
  - Citation edges come from the Semantic Scholar API. They are facts and are
    not guessed. Every edge this script writes is marked "v" (verified).
  - Cluster assignment and the one-paragraph note come from Claude, which is
    judgement and is marked as such in the digest.

The script never invents an edge and never invents an author list.
"""

import json, os, re, sys, time, datetime, urllib.request, urllib.parse, urllib.error

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP      = os.path.join(ROOT, "data", "map.json")
REJECTS  = os.path.join(ROOT, "data", "rejects.json")
DIGESTS  = os.path.join(ROOT, "digests")

S2       = "https://api.semanticscholar.org/graph/v1"
S2_KEY   = os.environ.get("S2_API_KEY")           # optional, raises rate limits

# Two ways to reach a model, because a claude.ai Enterprise seat is not an
# Anthropic Console account and does not come with an API key. Either secret
# works; a direct Anthropic key wins when both are set because it is one hop
# fewer. With neither, the run still writes verified edges and says in the
# digest that judgement was skipped.
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY")
BRAINTRUST_KEY = os.environ.get("BRAINTRUST_API_KEY")
GATEWAY  = os.environ.get("BRAINTRUST_GATEWAY", "https://gateway.braintrust.dev/v1")
MODEL    = os.environ.get("MAP_MODEL", "claude-sonnet-5")

MAX_ADDITIONS   = 8
FIELDS = "paperId,title,year,authors,venue,externalIds,citationCount,abstract"


# ---------------------------------------------------------------- http helpers

def get(url, tries=4):
    headers = {"User-Agent": "evals-research-repo/1.0"}
    if S2_KEY:
        headers["x-api-key"] = S2_KEY
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and i < tries - 1:
                time.sleep(4 * (i + 1))       # S2 is aggressively rate limited
                continue
            raise
        except Exception:
            if i < tries - 1:
                time.sleep(3)
                continue
            raise
    return None


def load(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


# ------------------------------------------------------- resolve nodes to S2 ids

def resolve(nodes):
    """Attach a Semantic Scholar paperId to each node, caching it in map.json."""
    resolved = 0
    for n in nodes:
        if n.get("s2") or n.get("s2") is False:
            continue
        pid = None
        # prefer an explicit arXiv id from the stored url
        m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", n.get("url", ""))
        if m:
            try:
                d = get(f"{S2}/paper/arXiv:{m.group(1)}?fields=paperId")
                pid = d and d.get("paperId")
            except Exception:
                pid = None
        if not pid:                                   # fall back to title search
            try:
                q = urllib.parse.quote(n["t"])
                d = get(f"{S2}/paper/search?query={q}&limit=1&fields=paperId,title")
                hits = (d or {}).get("data") or []
                if hits and _same_title(hits[0].get("title", ""), n["t"]):
                    pid = hits[0]["paperId"]
            except Exception:
                pid = None
        n["s2"] = pid or False        # False = unresolvable, don't retry weekly
        if pid:
            resolved += 1
        time.sleep(1.1)
    return resolved


def _same_title(a, b):
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    a, b = norm(a), norm(b)
    return a and (a in b or b in a or a[:45] == b[:45])


# ---------------------------------------------------------------- find candidates

def candidates(nodes, since_year):
    """Papers citing >=1 map node, with a count of how many nodes they cite."""
    ids = {n["s2"]: n for n in nodes if n.get("s2")}
    found = {}
    for pid, node in ids.items():
        try:
            d = get(f"{S2}/paper/{pid}/citations?fields={FIELDS}&limit=100")
        except Exception as e:
            print(f"  ! citations failed for {node['id']}: {e}", file=sys.stderr)
            continue
        for row in (d or {}).get("data", []):
            p = row.get("citingPaper") or {}
            if not p.get("paperId") or (p.get("year") or 0) < since_year:
                continue
            if p["paperId"] in ids:            # already on the map
                continue
            rec = found.setdefault(p["paperId"], {"paper": p, "cites": []})
            rec["cites"].append(node["id"])
        time.sleep(1.1)
    return found


def qualifies(rec, nodes_by_id):
    """The inclusion rule. Deterministic, so it can be audited."""
    cites = set(rec["cites"])
    p = rec["paper"]
    if len(cites) >= 2:
        return True, f"cites {len(cites)} map papers"
    title = (p.get("title") or "").lower()
    if any(w in title for w in ("survey", "systematic review", "position",
                                "benchmark", "bench:", "a review")):
        return True, "review/position/benchmark — becomes a hub"
    age = max(1, datetime.date.today().year - (p.get("year") or datetime.date.today().year))
    if (p.get("citationCount") or 0) / age >= 25:
        return True, f"{p.get('citationCount')} citations in ~{age}y"
    return False, f"cites only {', '.join(cites)}; no velocity signal"


def crosses_clusters(cites, nodes_by_id):
    return len({nodes_by_id[c]["c"] for c in cites if c in nodes_by_id}) >= 2


# ------------------------------------------------------------------ Claude calls

def have_model():
    return bool(ANTHROPIC_KEY or BRAINTRUST_KEY)


def _post(url, body, headers, tries=3):
    """POST JSON with the same patience as get(). A transient 429 on a weekly
    job should cost a retry, not a whole run's worth of notes."""
    data = json.dumps(body).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            print(f"  ! model call {e.code}: {detail}", file=sys.stderr)
            return None
        except Exception as e:
            if i < tries - 1:
                time.sleep(4)
                continue
            print(f"  ! model call failed: {e}", file=sys.stderr)
            return None
    return None


def claude(prompt, max_tokens=1400):
    """Ask for judgement. Same job either way, two different transports."""
    if ANTHROPIC_KEY:
        d = _post("https://api.anthropic.com/v1/messages",
                  {"model": MODEL, "max_tokens": max_tokens,
                   "messages": [{"role": "user", "content": prompt}]},
                  {"content-type": "application/json",
                   "x-api-key": ANTHROPIC_KEY,
                   "anthropic-version": "2023-06-01"})
        if not d:
            return None
        return "".join(b.get("text", "") for b in d.get("content", [])
                       if b.get("type") == "text")

    if BRAINTRUST_KEY:
        # The gateway is OpenAI-shaped and takes a Braintrust key, so it needs
        # Anthropic connected as a provider in the Braintrust org. Model names
        # are the provider's own; override with MAP_MODEL if the org pins one.
        d = _post(f"{GATEWAY}/chat/completions",
                  {"model": MODEL, "max_tokens": max_tokens,
                   "messages": [{"role": "user", "content": prompt}]},
                  {"content-type": "application/json",
                   "authorization": f"Bearer {BRAINTRUST_KEY}"})
        if not d:
            return None
        choices = d.get("choices") or []
        return choices[0].get("message", {}).get("content", "") if choices else None

    return None


def describe(batch, clusters, nodes_by_id):
    """Ask for cluster + note only. Never for citations or authors."""
    listing = "\n\n".join(
        f"[{i}] {r['paper'].get('title')} ({r['paper'].get('year')})\n"
        f"cites on-map: {', '.join(sorted(set(r['cites'])))}\n"
        f"abstract: {(r['paper'].get('abstract') or '')[:900]}"
        for i, r in enumerate(batch))
    keys = ", ".join(f"{k} ({v['name']})" for k, v in clusters.items())
    prompt = f"""These papers are being added to a citation map. For each, assign one cluster and write a note.

Clusters: {keys}

For each paper return JSON only, no prose, no markdown fences:
[{{"i": 0, "c": "<cluster key>", "n": "<two sentences: what it does, and why it matters given what it cites>"}}]

The note is read by one researcher tracking these four literatures. Be specific and concrete about the finding. Do not pad, do not hedge, do not restate the title. If the abstract is too thin to say something real, write "abstract too thin to summarise".

{listing}"""
    txt = claude(prompt)
    if not txt:
        return {}
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    try:
        return {d["i"]: d for d in json.loads(txt)}
    except Exception as e:
        print(f"  ! could not parse model output: {e}", file=sys.stderr)
        return {}


# ------------------------------------------------------------------------ main

def main():
    m = load(MAP, None)
    if not m:
        sys.exit("data/map.json missing")
    rejects = load(REJECTS, {})
    nodes_by_id = {n["id"]: n for n in m["nodes"]}
    today = datetime.date.today().isoformat()

    print("resolving nodes to Semantic Scholar ids…")
    print(f"  resolved {resolve(m['nodes'])} new")

    print("collecting citing papers…")
    found = candidates(m["nodes"], since_year=datetime.date.today().year - 1)
    print(f"  {len(found)} distinct citing papers")

    keep, dropped = [], 0
    for pid, rec in found.items():
        if pid in rejects:
            continue
        ok, why = qualifies(rec, nodes_by_id)
        if ok:
            rec["why"] = why
            keep.append(rec)
        else:
            rejects[pid] = {"title": rec["paper"].get("title"), "why": why, "seen": today}

    # bridges first, then breadth of attachment
    keep.sort(key=lambda r: (crosses_clusters(r["cites"], nodes_by_id),
                             len(set(r["cites"]))), reverse=True)
    if len(keep) > MAX_ADDITIONS:
        for r in keep[MAX_ADDITIONS:]:
            pid = r["paper"]["paperId"]
            rejects[pid] = {"title": r["paper"].get("title"),
                            "why": "over per-run cap, reconsider next week", "seen": today}
        dropped = len(keep) - MAX_ADDITIONS
        keep = keep[:MAX_ADDITIONS]

    notes = describe(keep, m["clusters"], nodes_by_id) if keep else {}

    added = []
    for i, rec in enumerate(keep):
        p = rec["paper"]
        info = notes.get(i, {})
        authors = [a.get("name") for a in (p.get("authors") or []) if a.get("name")]
        first = (authors[0].split()[-1].lower() if authors else "anon")
        nid = re.sub(r"[^a-z0-9]", "", first) + str(p.get("year") or "")
        while nid in nodes_by_id:
            nid += "b"
        ext = p.get("externalIds") or {}
        url = (f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv")
               else f"https://doi.org/{ext['DOI']}" if ext.get("DOI")
               else f"https://www.semanticscholar.org/paper/{p['paperId']}")
        node = {
            "id": nid, "t": p.get("title") or "untitled",
            "a": ", ".join(authors[:12]) if authors else "authors not verified",
            "y": p.get("year") or 0,
            "v": p.get("venue") or "preprint",
            "c": info.get("c") if info.get("c") in m["clusters"] else rec["cites"] and nodes_by_id[rec["cites"][0]]["c"],
            "url": url,
            "n": info.get("n") or "No summary generated this run.",
            "s2": p["paperId"], "isNew": True, "added": today,
        }
        m["nodes"].append(node)
        nodes_by_id[nid] = node
        for target in sorted(set(rec["cites"])):
            m["edges"].append({"source": nid, "target": target, "confidence": "v"})
        rec["node"] = node
        added.append(rec)

    # retire the "new" ring after three weeks
    cutoff = (datetime.date.today() - datetime.timedelta(days=21)).isoformat()
    for n in m["nodes"]:
        if n.get("isNew") and n.get("added", "9999") < cutoff:
            n["isNew"] = False

    # integrity gate — never commit a broken graph
    ids = {n["id"] for n in m["nodes"]}
    bad = [e for e in m["edges"] if e["source"] not in ids or e["target"] not in ids]
    if bad:
        sys.exit(f"refusing to write: {len(bad)} dangling edges")
    if len(ids) != len(m["nodes"]):
        sys.exit("refusing to write: duplicate node ids")

    m["updated"] = today
    with open(MAP, "w") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
    with open(REJECTS, "w") as f:
        json.dump(rejects, f, indent=2, ensure_ascii=False)

    write_digest(added, dropped, len(found), today, nodes_by_id)
    print(f"added {len(added)}, rejected {len(rejects)} cumulative, held back {dropped}")


def write_digest(added, dropped, screened, today, nodes_by_id):
    os.makedirs(DIGESTS, exist_ok=True)
    bridges = [r for r in added if crosses_clusters(r["cites"], nodes_by_id)]
    routine = [r for r in added if r not in bridges]

    L = [f"# Citation map — {today}", ""]
    if not added:
        L += [f"Nothing qualified. Screened {screened} citing papers.", ""]
    if bridges:
        L += ["## Bridges", ""]
        for r in bridges:
            n = r["node"]
            cl = sorted({nodes_by_id[c]["c"] for c in r["cites"] if c in nodes_by_id})
            L += [f"**{n['t']}** ({n['y']}) — {n['a']}",
                  f"[source]({n['url']}) · joins {' + '.join(cl)} · {r['why']}",
                  f"Cites on-map: {', '.join(sorted(set(r['cites'])))}", "", n["n"], ""]
    if routine:
        L += ["## Routine additions", ""]
        for r in routine:
            n = r["node"]
            L += [f"- **{n['t']}** ({n['y']}) — [source]({n['url']}) — attaches to "
                  f"{', '.join(sorted(set(r['cites'])))} — {r['why']}"]
        L += [""]
    L += ["---", f"Screened {screened} citing papers. Added {len(added)}."
          + (f" Held back {dropped} over the per-run cap." if dropped else "")]
    if not have_model():
        L += ["", "_Neither BRAINTRUST_API_KEY nor ANTHROPIC_API_KEY set: "
                  "notes and cluster assignments were skipped. Edges above are "
                  "still verified citations._"]

    with open(os.path.join(DIGESTS, f"{today}.md"), "w") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
