# NetPath — Code Walkthrough

A guided tour through every module, explaining what each part does and *why it's built that way*. Read this after skimming the README. File sizes are approximate line counts.

**Reading order:** parser → datapath → ml_classifier → hw_offload → qos → generator → api → web

---

## 1. `core/parser.py` (~790 lines) — The Foundation

The single most important file. It decodes raw network bytes into typed Python objects using nothing but `struct` and bit manipulation.

### Protocol constants (lines 17–77)

```python
class EtherType(IntEnum):
    IPv4 = 0x0800
    ARP = 0x0806
    IPv6 = 0x86DD
    VLAN = 0x8100
```

These are the *dispatch values* — the number in an Ethernet header's type field tells the parser what's inside, exactly like a real NIC driver does. Same pattern for `IPProto` (1=ICMP, 6=TCP, 17=UDP, 47=GRE) and `TCPFlag` (SYN=0x02 … CWR=0x80).

### Header dataclasses — one per protocol

Each is a frozen dataclass with this shape:

```python
@dataclass(frozen=True)
class Ethernet:
    dst_mac: str
    src_mac: str
    ethertype: int
    payload: bytes

    FORMAT: ClassVar[str] = "!6s6sH"        # big-endian: 6B + 6B + 2B
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)   # = 14

    @classmethod
    def parse(cls, raw: bytes) -> Ethernet:
        if len(raw) < cls.SIZE:
            raise ValueError(...)
        dst, src, etype = struct.unpack(cls.FORMAT, raw[:cls.SIZE])
        return cls(dst_mac=_format_mac(dst), ..., payload=raw[cls.SIZE:])
```

Key ideas:
- **`!` in the format string** = network byte order (big-endian). Every network protocol is big-endian; forgetting this is the classic bug.
- **Length check first** → raises `ValueError` on truncation rather than reading garbage.
- **`payload` carries everything left over** — the next layer parses from it.

### Bitwise field extraction — where protocol knowledge lives

VLAN TCI (Tag Control Info) packs three fields into 16 bits:

```python
pcp=(tci >> 13) & 0x7,     # top 3 bits  — Priority Code Point
dei=(tci >> 12) & 0x1,     # next bit    — Drop Eligible Indicator
vid=tci & 0xFFF,           # low 12 bits — VLAN ID
```

IPv4 does the same for version/IHL packed in one byte:

```python
version = (ver_ihl >> 4) & 0xF    # high nibble
ihl = ver_ihl & 0xF               # low nibble — header length in 32-bit words
hdr_len = ihl * 4                 # words → bytes
```

And validates before trusting:
```python
if version != 4: raise ValueError(...)   # not actually IPv4
if ihl < 5:      raise ValueError(...)   # minimum header is 5 words (20 B)
```

TCP flags decode into names via enum iteration:

```python
@property
def flag_names(self) -> list[str]:
    return [f.name for f in TCPFlag if self.flags & f]
# flags=0x12 → ["SYN", "ACK"]
```

This is exactly the kind of code that proves you've read the RFCs.

### The tricky ones

**DHCP** has a fixed 236-byte header including two large text fields nobody uses (sname 64 B + file 128 B), followed by a magic cookie and TLV options:

```python
SIZE = 236
MAGIC = 0x63825363
...
magic = struct.unpack("!I", raw[options_start:options_start+4])[0]
if magic == cls.MAGIC:                    # sanity check before walking options
    i = 0
    while i < len(opt_raw):
        opt = opt_raw[i]
        if opt == 255: break              # end-of-options marker
        if opt == 0:   i += 1; continue   # pad byte — no length field
        opt_len = opt_raw[i+1]
        if opt == 53 and opt_len == 1:    # message-type option
            msg_type = opt_raw[i+2]       # 1=DISCOVER 2=OFFER 3=REQUEST 5=ACK
        i += 2 + opt_len                  # TLV stride: tag(1) + len(1) + value
```

