# Design: Writer Factory — content-generation pipeline

- **Date:** 2026-06-12
- **Status:** Approved (architecture); implementation plan pending
- **Scope:** new `app/writer/` subsystem replacing the inline caption /
  comment generation paths in `posting_service` and `engagement_service`

## 1. Problem & context

Today's caption / comment generation has three failure modes that compound
into "matrix-bot" output:

1. **Prompt hijacks the video.** Captions go through `generate_caption_for_local_file`
   which uploads the video to Gemini, but the prompt template puts persona,
   reference examples, hashtag rules and CTA requirements *before* the
   video. Gemini follows the louder prompt instructions and ignores the
   video. Evidence: 25 most recent sport-group captions reference **0**
   video-specific moments — every line is generic NBA backdrop.

2. **Comments don't see the video at all.** `generate_comment.render_prompt`
   only injects `Video title: <title>` — the actual video bytes are never
   uploaded. Gemini fabricates product pitches that have no relation to the
   target clip.

3. **Cross-account caption reuse.** `posting_service._caption_for_publication`
   caches the first caption per video and replays it across every sibling
   publication. 44% of recent sport captions are byte-identical copies of
   another row.

Layered on top: prompts are bound 1-per-group, so every account in a group
speaks the same way; there is no diversity seed, so the same prompt + same
video produce near-identical output every time; the 4 reference examples
in the sport prompt act as a fixed template the model parrots; the IELTS
comment path mixes AI output with a small set of hard-coded short phrases
that loop.

This design replaces the ad-hoc generation paths with a 7-layer pipeline
under `app/writer/`. The pipeline is **dynamic** (no persistent account ↔
persona binding), **zero-config per product** (no per-vertical persona
authoring), and **observable end-to-end** (each layer's output stored as
audit).

## 2. Goals / non-goals

**Goals**

- Captions reference at least one concrete moment from the video they
  describe. Target ≥ 70% of generated captions score ≥ 7/10 on the
  judge's `fact_reference` dimension.
- Comments are generated against the actual target video (not just the
  title). The video is uploaded to Gemini and described before the
  comment prompt runs.
- Zero byte-identical captions across publications of the same video.
- Same group, same video → different output every account, every run.
- New product / vertical onboarding requires only a new prompt body and
  product brief. No persona authoring, no schema migration.
- Every generated caption / comment carries a full audit trail: video
  facts, voice composition, angle, candidates, critique notes, winner
  selection.

**Non-goals**

- Multi-model ensemble (Claude / GPT alongside Gemini). Out of scope —
  may revisit if v2 plateaus.
