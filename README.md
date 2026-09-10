# evals_research_repo

A self-updating citation map of four literatures: AI evaluation methodology, mechanistic interpretability, RL and reward hacking, and human–AI interaction and design.

The map is a real citation graph — nodes are papers, edges are citations — rendered as an interactive page. It grows weekly, and it tells you when a new paper attaches across cluster boundaries, because those are the papers joining the four literatures together.

**Live map:** `https://imhurl23.github.io/evals_research_repo/`

## Layout

```
index.html                     the viewer; fetches data/map.json at load
data/map.json                  the graph. this is the only file you edit by hand
data/rejects.json              papers already screened and declined
data/frontier_seen.json        what the frontier watch has already reported
digests/YYYY-MM-DD.md          what each weekly map run found
digests/frontier-YYYY-MM-DD.md what the daily watch found
snapshots/YYYY-MM-DD.png       rendered image of the map that week
scripts/update_map.py          the weekly map updater
scripts/watch_frontier.py      the daily frontier watch
scripts/llm.py                 model transport, shared by both jobs
.github/workflows/             the two schedules
```

Data is separate from the viewer on purpose. The weekly job edits JSON, never JavaScript, so a bad run produces a readable diff instead of a broken page.

## Setup

1. **Create the repo** as `imhurl23/evals_research_repo` and push these files to `main`.
2. **Enable Pages:** Settings → Pages → Source: *Deploy from a branch* → `main` / `/ (root)`. The map is live in about a minute. Pages only serves a private repo on a paid plan, so on a free plan the repo has to be public.
3. **Add a model secret:** Settings → Secrets and variables → Actions → New repository secret. Set **one** of:
   - `BRAINTRUST_API_KEY` — routes through the [Braintrust gateway](https://braintrust.dev/docs/deploy/gateway), which needs Anthropic connected as a provider in the Braintrust org. Use this when you can't get an Anthropic Console key: a claude.ai Team or Enterprise seat is a different product and doesn't come with one.
   - `ANTHROPIC_API_KEY` — a key from the [Claude Console](https://platform.claude.com). Takes precedence if both are set.

   Without either, the map still updates and every edge is still a verified citation; new papers just arrive with no summary, and cluster assignment falls back to the cluster of the first paper they cite. The digest says so when that happens.
4. **Optional:** add `S2_API_KEY` for a [Semantic Scholar key](https://www.semanticscholar.org/product/api). Unkeyed access is heavily rate limited, and with 39+ nodes to query the first few runs will be slow without one.
5. **Test it:** Actions → Weekly citation map update → Run workflow. Don't wait a week to find out it works.

The schedule is Mondays 13:00 UTC. Change the cron in `.github/workflows/weekly-update.yml`.

## How a paper gets added

Citations come from the Semantic Scholar API, not from a language model. A paper must first clear the **hub gate**, then satisfy one of the three signals.

The hub gate: at least one map paper it cites must not be a *hub* — a paper that at least 20% of the week's screened candidates also cite. TruthfulQA, InstructGPT and DPO are cited by most of the LLM literature, so attaching only through them says nothing about topic. Without this gate, citing two famous papers was enough to join, which admitted an LLM compression survey and a review of maritime emergency response. Hubs are recomputed each run against that run's own pool and printed in the log, so the set moves as the map and the literature move rather than sitting in a hardcoded list.

Having cleared it, a paper is added if it:

- cites **two or more** papers already on the map, or
- is a survey, position paper, or benchmark citing at least one, since those become hubs, or
- cites at least one and has unusual citation velocity for its age.

The gate runs first, so a widely-cited survey that only attaches through hubs is still rejected. This does mean a high-velocity paper hanging off a single hub no longer gets in on velocity alone — deliberate, since topical attachment is what the map is for.

Everything else is logged to `data/rejects.json` with a reason and skipped in future runs. Additions are capped at 8 per week; over the cap, papers with the widest cross-cluster attachment win and the rest are reconsidered next week. An unreadable map has failed at its job.

## Frontier watch

A second, independent job runs daily at 12:00 UTC and opens an issue labelled `frontier-watch` when something new shows up. It is not part of the map and never edits it. Four structured sources, no scraping:

| Source | What it catches |
|---|---|
| Hugging Face model API | new model repos from labs that ship weights — model cards often appear before the announcement |
| PyPI + GitHub Releases | eval harness versions. PyPI is primary: `inspect_ai` publishes no GitHub releases at all, so a releases-only watch would miss one of the most active harnesses |
| arXiv | new benchmark and harness papers, which land before the code and long before anyone cites them |
| GitHub search | recently created, fast-growing eval repos |

Facts come from the APIs. The model only decides what is worth your attention and writes the one-line reason; it is never asked whether something was released. Items are reported once and recorded in `data/frontier_seen.json`, rejections included, so nothing is re-judged every morning. A quiet day opens no issue.

The report is capped at 25 items and allocated round-robin across the four kinds — arXiv alone returned 60 of 103 items in testing, which would otherwise bury a model launch under a busy week of preprints.

**The first run primes rather than reports.** With no state it would announce a 90-day repo backlog as news, so it records what already exists, opens nothing, and reports only deltas from then on. Run it once by hand before relying on it.

## What the model does and doesn't do

Claude assigns each new paper a cluster and writes its note. That's judgement, and it can be wrong — fix it by editing `data/map.json`.

Claude does **not** supply citations. Every edge written by the automation comes from the citation API and is marked `"v"`. Author lists come from the API; where unavailable the field reads `authors not verified` rather than a guess.

## Reading the map

- **Colour** is cluster.
- **Cream ring** is a bridge: the paper cites across two or more clusters.
- **Green ring** is new in the last three weeks.
- **Solid edge** is a citation confirmed in the citing paper's own text. **Dashed** is likely lineage that was never verified — 18 of the seed graph's 47 edges are dashed. Treat them as provisional.
- Node size is degree within the map, so it reflects this graph, not real citation counts.

Three seed nodes carry `authors not verified`. They were kept because their structural position is well evidenced even though authorship wasn't confirmed.

## Editing by hand

Edit `data/map.json` and push; Pages picks it up. Node ids are `lastnameYEAR`. Adding an edge means appending `{"source": ..., "target": ..., "confidence": "v"|"l"}`. The updater refuses to commit if any edge points at a missing node or if ids collide, so a malformed hand-edit fails the next run loudly rather than silently corrupting the graph.

## Known limits

- Semantic Scholar coverage is thin for work published outside conventional venues. Several seed nodes — the Transformer Circuits publications and the Sharkey et al. interim report — may not resolve to API ids at all, and citations flowing through them will be missed.
- The graph counts citations *within the map only*. There are no true citation totals anywhere in the interface.
- Force-directed layout is non-deterministic. Cluster positions shift between loads; only topology is meaningful.