**VXLAN** wire layout trips everyone up — it's *not* two clean 32-bit words:

```
byte:  0        1 2 3         4 5 6     7
      [flags] [reserved 3B] [ VNI 3B ] [rsvd]
```

So the parse reads bytes directly, not via struct:

```python
flags_byte = raw[0]
vni = int.from_bytes(raw[4:7], 'big')
```

(The original version read `>>8` off a 32-bit word and produced vni=19 instead of 5000 — caught by tests.)

**WiFi 802.11** frame-control is all bitfields:

```python
type    = (fc >> 2) & 0x3     # mgmt(0)/ctrl(1)/data(2)
subtype = (fc >> 4) & 0xF     # beacon=8, QoS-data=8|type=2 ...
to_ds   = (fc >> 8) & 0x1
from_ds = (fc >> 9) & 0x1
```

Address-4 exists only when both to_ds and from_ds are set (mesh/WDS); the QoS control field only on QoS-data subtypes. The parser conditionally skips those variable regions.

### The dispatch state machine — `parse_packet()`

```python
def parse_packet(raw: bytes, timestamp: float = 0.0) -> ParsedPacket:
    pkt = ParsedPacket(timestamp=timestamp, raw=raw)
    if len(raw) < Ethernet.SIZE:
        return pkt                       # too short to even be Ethernet
    pkt.ethernet = Ethernet.parse(raw)
    payload = pkt.ethernet.payload
    etype = pkt.ethernet.ethertype

    def _safe(fn, *args):
        try:
            return fn(*args)
        except (ValueError, struct.error):
            return None                  # graceful degradation
```

Then layer by layer:
1. VLAN? (`etype == 0x8100`) unwrap it, take inner ethertype.
2. ARP? parse and done — ARP goes nowhere else.
3. IPv4 or IPv6? parse, keep `proto` number.
4. ICMP / TCP / UDP by proto number.
5. **UDP port 4789 → try VXLAN parse** (tunnel detection).
6. **GRE with proto=0x0800 → re-parse the inner IPv4 + L4** (tunnel unwrapping).
7. UDP ports 67/68 + not VXLAN → DHCP.

Every step wrapped in `_safe`, so a truncated TCP header keeps the parsed Ethernet+IP layers instead of crashing. Fuzz tests hammer random bytes at this function and assert it never raises.

### Flow keys

```python
def _set_flow_key(pkt):
    if pkt.tcp and pkt.ipv4:
        pkt.flow_key = (src_ip, dst_ip, sport, dport, 6)
    elif pkt.icmp and pkt.ipv4:
        # ICMP has no ports — use identifier/sequence as pseudo-ports
        pkt.flow_key = (src_ip, dst_ip, ident, seq, 1)
```

The 5-tuple is what everything downstream keys on: ACLs, flow tracking, per-flow shaping.

---

## 2. `core/datapath.py` (~500 lines) — The Brain

### ForwardAction + PacketMeta

```python
class ForwardAction(Enum):
    DROP, FORWARD, BRIDGE, ROUTE, TO_CPU
```

`TO_CPU` ("trap") is real switch vocabulary — packets the ASIC can't handle get punted to the CPU (TTL expiry, unknown NDP resolution).

`PacketMeta` is each packet's verdict: chosen action, egress interface, ML traffic class, offload target, flood port list, and timestamps whose difference gives `latency_us`.

### Bridge — L2 learning switch

```python
def forward(self, pkt, rx_port):
    src_mac, dst_mac = ..., vlan = ...
    self.learn(src_mac, rx_port, vlan)          # ← learning happens on ingress

    if dst_mac.startswith("ff:") ...            # broadcast/multicast
        return BRIDGE, self.flood_ports(rx_port, vlan), ...

    entry = self.lookup(dst_mac, vlan)
    if entry and entry.port_id != rx_port:  → forward to learned port
    elif entry and entry.port_id == rx_port: → DROP (hairpin)
    else:                                    → unknown unicast → flood
```

