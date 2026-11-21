---
name: openrouter-video
description: |
  Generate video through OpenRouter's video API (`openrouter_video`): one prepaid OpenRouter key for Google Veo 3.1 (Lite, Fast, standard), ByteDance Seedance 2.x, MiniMax Hailuo, Alibaba Wan, xAI Grok Imagine, Runway Gen-4.5 and other models in OpenRouter's live catalog. Use for text-to-video, first/last-frame image-to-video from a local keyframe, and reference-to-video. Covers model choice by cost and task, how durations, resolutions and audio are validated against the catalog, pricing SKUs and the charged cost, data-collection routing, negative prompts, and resume after a polling timeout.
---

# OpenRouter video

`openrouter_video` submits to `POST https://openrouter.ai/api/v1/videos`, polls
the job, and downloads the MP4 from the authenticated content endpoint. The
model list, the allowed durations, resolutions and aspect ratios, frame support,
audio, seed, passthrough parameters and prices all come from OpenRouter's live
catalog (`GET /api/v1/videos/models`). When the catalog cannot be reached, the
tool falls back to the snapshot in
`tools/provider_contracts/openrouter_video_models.json`.

## Route and billing

| Item | Value |
|------|-------|
| Tool | `openrouter_video`, `model: "<vendor>/<model>"` (default `google/veo-3.1-lite`) |
| Credential | `OPENROUTER_API_KEY` from https://openrouter.ai (Settings > Keys) |
| Billing | Prepaid OpenRouter credits. The completed job reports the charge in `usage.cost`. |
| Cost reporting | `cost_usd` is `usage.cost` and `data.cost_status` is `settled`. If the job returns no usage, the preflight estimate is reported with `cost_status: pending`. |
| Preflight | `estimate_cost()` and `dry_run: true` price the request from the model's `pricing_skus`. Unknown SKU shapes raise `PriceQuoteRequired` instead of guessing. |

Verified live (2026-11-21): Veo 3.1 Lite, 4 s, 720p, no audio, a local first
frame sent inline. `usage.cost` was $0.12, which is 4 s at the catalog's
`duration_seconds_without_audio_720p` rate of $0.03.

OpenMontage's default budget policy asks the user before any single action over
$0.50 and the first time a new paid tool is used.

## Choosing a model

Prices are from the catalog snapshot (2026-11-21), for 720p without audio unless
noted. Check the live catalog before a production run: the list changes often.

| Model | Price | Durations | Use it for |
|-------|-------|-----------|------------|
| `google/veo-3.1-lite` | $0.03/s at 720p, $0.05/s at 1080p (audio: $0.05/s, $0.08/s) | 4, 6, 8 | Cheapest good image-to-video; drafts and most keyframe animation |
| `google/veo-3.1-fast` | $0.08/s at 720p, $0.10/s at 1080p | 4, 6, 8 | Better motion than Lite at moderate cost |
| `google/veo-3.1` | $0.20/s ($0.40/s with audio) | 4, 6, 8 | Hero shots, 4K |
| `bytedance/seedance-2.0-mini` | video tokens, about $0.03/s at 480p | 4 to 15 | Cheap Seedance drafts |
| `bytedance/seedance-2.0` / `-fast` | video tokens, about $0.15/s / $0.12/s at 720p | 4 to 15 | Character consistency, reference-to-video |
| `bytedance/seedance-2.5` | video tokens, about $0.23/s at 720p | 4 to 30 | Up to 30 s and the richest references |
| `minimax/hailuo-3-max` | $0.05/s at 480p, $0.08/s at 768p | 5 to 15 | Fast physical motion, no audio |
| `alibaba/wan-3.0` | $0.10/s at 720p | 2 to 30 | Long clips |
| `x-ai/grok-imagine-video-1.5-lite` | $0.03/s at 720p plus $0.01 per input image | 1 to 15 | Very cheap short clips |
| `runway/gen-4.5` | $0.12/s | 2 to 10 | Runway look, first frame only |

Kling is not in OpenRouter's video catalog; use `kling_video` or
`kling_official_video` for Kling.

