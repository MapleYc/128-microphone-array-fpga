# Shared RAW validation and lock-free construction boundary

This independent candidate changes no wire format and opens no device merely by import. The running E package/PID5664 was not modified.

## One canonical parent-side decode

Import from **`monitor.raw128_receiver`** everywhere; do not load a second module under the short `raw128_receiver` name, because the opaque validated type belongs to its canonical module.

```python
try:
    view = decode_transport_packet(data)
except ValueError as error:
    receiver.reject_malformed(error)
    bridge.reject_malformed_raw(error, received_at)
else:
    receiver.feed_decoded(view, received_at)
    bridge.consume_decoded_datagram(view, received_at)
```

`RawPacketView.raw` is immutable original bytes. `metadata` is a read-only MappingProxy containing the original strict header/frame fields without `pcm24le`. Direct/fake construction and mutable views are rejected. The decoder still enforces length, complete format, known flags and all three consecutive frame IDs; per-consumer inter-packet continuity policies remain unchanged.

Old `decode_packet(data)` returns the complete original dict, including PCM, and `Raw128Receiver.feed(data)`/`BoardLockAdapter.consume_datagram(data)` remain compatible. `materialize_packet(view)` produces that complete dict on demand. A thread DSP receiving `ValidatedDatagram(raw, metadata)` must join the three PCM slices at its own consumption stage if `pcm24le` is absent. Process mode continues transporting unchanged raw bytes, not pickling MappingProxy objects. Diagnostic recording, if explicitly enabled elsewhere, materializes PCM only when writing; current no-media mode never does so.

## Construction reservation

The bridge reserves generation, target key and router ticket while holding its short authority lock. Factory allocation, process spawn and native preparation occur outside that lock. Publication requires the same reservation, generation, target, fresh status and router ticket. No candidate accepts RAW until publication/readiness.

The candidate router proxy guards registration/installation/prepare and signals its exact stale owner on cancellation. A superseded candidate must finish native/process retirement before the next candidate is constructed. This requires the matching router's optional `expected_ticket` support on `request_pc_quiesce` and `flush_pc`; it prevents an old cleanup after a source ABA from advancing the new source ticket.

## Diagnostic interfaces

- `snapshot()` delegates to `snapshot_compact()`: control facts plus child compact/health; no retired full audit copy.
- `snapshot_full_cached()`: explicit forensic path. With a recorded DSP failure, it returns the matching closed failed-generation cache, even if a healthy new generation is active. `fault_forensic_pending` remains true until the complete owner/process close barrier and one final audit collection; native-close alone is insufficient. Normal later retirements cannot overwrite this cache.
- `first_transport_failure` is the current segment's first fault; `transport_fault_history` retains the last16 segment-first immutable facts. Burst faults still increment counters but do not overwrite the first. New healthy admission/operator retry may clear the current latch, not the history.
- Callback wall time above10ms is diagnostic only and includes scheduling. The latch records both monotonic receipt and performance-counter duration without pretending those Windows clock origins are identical. No timing observation itself bypasses continuity or injects audio.

## Verification

`SYSTEM_TRANSPORT_TARGETED_FINAL.log`: 64 pure/fake tests pass, including shared decode compatibility/strictness, no PCM construction in nonrecord ingress, cancellation before registration, NO/epoch/target jumps, router ABA, cold native preparation cancellation, real retirement barriers, compact-vs-full audit identity and existing board/startup/retry coverage. No actual GUI, network, serial, WinMM device or media was used. The historical minimal Route gain fixture was given only a stable ticket method; the original failure log is preserved.

These tests establish functional control/transport contracts, not 60-second real-time or physical-board acceptance. The parent owns the combined single-mic/enhanced × GUI-on/off matrix and live scheduling measurements.