This is textbook transparent bridging: learn sources, forward/flood destinations, avoid loops-by-hairpin. MAC entries age out after 300 s of silence.

Flood respects VLANs — access ports only receive their own VLAN; trunks check their allowed list.

### Router — L3 with longest-prefix match

Routes are indexed by prefix length so LPM checks /32 first, then /31, … first hit wins:

```python
def add_route(self, prefix, ...):
    plen = int(prefix.split("/")[1])
    self._routes_by_len.setdefault(plen, []).append(entry)
    self._sorted_lens = sorted(self._routes_by_len, reverse=True)

def longest_prefix_match(self, dst_ip):
    for plen in self._sorted_lens:                      # longest first
        for route in self._routes_by_len[plen]:
            if ip in route.prefix: return route          # early exit
```

Forwarding decision mirrors a real FIB pipeline:

```python
if ipv4.ttl <= 1:        return TO_CPU, "TTL exceeded"   # would emit ICMP time-exceeded
route = lpm(dst_ip)
if not route:            return DROP, "no route"
next_hop = route.next_hop or dst_ip                       # connected vs recursive
dst_mac = self.arp_table.get(next_hop)
if not dst_mac:          return TO_CPU, "ARP miss"        # punt: send ARP request
return FORWARD, route.iface
```

### DataPathEngine — the 7-stage pipeline

```python
async def process_packet(self, pkt, ingress_iface="eth0") -> PacketMeta:
    # Stage 1 — sanity: must have Ethernet
    # Stage 2 — ACL: indexed 5-tuple match, first hit wins
    if self._check_acl_drop(...): return DROP

    # Stage 3 — flow stats + ML classification
    flow = await self.flow_table.get_or_create(5-tuple)
    await self.flow_table.update(flow, len(pkt.raw), ts)
    if len(flow.packet_sizes) >= 5:                # cold-start guard
        meta.traffic_class = await self.classifier.classify(flow)

    # Stage 4 — HW offload decision
    meta.offload_target = self.offload_engine.decide_offload(profile, flow_hash)
    if target in (HW_NIC, HW_WIFI, HW_QOS):
        self._account_hw_forwarding(pkt)           # count routed/bridged anyway!
        return FORWARD                             # fast path: skip stages 5–6

    # Stage 5 — bridge vs route
    if pkt.ipv4 or pkt.ipv6:   router path
    elif pkt.arp / pure-L2:    bridge path (learn + flood/forward)

    # Stage 6 — QoS policing
    if shaper exists for flow and bucket can't pay: return DROP

    # Stage 7 — stamp latency, update EMA stats
```

Two subtleties worth calling out:

- `_account_hw_forwarding()` — even when hardware takes the packet, real switches still know *what they did* with it (bridged or routed). Without this call, dashboards under-report routed flows whenever offload rate is high.
- Latency uses exponential moving average (`alpha=0.1`) instead of storing every sample — O(1) memory forever.

A background coroutine runs every 30 s expiring idle flows and ageing MAC entries.

ACL lookup is bucketed to avoid full scans:

```python
for key in ((proto, dst_port), None):     # exact bucket, then wildcard bucket
    for rule in self._acl_index.get(key, []): ...
```

---

## 3. `core/ml_classifier.py` (~250 lines) — Smart Traffic Management

### Feature engineering

Each flow keeps rolling deques (maxlen=100) of packet sizes and inter-arrival times. Eight features capture traffic "shape":

```python
[mean(sizes), std(sizes), min(sizes), max(sizes),
 mean(iats)*1000, std(iats)*1000,          # jitter!
 total_bytes/duration, total_packets/duration]
```

Jitter (IAT standard deviation) is the classic voice-quality metric — regular 20 ms spacing ⇒ tiny jitter ⇒ VoIP. This single feature does most of the classification work.

### Seeding without a dataset