Seedance bills video tokens: width x height x frames / 1024, with
frames = 24 x seconds + 1. The rendered frame can be larger than the nominal
tier: a 4 s 480p 16:9 clip rendered at 864x496 with 97 frames and settled at
$0.142 (verified 2026-11-21). The tool adds a 5% margin to token estimates so
preflight does not undercount; `usage.cost` is the real charge.

## Operations

| Operation | Inputs | Notes |
|-----------|--------|-------|
| `text_to_video` | `prompt` | Default when no frame or reference is given. |
| `image_to_video` | `prompt` + `reference_image_path` or `reference_image_url` (first frame), optionally `last_image_path` / `last_image_url` | Only models whose catalog lists `first_frame` / `last_frame` accept them. |
| `reference_to_video` | `prompt` + `reference_image_paths` / `reference_image_urls` | Sent as `input_references`. Use Seedance for recurring characters. |

The operation is inferred when omitted. Frames with `operation: "text_to_video"`
are an error, not a silent downgrade.

Local files are sent inline as base64 data URIs (verified on Veo). Nothing is
uploaded to a third-party host, and `video_selector` does not route local frames
through fal for this tool. Remote frames must be `https` URLs. Results and job
checkpoints replace the inline bytes with a size placeholder.

## Parameters

- `duration`: must be one of the model's `supported_durations`; an unsupported
  value fails before any charge and lists the valid ones. When omitted, the tool
  picks 5 s, or the next supported length (6 s on Veo). Models whose catalog
  lists `-1` choose their own length when no duration is sent.
- `resolution` and `aspect_ratio`: checked against the catalog. Defaults are
  `720p` and `16:9` when the model offers them.
- `generate_audio`: **off by default** in this tool, because audio costs more
  and OpenRouter itself defaults it on. Set `true` for native audio on models
  that support it; requesting audio from a silent model is an error.
- `seed`: only for models whose catalog says they take one.
- `negative_prompt`: sent through Veo's provider passthrough
  (`provider.options["google-vertex"].parameters.negativePrompt`). Other models
  reject it; pass their own keys through `provider_options` if the catalog's
  `allowed_passthrough_parameters` lists them.
- `exclude_data_collection`: off by default. Set `true` to send
  `provider.data_collection: "deny"`, which keeps the request away from
  endpoints that train on request data. Some models may then have no endpoint.
  No video endpoint offers zero data retention.
- `provider_preferences`: OpenRouter routing (`order`, `only`, `ignore`),
  merged into the `provider` object.

## Prompting

- The keyframe decides the motion more than the prompt. A subject facing or
  walking toward something keeps going that way. If a clip moves the wrong way,
  redraw the keyframe so the intended motion is the only plausible one (side
  profile facing the direction of travel works well). Prompt wording alone rarely
  fixes it.
- Say "static camera" if the camera must not move; Veo Lite tends to track the
  subject otherwise.
- Describe one continuous action that fits the duration.

## Failure handling

- HTTP 402: the OpenRouter balance is empty. Tell the user; do not switch
  provider silently.
- HTTP 429, 502, 503 at submission: nothing was created, so a new submission is
  safe after a pause (`data.retryable: true`).
- Job `failed`, `cancelled` or `expired`: the error is in the message. A new
  submission is a new charge, so change the prompt or inputs first.
- Poll timeout: the result carries `data.resume_job`. Call `openrouter_video`
  again with that object (and a longer `poll_timeout`) to keep waiting on the
  same job. Set `job_path` to persist the checkpoint. The API has no cancel
  endpoint.
- The tool never retries a paid submission on its own.

## Refreshing the catalog snapshot

```bash
curl -s https://openrouter.ai/api/v1/videos/models > /tmp/or_models.json
```

Then rewrite `tools/provider_contracts/openrouter_video_models.json` with the
new `data` list (same fields) and update `verified_on`.

## Provenance

Every result records `data.request` (the payload sent, without inline image
bytes), `data.job_id`, `data.generation_id`, `data.usage`,
`data.catalog_source` (`live` or `snapshot`) and `data.resume_job`.
