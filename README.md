# evals_research_repo

A self-updating citation map of four literatures: AI evaluation methodology, mechanistic interpretability, RL and reward hacking, and human–AI interaction and design.

The map is a real citation graph — nodes are papers, edges are citations — rendered as an interactive page. It grows weekly, and it tells you when a new paper attaches across cluster boundaries, because those are the papers joining the four literatures together.

**Live map:** `https://imhurl23.github.io/evals_research_repo/`

## Layout

```
index.html                     the viewer; fetches data/map.json at load
data/map.json                  the graph. this is the only file you edit by hand
data/rejects.json              papers already screened and declined
digests/YYYY-MM-DD.md          what each weekly run found
snapshots/YYYY-MM-DD.png       rendered image of the map that week
scripts/update_map.py          the weekly updater
.github/workflows/             the schedule
```

Data is separate from the viewer on purpose. The weekly job edits JSON, never JavaScript, so a bad run produces a readable diff instead of a broken page.

## Setup

1. **Create the repo** as `imhurl23/evals_research_repo` and push these files to `main`.
2. **Enable Pages:** Settings → Pages → Source: *Deploy from a branch* → `main` / `/ (root)`. The map is live in about a minute.
3. **Add a secret:** Settings → Secrets and variables → Actions → New repository secret → `ANTHROPIC_API_KEY`. Without it the map still updates, but new papers arrive with no summary or cluster assignment.
4. **Optional:** add `S2_API_KEY` for a [Semantic Scholar key](https://www.semanticscholar.org/product/api). Unkeyed access is heavily rate limited, and with 39+ nodes to query the first few runs will be slow without one.
5. **Test it:** Actions → Weekly citation map update → Run workflow. Don't wait a week to find out it works.

The schedule is Mondays 13:00 UTC. Change the cron in `.github/workflows/weekly-update.yml`.

## How a paper gets added

Citations come from the Semantic Scholar API, not from a language model. A paper is added if it:

- cites **two or more** papers already on the map, or
- is a survey, position paper, or benchmark citing at least one, since those become hubs, or
- cites at least one and has unusual citation velocity for its age.

Everything else is logged to `data/rejects.json` with a reason and skipped in future runs. Additions are capped at 8 per week; over the cap, papers with the widest cross-cluster attachment win and the rest are reconsidered next week. An unreadable map has failed at its job.

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