- Self-play adversarial training against an "AI detector". Out of scope.
- Account-level persona memory / continuity ("this account speaks like
  the same person over time"). Out of scope — dynamic voice per
  generation is the deliberate design choice.
- Real-time A/B prompt tuning against GeeLark engagement data. Out of
  scope, but the audit log is designed so it can be added later.
- Cross-platform expansion beyond TikTok captions and TikTok comments.
  Captions for YT / IG / FB publications go through the same pipeline
  but no platform-specific prompt variants are authored in v2.

## 3. Architecture

```
┌────────────────────────────────────────────────────────────┐
│ INPUT                                                      │
│  - Own video (publication path) → videos.local_path        │
│  - Target video (engagement path) → discovered_videos      │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 0  TREND OBSERVATORY                                 │
│  - Weekly cron: scrape top-performing captions per         │
│    vertical via existing Bright Data discovery hook        │
│  - Gemini distills 8-12 "current style anchors" per        │
│    vertical → settings.trend_anchors_{vertical} (jsonb)    │
│  - Updated weekly, surfaces in Layer 4 prompt context      │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 1  VIDEO UNDERSTANDING                               │
│  - Gemini-with-schema call: video bytes → VideoFacts JSON  │
│  - Schema forces concrete fields (opening_2s,              │
│    climax_moment, on_screen_text, spoken_verbatim,         │
│    visible_props, vibe …)                                  │
│  - Cached on videos.understanding /                        │
│    discovered_videos.understanding (jsonb).                │
│    One Gemini call per video, ever.                        │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 2  VOICE COMPOSITION                                 │
│  - No persona table. Orthogonal trait pools:               │
│    age_band, gender, current_mood, vibe,                   │
│    knowledge_level, speech_rhythm, slang_palette,          │
│    emoji_freq, capitalization_style                        │
│  - Random per-generation sample → Gemini synthesises a     │
│    one-shot 3-sentence voice card                          │
│  - Voice is NOT persisted to accounts. Same account can    │
│    speak as a 22F retaker on Monday and a 30M sceptic      │
│    on Tuesday.                                             │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 3  ANGLE SELECTION                                   │
│  - Static angle pool: anxiety, flex, reaction, skeptic,    │
│    sarcasm, aspirational, confession, comparison,          │
│    advice, observation                                     │
│  - Weighted random pick (weights start uniform; can be     │
│    tuned later from engagement data)                       │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 4  GENERATION                                        │
│  - Inputs: video_facts, voice, angle, trend_anchors,       │
│    prompt body, product brief                              │
│  - ONE Gemini call with a JSON-array schema enforcing      │
│    exactly 5 candidate objects {text, opener_used}         │
│    (not `n=5` retries — schema-driven so the call is       │
│    deterministic and we don't pay per-candidate latency)   │
│  - Hard rule injected into the prompt:                     │
│    "reference at least ONE specific moment from the        │
│    video_facts list above; generic lines are rejected"     │
│  - Hard rule: avoid CTA boilerplate ("who you got",        │
│    "drop picks", "comment below", etc.)                    │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 5  CRITIQUE & REVISE                                 │
│  - Single Gemini call per generation: for each of the 5    │
│    candidates emits {concrete_problems: [...]}             │
│  - If any candidate has ≥ 2 problems, that candidate is    │
│    sent back to a reviser call together with its critique  │
│    → revised version replaces the original in the pool     │
│  - Cap: 1 revise round (no infinite loops)                 │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 6  SELECTION + FINGERPRINT                           │
│  - Judge call: score each candidate on 4 dims              │
│    (fact_reference, voice_fit, anti_ai, freshness)         │
│    + extract a style fingerprint                           │
│  - Self-dedup: compare fingerprint vs this account's       │
│    last 30 days of captions (Jaccard, reject if > 0.7)     │
│  - Cross-account dedup: compare vs same vertical's last    │
│    7 days across all matrix accounts (reject if > 0.85)    │
│  - Top-scoring candidate that passes both dedup wins       │
│  - If no candidate passes: log + return highest-scorer     │
│    anyway (never block publication)                        │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│ LAYER 7  DELIVERY + AUDIT                                  │
│  - Write caption to publications.caption (or               │
│    engagement_runs.comment_text)                           │
│  - Write full audit row: voice, angle, all candidates,     │
│    critique notes, scores, winner_idx                      │
└────────────────────────────────────────────────────────────┘
```

## 4. Data model changes

**New tables**

```sql
CREATE TABLE writer_audit (
  id              SERIAL PRIMARY KEY,
  kind            VARCHAR(20) NOT NULL,  -- 'caption' | 'comment'
  publication_id  INT REFERENCES publications(id) ON DELETE CASCADE,
  engagement_run_id INT REFERENCES engagement_runs(id) ON DELETE CASCADE,
  video_id        INT,                   -- own or discovered
  account_id      INT,
  voice           JSONB NOT NULL,
  angle           VARCHAR(40) NOT NULL,
  trend_anchors_used JSONB,
  candidates      JSONB NOT NULL,        -- [{text, critique, score}, …]
  winner_idx      INT NOT NULL,
  winner_fingerprint JSONB NOT NULL,
  dedup_self_max  FLOAT,
  dedup_cross_max FLOAT,
  total_gemini_calls INT NOT NULL,
  generated_at    TIMESTAMP DEFAULT now(),
  CHECK ((publication_id IS NULL) <> (engagement_run_id IS NULL))
);

CREATE INDEX idx_writer_audit_account_time
  ON writer_audit (account_id, generated_at DESC);
CREATE INDEX idx_writer_audit_kind_time
  ON writer_audit (kind, generated_at DESC);
```

**Modified tables**

```sql
ALTER TABLE videos
  ADD COLUMN understanding         JSONB,
  ADD COLUMN understanding_status  VARCHAR(20) DEFAULT 'pending';
  -- pending | ok | failed

ALTER TABLE discovered_videos
  ADD COLUMN understanding         JSONB,
  ADD COLUMN understanding_status  VARCHAR(20) DEFAULT 'pending';

ALTER TABLE settings
  -- no schema change; trend_anchors_{prompt_id} keys are added as
  -- runtime values via existing setting writes. The "vertical" is
  -- keyed on caption_prompts.id because a group/prompt body is what
  -- defines the lane (current prod: prompt 1 = sport, prompt 2 =
  -- cathoven_ielts). New verticals appear automatically when a new
  -- caption_prompt is created.
```

**Removed code paths**

- `posting_service._caption_for_publication` video-level cache is
  deleted; every publication generates fresh.
- `posting_service._resolve_caption_config` continues to exist but is
  simplified — the writer pipeline reads brief / model directly from
  the caption_prompt row.
- `generate_comment.render_prompt` is replaced with the writer
  pipeline; the existing entry point keeps its signature for
  backwards-compat but delegates to `app.writer`.

## 5. New module layout

```
app/writer/
  __init__.py
  understanding.py     # Layer 1
  voice.py             # Layer 2
  angles.py            # Layer 3
  trends.py            # Layer 0 (cron-driven)
  generate.py          # Layer 4
  critique.py          # Layer 5
  judge.py             # Layer 6
  fingerprint.py       # Layer 6 helper
  pipeline.py          # public entry — orchestrates Layers 1-7
  schemas.py           # Pydantic models: VideoFacts, Voice, Critique, JudgeScore
  prompts/             # versioned prompt templates as .txt
    understanding.txt
    voice_synth.txt
    generation_caption.txt
    generation_comment.txt
    critique.txt
    revise.txt
    judge.txt
    trend_distill.txt
  audit.py             # writer_audit writes / reads
```

`pipeline.py` exposes two functions used by callers:

```python
def write_caption(session, video_id, account_id) -> tuple[str, int]:
    """Returns (caption_text, audit_id). Never raises on AI failure —
    falls back to the best-scoring candidate even if dedup rejects all."""

def write_comment(session, engagement_run_id) -> tuple[str, int]:
    """Returns (comment_text, audit_id). Same fallback policy."""
```

## 6. Voice composition details

The trait pools live in `voice.py` as Python literals (not DB). New
verticals onboard by editing this file, not by writing prompts:

```python
AGE_BANDS = ["17-19", "20-22", "23-25", "26-29", "30-34", "35-40"]
GENDERS   = ["F", "M", "NB"]
MOODS     = ["overwhelmed", "smug", "deadpan", "hyped", "tired",
             "curious", "annoyed", "vulnerable", "playful", "skeptical"]
VIBES     = ["studying-with-me", "main-character", "quiet-academic",
             "chaotic-good", "burnt-out-pro", "fresh-convert",
             "ride-or-die-fan", "armchair-analyst", "casual-observer"]
KNOWLEDGE = ["beginner", "intermediate", "advanced", "obsessive"]
RHYTHMS   = ["short-broken", "mid-flow", "run-on", "punchy"]
EMOJI_FREQ= ["none", "rare", "one-per-line", "constellation"]
CAPS      = ["all-lower", "sentence", "ALL CAPS BURSTS", "mixed"]
SLANG     = ["gen-z-standard", "ielts-prep-niche", "ph-taglish",
             "filo-balikbayan", "tiktok-academic", "minimal"]
```

Layer 2 samples one value from each pool, joins them, and asks Gemini
(via `voice_synth.txt`) to produce a 3-sentence voice card:

```
You are a 22-year-old (F) IELTS retaker, currently overwhelmed,
vibe: studying-with-me, knowledge: intermediate,
speech rhythm: short broken sentences, emoji: rare (~1 per line),
capitalisation: all lowercase, slang palette: gen-z-standard.

In ≤ 3 sentences, describe how this person would caption a TikTok:
their opener tics, two filler words they overuse, one phrase they
NEVER use.
```

Output is consumed by Layer 4 as the voice prompt block.

## 7. Trend Observatory (Layer 0)

A weekly procrastinate cron `trend_refresh` per vertical:

1. Read existing `discovered_videos` rows for the vertical from the
   past 7 days, top 50 by `play_count`.
2. Send their `description` strings (already collected by Bright Data)
   to Gemini with `trend_distill.txt`, which extracts:
   - Top 6 opener patterns
   - Top 4 emoji combos
   - Top 4 hashtag tail combos
   - 3 phrases that feel "of-the-moment"
3. Write the distilled JSON to `settings.trend_anchors_{prompt_id}`.

The vertical mapping is `caption_prompt → discovered_videos` via
whichever Bright Data discovery campaign that prompt's groups
subscribe to; if the schema doesn't already carry that mapping in
W1, add a `caption_prompts.discovery_keywords` text[] column at
the W4 phase that owns the trend cron.

If a vertical has < 20 discovered_videos rows the cron skips it and
leaves the previous anchors in place.

The cron is added to the existing procrastinate schedule alongside
`auto_publish_scan`, `proxy_check`, etc.

## 8. Failure handling

| Failure | Behaviour |
|---|---|
| Layer 1 understanding times out / schema invalid 3x | Mark `understanding_status='failed'`, generation proceeds with `understanding=None` (Layer 4 prompt knows to handle missing facts and writes a generic-but-still-voiced caption) |
| Layer 2 voice synth fails | Fall back to the raw trait list as the voice block |
| Layer 4 returns < 3 candidates | Proceed with what we got; if 0, raise — caller treats as caption generation failure (same as today) |
| Layer 5 reviser fails | Skip revision; use original candidates |
| Layer 6 dedup rejects all candidates | Log warning, return highest scorer anyway |
| Trend anchors missing for vertical | Layer 4 prompt skips that block, no error |

The pipeline NEVER blocks a publication on writer failure — fallback to
"best available output" is preferred over deferring a scheduled post.
Failures are logged and surface in the audit table.

## 9. Cost budget

Per caption / comment generation:

| Layer | Gemini calls | Notes |
|---|---|---|
| 0 Trend | 0 (precomputed) | 1 call/week per vertical, amortised |
| 1 Understanding | 0–1 | 0 if cached, 1 first time per video |
| 2 Voice | 1 | Always |
| 3 Angle | 0 | Pure local random |
| 4 Generation | 1 | Returns 5 candidates in one call |
| 5 Critique | 1 | One call covers all candidates |
| 5 Revise | 0–1 | Only if critique flags ≥ 1 candidate |
| 6 Judge | 1 | Includes fingerprint extraction |
| **Total** | **4–6 per generation** | + 1 amortised for understanding |

At gemini-3-flash pricing the worst-case per-caption cost is roughly
$0.012. 10 000 captions ≈ $120. Current production volume (~30
captions/day average) puts steady-state at < $15/month.

## 10. Pipeline UI (minimal)

A new page `/writer-lab` under the existing dashboard. Three sections,
all read-only except the prompt editor:

1. **Video probe.** Paste a video id (own or discovered) → see the
   stored understanding JSON, or trigger a fresh extraction. Then
   "generate now" runs the full pipeline and shows all 5 candidates
   with critique + scores + fingerprint, plus which one won.
2. **Audit browser.** Filter writer_audit by account, kind, time,
   minimum fact_reference score. Inspect any past generation in full.
3. **Trend anchors.** Read-only display of current
   `trend_anchors_{vertical}` per vertical, with a "refresh now"
   button that triggers the cron task immediately.

No persona editor (there are no personas). No prompt editor in v2 —
prompt files are version-controlled in the repo.

## 11. Test strategy

- **Unit:** voice trait sampling determinism (seeded random),
  fingerprint extraction stability, angle pool sampling weights,
  schema validation of all Gemini outputs.
- **Integration with mocked Gemini:** the full pipeline runs end-to-end
  with a stub Gemini client returning fixed JSON. Verifies:
  - audit row written exactly once per `write_caption` call
  - dedup actually rejects near-duplicates from history
  - fallback path returns highest scorer when dedup rejects all
- **Smoke against live Gemini:** opt-in test (`pytest -m live_gemini`)
  that runs against a sample video. Not run in CI.
- **Backwards-compat:** existing `posting_service` tests must still
  pass — caller signature for caption generation does not change.

## 12. Implementation phases

| Week | Deliverable |
|---|---|
| 1 | Alembic migration + Layer 1 understanding live for new uploads; understanding back-fill cron for existing videos as needed |
| 2 | Layer 2 voice + Layer 3 angle + Layer 4 generation (5 candidates, no critique yet); caption path switches to pipeline for ONE pilot group, others stay on old path |
| 3 | Layer 5 critique + revise; Layer 6 judge + self-dedup; Layer 7 audit table |
| 4 | Cross-account dedup; comment path migrated to pipeline; Layer 0 trend observatory cron |
| 5 | `/writer-lab` UI, regression hardening, full rollout to all groups, decommission old generation code |

Each week's output is independently shippable behind a per-group flag
(`groups.use_writer_factory: bool`). Old code path remains operational
until week 5.

## 13. Success metrics

Measured weekly against the writer_audit table:

| Metric | Today | Target by end of W5 |
|---|---|---|
| Byte-identical caption pairs (same video, 7 days) | 44% | < 2% |
| Captions referencing ≥ 1 video-specific moment (judge.fact_reference ≥ 7) | 0% measured | ≥ 70% |
| Template CTA frequency ("who you got", "drop picks", "comment below") | ≥ 80% | < 20% |
| Self-fingerprint Jaccard ≥ 0.7 within an account, 30-day window | unknown | < 5% of generations |
| Avg Gemini cost per caption | ~$0.001 | ~$0.012 (12× — accepted trade) |
| Pipeline end-to-end p95 latency | n/a | < 30 s |

## 14. Open questions

These do NOT block implementation start — they will be revisited mid-W2
or W3 with real data:

1. **Angle pool weighting** — start uniform; later tune from
   engagement data once GeeLark stats integration exists. No-op for v2.
2. **Trend cadence** — weekly might be too slow for sport (game-day
   rotation). May add `vertical.trend_refresh_hours` setting if W4
   data shows lag.
3. **Voice synth ablation** — do we strictly need the Gemini-synthesised
   voice card, or could Layer 4 consume the raw trait list directly?
   Cheaper if yes; needs A/B test in W2.
4. **Cross-account dedup window** — 7 days is a guess. May tighten
   to 3 if early data shows convergence.
5. **Comment understanding on discovered videos** — does Bright Data
   already provide enough metadata that we can skip Layer 1 for
   comments? Unknown until W4 measurement.
