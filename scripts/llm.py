#!/usr/bin/env python3
"""
Shared model transport.

Two jobs in this repo ask a model for judgement — the weekly citation map and
the daily frontier watch — and both hit the same two problems: a claude.ai
Enterprise seat is not an Anthropic Console account and does not come with an
API key, and a model that returns almost-JSON should not cost a whole run.
Keeping one copy means a fix lands in both.

Neither job lets the model supply facts. Releases, versions, dates and
citation edges come from APIs; the model only ranks, classifies and writes
prose about what those APIs already returned.
"""

import json, os, re, sys, time, urllib.request, urllib.error

# A direct Anthropic key wins when both are set: it is one hop fewer. With
# neither, callers degrade to whatever they can do without judgement.
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY")
BRAINTRUST_KEY = os.environ.get("BRAINTRUST_API_KEY")
GATEWAY  = os.environ.get("BRAINTRUST_GATEWAY", "https://gateway.braintrust.dev/v1")
MODEL    = os.environ.get("MAP_MODEL", "claude-sonnet-5")


def have_model():
    return bool(ANTHROPIC_KEY or BRAINTRUST_KEY)


def _post(url, body, headers, tries=3):
    """POST JSON patiently. A transient 429 on a scheduled job should cost a
    retry, not the run's whole output."""
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
        if d.get("stop_reason") == "max_tokens":
            print("  ! response truncated at max_tokens", file=sys.stderr)
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
        if not choices:
            print(f"  ! gateway returned no choices: {str(d)[:200]}", file=sys.stderr)
            return None
        if choices[0].get("finish_reason") == "length":
            print("  ! response truncated at the token limit", file=sys.stderr)
        return choices[0].get("message", {}).get("content", "")

    return None


def json_array(txt):
    """Pull a JSON array out of a model response, tolerating code fences and
    any stray prose around it. Returns None if there is no parsable array."""
    if not txt:
        return None
    txt = re.sub(r"```(?:json)?", "", txt.strip())
    i, j = txt.find("["), txt.rfind("]")
    if i == -1 or j <= i:
        return None
    try:
        arr = json.loads(txt[i:j + 1])
    except Exception:
        return None
    return arr if isinstance(arr, list) else None