Instead of shipping training data, the model bootstraps from Gaussian archetypes:

```python
voice = column_stack([
    normal(200, 30),      # avg size ~200 B
    normal(20, 5),        # size variance small
    full(150), full(250), # min/max
    normal(20, 2),        # IAT ~20 ms — the tell
    normal(2, 1),         # jitter ~2 ms
    normal(80000, 10000), # throughput
    normal(50, 5),        # pps
])
```

Fit StandardScaler + RandomForestClassifier(50 trees) once at startup → works immediately. `feedback()` accepts ground-truth labels later and retrains every 500 samples — that's the online-learning story.

Cold start guard: fewer than 5 packets ⇒ `BEST_EFFORT`, never guess on thin evidence.

### FlowTable

Asyncio-lock-guarded dict keyed `"src:sport-dst:dport/proto"` holding FlowFeatures + last classification. Idle >60 s ⇒ expired by the cleanup task. `list_flows()` feeds `/api/flows`.

---

## 4. `core/hw_offload.py` (~210 lines) — The Qualcomm Question

"What engine processes this packet?" — modeled after NSS/EDMA-style silicon.

Distill a ParsedPacket into a `PacketProfile`: ethertype, L4 protocol, flags for vlan/fragment/options/tunnel/encryption(ESP=proto 50)/dscp.

Complexity ladder:

```python
SIMPLE    — plain TCP/UDP/ICMP over IPv4/6
MODERATE  — VLAN, fragments, IP options, supported tunnels
COMPLEX   — tunnels when HW lacks VXLAN support
EXCEPTION — anything else (unknown ethertype/proto) → CPU slow path
```

Decision logic mirrors real flow-cache behavior:

```python
def decide_offload(self, profile, flow_hash):
    if flow_hash in self._hw_flows:              # already accelerated?
        return HW_CRYPTO if encrypted else HW_QOS if dscp>0 else HW_NIC_OFFLOAD
    if complexity == EXCEPTION: return CPU_SLOW_PATH
    if complexity == COMPLEX and not hw_vxlan: return CPU_SLOW_PATH
    if cache full:             return CPU_FAST_PATH   # software fallback
    install flow_hash in cache                        # first packet pays setup
    return HW_CRYPTO / HW_QOS / HW_NIC_OFFLOAD
```

First packet of a flow installs it in the cache; subsequent packets hit the fast path until eviction. That's precisely how Linux XDP/nftables flow offload and NSS work.

---

## 5. `core/qos.py` (~400 lines) — Traffic Management

### TokenBucket — the atom

```python
def _replenish(self):
    now = time.monotonic()
    self._tokens = min(self.burst, self._tokens + (now - self._last_time) * self.rate)

def consume(self, size):
    self._replenish()
    if self._tokens >= size: self._tokens -= size; return True
    return False
```

Uses `time.monotonic()` — immune to NTP clock jumps that would corrupt wall-clock math.

### DualTokenBucket — RFC 2697 three-color marker

Two buckets: committed (CIR/CBS) and peak (PIR/PBS).

```python
green  — fits committed bucket (contract honored)
yellow — exceeds CIR but fits PIR (best effort, may drop downstream)
red    — exceeds PIR (drop candidate)
```

### HTBScheduler — Linux `tc qdisc htb` in Python

Each class has guaranteed `rate` + borrowable `ceil`. Dequeue scans classes by priority; a class may transmit while its ceil-bucket can pay. Children hang off parents, so bandwidth hierarchies are possible.

### PriorityScheduler — strict priority + DRR

Six levels (CRITICAL→SCAVENGER). Always drain higher priority first; within a level, deficit round-robin with per-level quantum (`weight × 1500`). Prevents starvation within a class while keeping strict ordering between classes.

### FlowClassifier rules

Port-based defaults map applications to DSCP marks:

