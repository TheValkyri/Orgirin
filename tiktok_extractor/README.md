# tiktok_extractor — PoC for the Origin TikTok integration plan

This is a working, tested implementation of Extractor A and the
orchestrator/contract layer described in `tiktok-integration-plan.md`
(sections referenced below use `§` to point back to that document). It is
meant to be handed to an AI coding agent (or worked on directly) as the
starting point for wiring TikTok support into Origin — not as a finished
feature.

## What's real vs. what's a stub

| Module | Status | Plan ref |
|---|---|---|
| `models.py` | Complete, tested | §6 (unified contract) |
| `errors.py` | Complete | §9 (error taxonomy) |
| `parser.py` | Complete, tested against 9 fixtures | §4 (parsing + gear selection) |
| `url_resolver.py` | Complete, tested | §15 (short-link handling) |
| `webpage_fetch.py` | **Written but NOT network-tested** | §4 |
| `extractor_b_stub.py` | Deliberate stub — isolation contract only | §5 |
| `orchestrator.py` | Complete, matches fallback-chain rules in §3/§5 | §3 |
| Task queue / QWebChannel / FFmpeg pipeline | **Not started** | §7, §8 |

### Why `webpage_fetch.py` is unverified

This sandbox's outbound network is allow-listed to package registries only
(pypi, npm, github, etc.) — `tiktok.com` is not reachable from here, so the
actual HTTP fetch + HTML script-tag extraction could not be exercised
against live traffic. The code is written to spec based on the researched
page structure, but **the implementing agent's first task in Phase 1
should be**: run this against 3-5 real public TikTok URLs (one plain
video, one photo/carousel, one with an unusual caption/emoji, one
already-deleted post) and fix whatever the real HTML doesn't match —
most likely candidates for drift: the exact script tag `id` attribute, and
whether the wrapper still nests at `__DEFAULT_SCOPE__` → `webapp.video-detail`
(the fast path in `parser.py` is just an optimization; if it's wrong, the
walk-based fallback should still find the data — that's the point of §4).

### Why `parser.py` can be fully trusted despite that

`parser.py` never touches the network — it operates on a plain Python dict.
All 9 fixtures under `tests/fixtures/` were constructed from real field
names/structures confirmed against TikTok's own CDN response samples and
yt-dlp's TikTok extractor source (both cited in the research that produced
the architecture plan), not guessed. The gear-selection logic specifically
was mutation-tested (see commit history / dev notes) — an earlier version
of the h265-tiebreak test only compared gears at *different* resolutions,
which meant it couldn't actually detect a broken codec comparison; a
second fixture (`video_codec_tiebreak.json`) with identical resolution and
different codec was added specifically to close that gap. If you extend
`parser.py`, extend the fixtures first — a passing test suite should mean
something.

## Running the tests

```bash
pip install pytest requests --break-system-packages   # if not already present
python3 -m pytest tiktok_extractor/tests/ -v
```

25 tests, all passing, zero network access required.

## How this maps onto Origin's existing codebase

- `TikTokMediaResult` (§6) should become the object your download-pipeline
  code consumes — treat it exactly like whatever intermediate format
  currently comes out of Origin's yt-dlp-based YouTube extraction step,
  and feed it into the same task queue (§8) rather than building a parallel
  code path.
- `ExtractorOrchestrator.extract()` is the single entry point the task
  queue should call. It already implements the A-primary/B-fallback
  decision from §3/§5 — callers don't need to know Extractor B exists.
- Post-download verification (§7.4/§10 — probe the actual downloaded file
  and compare against the selected gear before marking a task successful)
  is **not implemented here** because it depends on Origin's existing
  ffprobe/task-completion code, which this sandbox doesn't have access to.
  This is the top-priority next piece to write, given it's the direct fix
  for the "false success reporting" bug class already found in Origin's
  YouTube pipeline during the prior audit.

## Next steps, in order (mirrors plan §14)

1. Point `webpage_fetch.py` at 3-5 live URLs, fix whatever doesn't match.
2. Wire `ExtractorOrchestrator` into Origin's existing task queue as a new
   source type (§8) — reuse the state machine, don't fork it.
3. Add the ffmpeg stream-copy audio path and the post-download
   verification step (§7).
4. Photo/carousel multi-file task support — confirm first whether Origin's
   task model already supports one task producing N output files (open
   question in plan §15).
5. Extractor B real implementation, behind the feature flag, only once
   1-4 are solid.
