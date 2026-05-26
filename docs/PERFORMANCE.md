# Performance Guide

This document describes practical tuning options for `@aiden0z/pptx-renderer` without changing render output semantics.

## List Render Options

Pass these via `renderList(options)` or `PptxViewer.open(input, container, { listOptions })`:

- `batchSize`: number of slides appended per frame batch (default: `12`).
- `windowed`: enable IntersectionObserver-based windowed mounting (default: `false`).
- `initialSlides`: how many slides to mount immediately in windowed mode (default: `4`).
- `overscanViewport`: pre-mount range in viewport heights (default: `1.5`).

ZIP parser safety/performance knobs (pass via `ViewerOptions.zipLimits` or `PptxViewer.open()`):

- `zipLimits.maxEntries`
- `zipLimits.maxEntryUncompressedBytes`
- `zipLimits.maxTotalUncompressedBytes`
- `zipLimits.maxMediaBytes`
- `zipLimits.maxConcurrency`

Use `RECOMMENDED_ZIP_LIMITS` as a safe starting point for untrusted PPTX input.

```ts
import { PptxViewer, RECOMMENDED_ZIP_LIMITS } from '@aiden0z/pptx-renderer';
```

| Limit                       | Recommended value | Effect                                                |
| --------------------------- | ----------------- | ----------------------------------------------------- |
| `maxEntries`                | `4000`            | Rejects archives with excessive file counts           |
| `maxEntryUncompressedBytes` | `32 MiB`          | Rejects a single oversized uncompressed entry         |
| `maxTotalUncompressedBytes` | `256 MiB`         | Rejects large total decompressed archives             |
| `maxMediaBytes`             | `192 MiB`         | Rejects large total media payloads under `ppt/media/` |
| `maxConcurrency`            | `8`               | Bounds concurrent ZIP entry reads                     |

If JSZip metadata does not provide a trustworthy uncompressed size, parsing still checks the actual decoded entry size before accepting the entry. This fallback applies to XML/text entries and media entries, so the same limits remain effective for archives whose size metadata is unavailable.

## Built-In Resource Guards

These guards are applied by the renderer even when ZIP byte limits are configured, because some PPTX structures can be small on disk but expensive after parsing:

- Chart cache point indexes are capped at `10,000` per cache. Oversized `c:ptCount` values do not drive array allocation.
- EMF bitmap previews are rejected above `16,777,216` decoded pixels, above `8192x8192` dimensions, or when the declared bitmap payload is truncated.
- External audio/video media is not preloaded automatically; rendered media elements use `preload="none"`.

## Recommended Presets

### Small deck (<= 30 slides)

```ts
await PptxViewer.open(buffer, container, {
  zipLimits: RECOMMENDED_ZIP_LIMITS,
  listOptions: { batchSize: 12 },
});
```

### Medium deck (30-150 slides)

```ts
await PptxViewer.open(buffer, container, {
  zipLimits: RECOMMENDED_ZIP_LIMITS,
  listOptions: {
    windowed: true,
    batchSize: 8,
    initialSlides: 4,
    overscanViewport: 1.5,
  },
});
```

### Large deck (> 150 slides)

```ts
await PptxViewer.open(buffer, container, {
  zipLimits: RECOMMENDED_ZIP_LIMITS,
  listOptions: {
    windowed: true,
    batchSize: 4,
    initialSlides: 2,
    overscanViewport: 2,
  },
});
```

## Strategy Selection

- Omit `windowed` (or set to `false`) when you need all slides in DOM at once (some compare/export pipelines).
- Use `windowed: true` when memory pressure and long first-render latency are the bottleneck.
- If `IntersectionObserver` is unavailable, windowed mode automatically falls back to full mounting.
- A newer render request supersedes older queued or batched work. This keeps rapid calls such as `setZoom()`, `setFitMode()`, `renderList()`, and `renderSlide()` from continuing stale list batches after the next request has been queued.

## E2E/Test Page Overrides

Dev pages support URL overrides:

- `listStrategy=full|windowed`
- `listBatchSize=<int>`
- `windowedInitialSlides=<int>`
- `windowedOverscanViewport=<number>`

Examples:

- `/test/pages/index.html?listStrategy=windowed&listBatchSize=6`
- `/test/pages/e2e-compare.html?file=sample&listStrategy=full`

## Benchmarking Notes

- Compare both first contentful render and interaction smoothness.
- Measure memory (DOM node count + browser heap) on long decks.
- Validate visual parity with existing unit/e2e tests after tuning.