| App | Port | Priority | DSCP |
|---|---|---|---|
| SSH | 22/TCP | CRITICAL | CS6 |
| DNS | 53/UDP | CRITICAL | CS6 |
| SIP | 5060/UDP | VOICE | EF |
| HTTPS | 443/TCP | VIDEO | AF41 |
| FTP | 21/TCP | BULK | CS1 |

`TrafficManager.process()` chains: classify → police (per-flow token bucket, drop violators) → enqueue.

---

## 6. `core/generator.py` (~260 lines) — Test Traffic

`PacketCrafter` builds scapy objects for every supported protocol and returns raw bytes — deliberately decoupled so the parser never touches scapy itself (generator = scapy's job; parser = ours).

`TrafficGenerator.generate_burst(n)` picks a random crafter per packet and yields pre-parsed ParsedPackets — an async generator, so consumers `async for` over it. `generate_stream(callback)` adds rate pacing via `asyncio.sleep(1/rate_pps)`.

Also includes `replay_pcap()` for feeding real captures through the pipeline.

---

## 7. `api/app.py` (~240 lines) — Telemetry Surface

Module-level singletons (one shared engine instance):

```python
datapath  = DataPathEngine(enable_ml=True, enable_hw_offload=True, enable_qos=True)
analytics = AnalyticsEngine()     # protocol counters + 10 s sliding window
```

Endpoints:

- `GET /api/stats` — one JSON blob: datapath counters, QoS stats, analytics, ML flow-table size, offload stats
- `GET /api/flows` — iterates the ML FlowTable, includes predicted `traffic_class`
- `POST /api/acl` / `POST /api/qos/policy` — live rule injection (try it while watching the dashboard)
- `WS /ws/live` — pushes the same stats blob every 100 ms; this drives the dashboard animation

Lifespan hook starts the cleanup task; CORS wide-open for local dev.

---

## 8. `web/src/` — React Dashboard

- `context.tsx` — WebSocket client with 3-second auto-reconnect; parses stats frames into React state; polls `/api/flows` every 2 s; exposes `startGenerator/stopGenerator`.
- `components.tsx` — pure presentational pieces: StatCard, protocol distribution bars, throughput gauge, QoS queue bars, ML panel, HW-offload gauge.
- `App.tsx` — dark-theme layout composing everything; the Start/Stop button POSTs to the API which spawns/stops the generator task server-side.

Data flow is fully push-driven: generator → datapath → stats snapshot → WebSocket → React state → render, at 10 Hz.

---

## 9. Tests — What Each Suite Proves

| File | Proves |
|---|---|
| `test_parser.py` | Every header field decodes correctly from hand-built hex; **Hypothesis fuzzing**: random bytes never crash the parser |
| `test_datapath.py` | MAC learning works; unknown unicast floods; broadcast excludes sender; hairpin drops; LPM picks longest prefix; TTL≤1 traps; ACL blocks matching 5-tuples |
| `test_qos.py` | Token bucket exhausts then replenishes; consume_or_wait computes deficit time; three-color marker green/yellow/red; HTB respects burst; strict priority order |
| `test_ml_hw.py` | Feature vectors zero out cold, populate warm; classifier returns valid classes; feedback doesn't crash model; offload cache installs/hits/full-falls-back; VXLAN/VLAN profiles detected |

Run: `pytest tests/ -v` → 69 passing.

---

## 10. Extending It — Suggested First PRs

1. **Live NIC capture** — replace/augment the generator with `scapy.all.sniff()` behind the same async interface.
2. **pyroute2 integration** — create veth pairs, sync Router FIB with kernel routes (dependency already present).
3. **IPFIX export** — FlowTable already has everything an IPFIX template needs; add a UDP exporter task.
4. **Wire ML classes into HTB** — currently ML class and QoS scheduling run in parallel; map TrafficClass → HTBClass so classified VoIP literally lands in the voice queue.
5. **Prometheus endpoint** — expose the same stats dict as metrics for Grafana.
