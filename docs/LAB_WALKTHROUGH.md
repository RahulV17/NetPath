# NetPath · Network Data Path Lab — Complete Walkthrough

This document walks through every file in the project, explaining what each part does, why it's built that way, and how it all connects. Read top-to-bottom for the full story, or jump to a section.

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Simulation Core — `web/src/sim/`](#2-simulation-core)
3. [3D Scene — `web/src/lab/PipelineScene.tsx` + `TrafficViz.tsx`](#3-3d-scene)
4. [State Store — `web/src/lab/store.ts`](#4-state-store)
5. [UI Panels — ControlPanel, StationReadout, Header](#5-ui-panels)
6. [Learning Systems — Challenges, Audio, Summary Card](#6-learning-systems)
7. [Mobile Layer](#7-mobile-layer)
8. [Entry & Build](#8-entry--build)
9. [How a Frame Flows (end-to-end trace)](#9-how-a-frame-flows)
10. [Design Decisions & Trade-offs](#10-design-decisions--trade-offs)

---

## 1. The Big Picture

NetPath is an **interactive 3D teaching website** that explains how a modern network data path works. A packet travels left to right through 8 stations:

```
Ingress → Parser → ACL → ML Classifier → HW Offload → L2/L3 Forwarder → QoS Shaper → Egress
   0        1       2          3              4             5                6          7
```

The central insight (spec §2): **the data path is not one pipeline**. Simple flows get offloaded to hardware at Station 4 and skip everything after; only complex packets take the CPU slow path through forwarding and QoS. The visualization makes this visible — green arcs carry ~80% of packets over stations 5–6.

Two engines cooperate:

- **`NetPathEngine`** (`sim/engine.ts`) — headless TypeScript port of the Python data path: flow table, ACL, HW offload cache, token-bucket QoS
- **Three.js scene** (`lab/PipelineScene.tsx`) — renders engine state as instanced particles moving through procedural station geometry

React never re-renders per frame. The engine mutates a fixed packet pool in place; a single rAF loop drives everything; UI panels sample at ~4 Hz.

```
┌─────────────┐   rAF tick    ┌──────────┐  ~4Hz   ┌─────────┐
│ SimDriver   │──────────────▶│  Engine  │────────▶│ Zustand │──▶ React UI
│ (LabPage)   │   step(dt)    │ (pool)   │ push    │  store  │    (4Hz)
└─────────────┘               └────┬─────┘         └─────────┘
                                   │ reads pool directly
                                   ▼
                            ┌─────────────┐
                            │ InstancedMesh│ ← useFrame (per frame,
                            │  (packets)   │   zero allocation)
                            └─────────────┘
```

---

## 2. Simulation Core

### `sim/types.ts` (~60 lines)

Shared vocabulary. Key types:

- **`TrafficClass`** — VOICE / VIDEO / INTERACTIVE / BULK / BEST (mirrors Python's enum)
- **`OffloadTarget`** — CPU slow path / CPU fast path / HW NIC / WiFi / crypto / QoS
- **`PacketEvent`** — everything a readout needs for one packet crossing: station, header type, flow hash, offload target, traffic class, queue depth, latency, size
- **`FlowRecord`** — rolling window of packet sizes + inter-arrival times, totals, last-seen timestamp, classification result
- **`AclRule`** — 5-tuple with `-1` wildcards, matching the Python ACL semantics

### `sim/classifier.ts` (~90 lines)

The browser can't run scikit-learn, so this is a **hand-tuned heuristic standing in for the Random Forest** — spec §5 explicitly allows qualitative "representative trends."

It computes the same features as Python: mean/std packet size, mean inter-arrival time (IAT), jitter (std of IAT), pps. Then ordered rules mimic tree traversal:

```ts
if (meanSize < 320 && jitter < 8 && meanIat > 4ms) → VOICE
if (meanSize > 1250 && meanIat < 3ms)              → BULK
if (meanSize > 800 && jitter ≥ 3)                  → VIDEO
if (jitter > 15 && meanSize < 600)                 → INTERACTIVE
default                                            → BEST
```

Thresholds chosen so the three Gaussian archetypes from the Python seed data classify correctly: voice ≈ 200 B @ 20 ms regular spacing (tiny jitter is the tell), video ≈ 1200 B bursty, bulk ≈ 1400 B back-to-back.

### `sim/engine.ts` (~450 lines) — the heart

A direct TS port of the Python `DataPathEngine`. The class owns:

**Fixed-size packet pool** (`MAX_PACKETS = 220`). Allocated once in `makePool()`. `step()` mutates slots in place and flips `active` flags — never allocates during animation (spec §11). Each packet carries: progress (fractional station index 0–7), speed, kind (voice/video/bulk/control), size, fast-path flag, drop state, traced flag, hash. Per-packet lifecycle flags (`_aclChecked`, `_classified`, `_offloadDecided`, `_policed`) ensure each stage fires exactly once per packet.

**Spawn logic.** `spawnDebt += dt * (2 + rateNorm * 38)` accumulates fractional packets so low rates still emit smoothly; kinds drawn from the selected traffic-mode distribution.

**Station events** fire as `Math.floor(progress)` crosses each integer:

| Station | What happens |
|---|---|
| 2 | ACL check — `aclDrops()` matches against indexed rules; drop ⇒ packet removed, `stats.dropped++` |
| 3 | Flow tracking — `touchFlow()` updates rolling windows; `classifyFlow()` labels the flow |
| 4 | Offload decision — `decideOffload()` consults the HW cache |
| 6 exit | Policing — `police()` spends token-bucket bytes; violation ⇒ drop |

**Flow table.** Map keyed by 4-hex-char hash, capped at 256 with oldest-expiry eviction. Every packet touches its flow: sizes/IATs appended (100-deep rolling windows), totals accumulated.

**HW offload cache.** Mirrors real silicon: control-plane packets are always complex → CPU slow path. Simple flows get cached with ~80% probability (`OFFLOAD_PROBABILITY`); cached flows hit "HW NIC"/"HW QoS" instantly. Cache holds 64 entries; when full, new flows get "CPU fast path" — software still, but not deep-inspected. This reproduces the Python benchmark behavior where 80–100% of packets bypass the CPU.

**QoS token bucket.** Refills toward a 64 KB cap at 250 KB/s. The QoS Starvation challenge shrinks both cap (4 KB) and refill (8 KB/s) — bulk-heavy traffic draws ~33 KB/s, so policing drops sustain while small voice packets mostly pass, which is exactly the causal story the challenge teaches.

**Challenge injection points.** Five methods mutate live state:
- `injectAclMisconfiguration()` — swap rules for drop-everything
- `injectClassifierDrift()` / `clearDrift()` — set flag that poisons every `classifyFlow()` call to VOICE (the gate inside `classifyFlow` is what makes drift *stick* — otherwise the next packet would immediately relabel correctly)
- `injectOffloadOverflow()` / `hwCacheClear()` — stuff or clear the cache
- `injectQosStarvation()` / `clearQosStarvation()`

**`reset()`** restores everything including challenge state — default ACL rules (with the baseline FTP-drop rule), flags cleared, buckets refilled.

**`sampleReadouts()`** builds the 8-station readout array for the panel — synthetic-but-plausible values derived from live stats (queue depth feeds latency, cache size feeds offload target). Called at 4 Hz, not per frame.

**`selectedPacketEvent`** getter returns real telemetry for whichever packet carries the `traced` flag — powers Follow One Packet.

---

## 3. 3D Scene

### `PipelineScene.tsx` (~250 lines)

R3F `<Canvas>` with NOC lighting (cool key light, warm nickel fill, green point light hovering over Station 4), dark background, floor grid for museum-exhibit depth.

**Stations.** Eight `<Station>` groups: cutaway box housing (opacity tied to pipeline-opacity slider), inner glowing icosahedron core (cyan normally, flame-gold when selected), brass bottom rail + steel top rail, pedestal, and a selection torus ring on the floor. Exploded view offsets each station vertically along a sine curve.

**Conduits.** Open-ended cylinders between adjacent stations with brass collars at joints; they flex with exploded view too.

**FastPathArc.** Quadratic bezier tube from Station 4 arcing 14 units up and 5.5 units behind, landing at Egress — green translucent, toggled by the offloadDecisions layer. This is the visual thesis of the whole lab.

**Packets.** One `<instancedMesh>` of 220 spheres. Per frame, a `useFrame` walks the pool, composes each active packet's matrix (position from progress × span; fast-path packets ride the arc via sine interpolation; scale by kind — bulk 1.15× down to control 0.4×, traced packets 1.6×), sets per-instance color, and writes `instanceMatrix.needsUpdate`. Zero allocations — one reused dummy Object3D and Color.

**CameraRig.** Six presets (Overview/Section/Parser/Classifier/Offload/Free); position lerps toward the goal at 0.06/frame for damped transitions; OrbitControls with distance clamps 14–110 so you can't clip into the pipeline or lose it.

**Mobile adaptations** (props-driven): DPR capped `[1, 1.5]` vs desktop `[1, 2]`; initial camera raised/pulled-back ([0, 30, 70], fov 50) so the full pipeline fits portrait screens.

### `TrafficViz.tsx` (~180 lines)

The four traffic-visualization layers:

- **HeaderLayers** — four stacked translucent plates (Eth/IP/TCP/Payload colors) bobbing above Station 1 with phase-shifted sine motion; reads as headers being peeled apart during parsing.
- **FlowPaths** — thin cylinders inside each conduit carrying a shared scrolling-dash canvas texture; one material, seven meshes (spec §11 reuse), offset animated in one `useFrame`.
- **QueueViz** — instanced orange cubes stacking in rows above Station 6, count driven by the engine's *real* `queueDepth` stat. When starvation drops pile up, you see the queue grow.
- **SectionClipping** — owns a horizontal clipping plane eased between below-floor (-10) and mid-housing (1.2); enables renderer-local clipping via effect and clears planes on unmount. Cut leaves the arc apex and header plates visible intentionally (context, not cutaway targets).

### `WebglFallback.tsx`

If WebGL is unavailable: static SVG cutaway of the 8 stations plus the exact spec message about enabling hardware acceleration. No canvas, no crash.

---

## 4. State Store

### `store.ts` (~230 lines)

One Zustand store, single source of truth:

**Data path:** `pathState` machine (Idle → Starting → Parsing → Classifying → Offloading → Forwarding → Shaping → Running → Stopping → drain → Idle — transitions driven partly by UI actions, partly by SimDriver observing the pool draining), `packetRate` (0–100 %), `activeFlows`, `trafficMode` (mixed/voiceHeavy/bulkHeavy), `throughputGbps`.

**Model:** `exploded` slider (0–1), `sectionView`, `pipelineOpacity`.

**Layers:** four booleans consumed by TrafficViz.

**Camera/station:** preset id, selected station index.

**Learning:** current chapter, `chaptersVisited[]` (accumulates for the summary card — deliberately survives Reset), sound toggle.

**Mobile panels:** `isControlPanelOpen`, `isReadoutOpen` with mutual-exclusion actions that take `isMobile` so desktop stays exempt; `isDesktopHintVisible`.

Design rule: **no per-frame values live here.** Continuous animation data lives in refs/mutable pools; the store receives ~4 Hz readout pushes. This keeps React renders cheap.

---

## 5. UI Panels

### `LabPage.tsx`

Owns the engine instance, the SimDriver rAF loop, challenge results history, audio lifecycle, and page assembly.

**SimDriver** is the heartbeat: clamps dt to 50 ms (tab-switch spikes), skips when `document.hidden` but keeps the clock honest by resetting `last`, steps the engine, fires audio cues on stat deltas (offload blip when processed jumps by 4+, drop thud when dropped increases), advances the startup state machine (Starting→Parsing→…→Running at 4 Hz so learners see the sequence), pushes readouts, and drains gracefully on Stop before returning to Idle.

**Header.** Plaque eyebrow ("THE PACKET ENGINE No. 01"), serif title, subtitle, chapter card with PREV/NEXT buttons and the chapter question/tip from the spec's six chapters.

### `ControlPanel.tsx`

Left rail with sections:

- **Data Path** — Start/Stop (disabled appropriately), Packet Rate slider, Path State / Active Flows (Normalized) / Throughput (shown in dual units: "Approx. X Gbps / Y MB/s" per spec §5)
- **Environment** — protocol-mix select, Flow Count slider, Reset to Default
- **Model** — Exploded View, Pipeline Opacity, Section View toggle
- **Traffic Visualization** — the four layer toggles
- **Follow One Packet** — Trace toggle; when active, polls `engine.selectedPacketEvent` at 4 Hz and shows live truth: station name, flow hash, size, latency
- **Camera** — six preset toggles
- **Learning** — Sound toggle, Fault Challenge button (aria-expanded/controls wired to modal id), Download Summary Card

### `StationReadout.tsx`

Right rail: horizontally scrollable Station 0–7 tabs, inline SVG mini-diagram highlighting the selection, then the seven spec readouts (Packet Size, Header Type, Flow Hash, Offload Target, Traffic Class, Queue Depth, Latency) — all labeled NORMALIZED so nothing reads as production data.

### `Disclaimer.tsx`

Spec §4 copy about educational values — pinned bottom-center.

---

## 6. Learning Systems

### `ChallengeModal.tsx` (~270 lines)

Non-blocking fault-diagnosis game. Four scenarios, each bound to verified injection points:

| Scenario | Injects | Correct stage | Teaches |
|---|---|---|---|
| ACL Misconfiguration | drop-all rule | 2 | Overly broad rules discard everything |
| Classifier Drift | drift flag | 3 | Lost training signal mislabels bulk as voice → downstream queue starvation |
| Offload Cache Overflow | stuffed cache | 4 | Cache exhaustion forces CPU fallback |
| QoS Starvation | tiny bucket | 6 | Undersized tokens starve bulk while voice passes |

Flow: pick scenario (fault goes live immediately) → "Select the affected stage" tabs → "Choose the best causal explanation" options → feedback. Wrong answer offers RETRY; right answer shows the spec's exact success copy for drift. If you solved it but picked the wrong station first, a tip reveals the true station. Closing reports once-per-cycle to `onResult` (correct→true, abandoned→false; wrong attempts never fire — guarded by a ref). Cleanup always restores normal behavior surgically (ACL restore doesn't nuke running packets/stats).

### `audio.ts`

Lazy AudioContext (created/resumed only after user gesture — browser policy), master gain 0.12. Synth blips: start (440 Hz triangle), offload burst (880 Hz), drop (160 Hz square), complete chime (523→784). `setMuted(true)` fully suspends the context so even in-flight blips are silent.

### `SummaryCard.tsx` + `summaryContent.ts`

Canvas-rendered plaque: brass double border, plaque eyebrow, serif title, green-check list of completed chapters, active layers, PASS/MISS challenge history, normalized throughput footnote, and the spec's closing insight line in italic serif across two lines. Downloads as `netpath-learning-summary.png` via `toBlob`; plays the completion chime.

---

## 7. Mobile Layer

### `MobileLayer.tsx`

Everything gated behind `useIsMobile()` (live matchMedia):

- **Mutual exclusion** — store actions close the other panel on mobile only
- **DesktopHint** — dismissible notice with exact spec copy, sessionStorage persistence, hidden ≥768px
- **MobileBars** — two bottom buttons (48 px min height, safe-area padding): controls drawer opener with live state chip, station-data opener
- **DrawerClose** — force-closes both panels from inside either drawer

Desktop panels are wrapped in visibility divs so mobile swaps absolute rails for drawers without remounting or losing simulation state. Camera presets untouched on desktop per spec §7.

---

## 8. Entry & Build

- **`index.html`** — `lang="en"`, viewport-fit=cover, spec meta description, title "NetPath · Network Data Path Lab"
- **`main.tsx`** — mounts LabPage, nothing else
- **`index.css`** — spec palette as CSS custom properties (--void #05070a, --bone #e8e0cc, --nickel #b08d57, --air #6fc7e8…), Source Serif 4 / Inter / JetBrains Mono typography split (serif titles, sans controls, mono measurements), focus-visible outlines, prefers-reduced-motion kill-switch for all transitions
- **Build:** `npm run build` → `tsc && vite build`, clean

---

## 9. How a Frame Flows

Trace one 16 ms frame with sound on and the sim running:

1. **SimDriver.tick** wakes (rAF), dt ≈ 0.016 s
2. `engine.step(0.016, true, 0.35, 'mixed')`:
   - spawnDebt += 0.016 × 15.3 ≈ 0.24 → maybe no spawn this frame
   - each active packet: progress += speed × dt × 0.9
   - a packet crosses 2.0 → ACL checks; crosses 3.0 → flow updated/classified; crosses 4.0 → 80% chance cached → fastPath=true
   - fast-path packets accelerate; slow-path packets entering 6.0 dwell and stack queueDepth
   - police() refills tokens, may drop a bulk packet
3. Audio deltas checked: processed jumped by 5 → `sfx.offload()`; dropped +1 → `sfx.drop()` (only if context running)
4. Startup state machine advances if still Starting→…
5. If 250 ms elapsed since last push → `sampleReadouts()` written to store → **this is the only thing that triggers a React render**, updating ControlPanel numbers and StationReadout
6. Separately, R3F's own loop runs every `useFrame`:
   - Packets component recomputes ≤220 instance matrices/colors from the mutated pool — one draw call
   - QueueViz restacks cubes from `engine.stats.queueDepth`
   - FlowPaths scrolls its texture offset
   - SectionClipping eases its plane
   - CameraRig lerps toward the preset goal
7. GPU draws: grid, 8 stations, conduits, arc, one instanced sphere mesh, queue cubes, header plates, flow tubes

Net allocations per frame: zero. React renders: 4 per second.

---

## 10. Design Decisions & Trade-offs

**Why a heuristic classifier instead of ONNX/sklearn-port?** Spec §5 says trends are representative; a 30-line rule set is auditable, instant, and dependency-free. The thresholds document domain knowledge (voice = small+regular+low-jitter). An ONNX runtime would add megabytes for no pedagogical gain.

**Why a fixed pool instead of spawning objects?** GC pauses during animation cause visible stutter. 220 pre-allocated slots with an `active` flag give O(1) spawn/despawn with zero garbage.

**Why does the engine keep its own stats instead of deriving from React?** React state is async/batched; the sim needs exact deltas within a tick (e.g., audio triggers compare processed counts before/after step).

**Why is the fault left running while feedback shows?** Watching the diagnosed symptom while reading the explanation cements causality. Cleanup happens on retry/close.

**Why synthetic readout formulas?** Real per-packet truth exists only for the traced packet; faking plausible per-station numbers labeled NORMALIZED is spec-sanctioned and cheaper than instrumenting every crossing.

**Known trade-offs (documented, deliberate):**
- MobileBars chip samples state at render-time rather than subscribing — updates lag until next render (flagged in code review)
- Readouts blend real stats with formula-derived values
- `(p as any)._aclChecked` style flags work but aren't type-safe — a proper per-stage state bitmask would be cleaner

---

## Running It

```bash
cd web
npm install        # three, @react-three/fiber@8, drei@9, zustand@5
npm run dev        # http://localhost:3000
npm run build      # tsc && vite build
```

Click **▶ Start** in the left panel. Watch packets flow; notice most jump the green arc at Station 4. Toggle layers off/on to isolate visuals. Trace a packet. Then open **Fault Challenge**, pick Classifier Drift, and diagnose it.
