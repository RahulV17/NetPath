/**
 * LiveBridge — mounts when the user attaches the Live Engine.
 *
 * On attach: POSTs /api/generator/start so the backend has traffic to
 * report (otherwise /ws/live streams zeros), then subscribes to
 * /ws/live (~10 Hz) and maps snapshots into the lab store. The local
 * sim keeps running for visuals; displayed numbers become the backend's.
 */
import { useEffect } from 'react'
import { useLab } from './store'
import { LiveEngineClient, dominantClass, mapToReadouts } from './liveEngine'

export function useLiveEngineBridge() {
  const attached = useLab((s) => s.liveEngineAttached)

  useEffect(() => {
    if (!attached) return

    // Ensure the backend is actually generating traffic — otherwise
    // every stat reads 0 and "real speed" looks broken.
    fetch('http://localhost:8000/api/generator/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rate_pps: 100, duration: 3600 }),
    }).catch(() => {
      /* backend may already be generating; WS will still connect */
    })

    const client = new LiveEngineClient(
      (snap) => {
        const s = useLab.getState()
        s.setLiveEngineStatus('live')
        s.pushReadouts(mapToReadouts(snap))
        const pps = snap.analytics?.throughput?.pps ?? 0
        // Real per-flow telemetry from FlowTable.snapshot
        const flowCount = snap.flows?.count ?? snap.ml?.flow_table_size ?? 0
        const domClass = dominantClass(snap.flows?.classified)
        const bps = snap.analytics?.throughput?.bytes_per_second
        const gbps = bps != null
          ? Number(((bps * 8) / 1e9).toFixed(3))
          : Number(((pps * 1500 * 8) / 1e9).toFixed(3))
        useLab.setState({
          throughputGbps: gbps,
          activeFlows: flowCount,
          liveFlowCount: flowCount,
          liveDominantClass: domClass,
          livePps: Math.round(pps),
        })
      },
      (connected) => {
        // Ignore status flips after manual detach — otherwise disconnect
        // fires onStatus(false) → 'connecting' and the UI sticks there.
        if (!useLab.getState().liveEngineAttached) return
        useLab.getState().setLiveEngineStatus(connected ? 'live' : 'connecting')
      },
    )

    client.connect()
    return () => {
      client.disconnect()
      useLab.setState({ liveEngineStatus: 'off' })
    }
  }, [attached])
}
