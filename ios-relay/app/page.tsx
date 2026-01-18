"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const DEFAULT_SERVER_IP = "10.19.130.231";
const DEFAULT_SERVER_PORT = 8765;

type RelayStatus =
  | { type: "status"; state: string; message?: string }
  | { type: "ready" }
  | { type: "error"; message: string };

type RoleMode = "both" | "left" | "right";

function clamp01(x: number): number {
  if (x < -1) return -1;
  if (x > 1) return 1;
  return x;
}

function floatToPcm16leBytes(frame: Float32Array): Uint8Array {
  const out = new Int16Array(frame.length);
  for (let i = 0; i < frame.length; i++) {
    const s = clamp01(frame[i]);
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return new Uint8Array(out.buffer);
}

function createLinearResampler(inRate: number, outRate: number) {
  const ratio = inRate / outRate;
  let inBuf = new Float32Array(0);
  let inPos = 0; // fractional index into inBuf

  return (chunk: Float32Array): Float32Array => {
    const inPosInt = Math.floor(inPos);
    const leftover = inBuf.subarray(inPosInt);
    const merged = new Float32Array(leftover.length + chunk.length);
    merged.set(leftover, 0);
    merged.set(chunk, leftover.length);
    inBuf = merged;
    inPos = inPos - inPosInt;

    const maxOut = Math.floor((inBuf.length - 1 - inPos) / ratio);
    if (maxOut <= 0) return new Float32Array(0);

    const out = new Float32Array(maxOut);
    for (let i = 0; i < maxOut; i++) {
      const idx = Math.floor(inPos);
      const frac = inPos - idx;
      const s0 = inBuf[idx] ?? 0;
      const s1 = inBuf[idx + 1] ?? s0;
      out[i] = s0 + (s1 - s0) * frac;
      inPos += ratio;
    }
    return out;
  };
}

export default function Page() {
  const [serverIp, setServerIp] = useState(DEFAULT_SERVER_IP);
  const [serverPort, setServerPort] = useState(String(DEFAULT_SERVER_PORT));
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("ios-relay");
  const [selectedMicId, setSelectedMicId] = useState<string>("");
  const [roleMode, setRoleMode] = useState<RoleMode>("both");
  const [log, setLog] = useState<string>("Idle.");
  const [streaming, setStreaming] = useState(false);
  const [inputSampleRate, setInputSampleRate] = useState<number | null>(null);
  const [inputChannels, setInputChannels] = useState<number | null>(null);
  const [lastRmsLeft, setLastRmsLeft] = useState<number | null>(null);
  const [lastRmsRight, setLastRmsRight] = useState<number | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const rmsLeftRef = useRef<number>(0);
  const rmsRightRef = useRef<number>(0);
  const rmsLogLastMsRef = useRef<number>(0);
  const silentRightDupRef = useRef<boolean>(false);

  const targetSampleRate = 16000;
  const frameMs = 20;
  const frameSamples = useMemo(() => Math.round((targetSampleRate * frameMs) / 1000), []);

  const addLog = useCallback((line: string) => {
    setLog((prev) => {
      const next = `${new Date().toLocaleTimeString()}  ${line}\n${prev}`;
      return next.slice(0, 8000);
    });
  }, []);

  const refreshDevices = useCallback(async () => {
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      setDevices(list.filter((d) => d.kind === "audioinput"));
    } catch (e) {
      addLog(`enumerateDevices failed: ${String(e)}`);
    }
  }, [addLog]);

  useEffect(() => {
    const savedIp = localStorage.getItem("iosRelay.serverIp");
    const savedPort = localStorage.getItem("iosRelay.serverPort");
    const savedMic = localStorage.getItem("iosRelay.micId");
    const savedDeviceId = localStorage.getItem("iosRelay.deviceId");
    const savedRoleMode = localStorage.getItem("iosRelay.roleMode");
    if (savedIp) setServerIp(savedIp);
    if (savedPort) setServerPort(savedPort);
    if (savedMic) setSelectedMicId(savedMic);
    if (savedDeviceId) setDeviceId(savedDeviceId);
    if (savedRoleMode === "both" || savedRoleMode === "left" || savedRoleMode === "right") {
      setRoleMode(savedRoleMode);
    }
    void refreshDevices();
  }, [refreshDevices]);

  useEffect(() => {
    localStorage.setItem("iosRelay.serverIp", serverIp);
  }, [serverIp]);
  useEffect(() => {
    localStorage.setItem("iosRelay.serverPort", serverPort);
  }, [serverPort]);
  useEffect(() => {
    localStorage.setItem("iosRelay.micId", selectedMicId);
  }, [selectedMicId]);
  useEffect(() => {
    localStorage.setItem("iosRelay.deviceId", deviceId);
  }, [deviceId]);
  useEffect(() => {
    localStorage.setItem("iosRelay.roleMode", roleMode);
  }, [roleMode]);

  const stop = useCallback(() => {
    setStreaming(false);
    if (wsRef.current) {
      try {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      } catch {}
      try {
        wsRef.current.close();
      } catch {}
    }
    wsRef.current = null;

    if (processorRef.current) {
      try {
        processorRef.current.disconnect();
      } catch {}
    }
    processorRef.current = null;

    if (gainRef.current) {
      try {
        gainRef.current.disconnect();
      } catch {}
    }
    gainRef.current = null;

    if (audioCtxRef.current) {
      try {
        void audioCtxRef.current.close();
      } catch {}
    }
    audioCtxRef.current = null;

    if (mediaStreamRef.current) {
      for (const t of mediaStreamRef.current.getTracks()) {
        try {
          t.stop();
        } catch {}
      }
    }
    mediaStreamRef.current = null;

    addLog("Stopped.");
  }, [addLog]);

  const start = useCallback(async () => {
    if (streaming) return;
    try {
      setLog("Starting…\n");
      addLog("Opening relay WebSocket…");

      const relayProto = window.location.protocol === "https:" ? "wss" : "ws";
      const relayUrl = `${relayProto}://${window.location.host}/ws`;

      const ws = new WebSocket(relayUrl);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      let relayReadyResolve: (() => void) | null = null;
      const relayReady = new Promise<void>((resolve) => {
        relayReadyResolve = resolve;
      });

      ws.onmessage = (evt) => {
        if (typeof evt.data !== "string") return;
        let msg: RelayStatus | null = null;
        try {
          msg = JSON.parse(evt.data) as RelayStatus;
        } catch {
          return;
        }
        if (msg.type === "status") {
          addLog(`Relay: ${msg.state}${msg.message ? ` (${msg.message})` : ""}`);
        } else if (msg.type === "ready") {
          addLog("Relay: ready.");
          relayReadyResolve?.();
          relayReadyResolve = null;
        } else if (msg.type === "error") {
          addLog(`Relay error: ${msg.message}`);
        }
      };
      ws.onerror = () => addLog("Relay WS error.");
      ws.onclose = () => addLog("Relay WS closed.");

      await new Promise<void>((resolve, reject) => {
        const onOpen = () => {
          ws.removeEventListener("open", onOpen);
          ws.removeEventListener("error", onError);
          resolve();
        };
        const onError = () => {
          ws.removeEventListener("open", onOpen);
          ws.removeEventListener("error", onError);
          reject(new Error("relay ws open failed"));
        };
        ws.addEventListener("open", onOpen);
        ws.addEventListener("error", onError);
      });

      ws.send(
        JSON.stringify({
          type: "start",
          serverIp,
          serverPort: Number(serverPort),
          deviceIdBase: deviceId,
          roles: roleMode,
          audio: {
            format: "pcm_s16le",
            sampleRateHz: targetSampleRate,
            channels: roleMode === "both" ? 2 : 1,
            frameMs,
          },
        })
      );

      addLog("Waiting for relay to connect to hudserver…");
      await Promise.race([
        relayReady,
        new Promise<void>((_, reject) => setTimeout(() => reject(new Error("relay not ready (timeout)")), 8000)),
      ]);

      addLog("Requesting microphone permission…");
      try {
        const supported = navigator.mediaDevices.getSupportedConstraints?.();
        if (supported) {
          addLog(`Supported constraints: channelCount=${supported.channelCount ? "yes" : "no"}`);
        }
      } catch {}
      const gumErrorToString = (e: unknown): string => {
        if (e && typeof e === "object") {
          const anyE = e as any;
          const name = typeof anyE.name === "string" ? anyE.name : "Error";
          const message = typeof anyE.message === "string" ? anyE.message : String(e);
          const constraint =
            typeof anyE.constraint === "string"
              ? anyE.constraint
              : typeof anyE.constraintName === "string"
                ? anyE.constraintName
                : undefined;
          return constraint ? `${name}: ${message} (constraint=${constraint})` : `${name}: ${message}`;
        }
        return String(e);
      };

      const getUserMediaWithFallbacks = async (): Promise<MediaStream> => {
        const wantDeviceId = selectedMicId ? { exact: selectedMicId } : undefined;

        // NOTE: On iOS Safari, some boolean constraints (and sometimes channelCount) can trigger
        // OverconstrainedError even when provided as "ideal". Fall back progressively.
        const baseIdeal = {
          echoCancellation: { ideal: false },
          noiseSuppression: { ideal: false },
          autoGainControl: { ideal: false },
        } as const;

        const attempts: MediaStreamConstraints[] = [
          { audio: { deviceId: wantDeviceId, channelCount: { ideal: 2 }, ...baseIdeal }, video: false },
          { audio: { deviceId: wantDeviceId, ...baseIdeal }, video: false },
          { audio: { channelCount: { ideal: 2 }, ...baseIdeal }, video: false },
          { audio: { ...baseIdeal }, video: false },
          { audio: true, video: false },
        ];

        let lastErr: unknown = null;
        for (let i = 0; i < attempts.length; i++) {
          const c = attempts[i];
          try {
            addLog(`getUserMedia attempt ${i + 1}/${attempts.length}…`);
            return await navigator.mediaDevices.getUserMedia(c);
          } catch (e) {
            lastErr = e;
            const msg = gumErrorToString(e);
            addLog(`getUserMedia failed: ${msg}`);
            const name = (e as any)?.name;
            if (name === "NotAllowedError" || name === "NotFoundError") break;
            // Otherwise, keep falling back.
          }
        }
        throw lastErr;
      };

      const stream = await getUserMediaWithFallbacks();
      mediaStreamRef.current = stream;
      await refreshDevices();
      const track = stream.getAudioTracks()[0];
      if (track) {
        const settings = track.getSettings();
        if (typeof settings.channelCount === "number") setInputChannels(settings.channelCount);
        addLog(
          `Track settings: sampleRate=${typeof settings.sampleRate === "number" ? settings.sampleRate : "?"}Hz channels=${
            typeof settings.channelCount === "number" ? settings.channelCount : "?"
          }`
        );
        try {
          const caps = track.getCapabilities?.();
          const cc = caps && (caps as any).channelCount;
          if (cc && typeof cc === "object") {
            const min = typeof cc.min === "number" ? cc.min : "?";
            const max = typeof cc.max === "number" ? cc.max : "?";
            addLog(`Track capabilities: channelCount min=${min} max=${max}`);
          }
        } catch {}
      }

      const audioCtx = new AudioContext({ latencyHint: "interactive" });
      audioCtxRef.current = audioCtx;
      await audioCtx.resume();
      setInputSampleRate(audioCtx.sampleRate);
      addLog(`AudioContext sampleRate=${audioCtx.sampleRate}Hz`);

      const src = audioCtx.createMediaStreamSource(stream);
      const processor = audioCtx.createScriptProcessor(1024, 2, 2);
      const zeroGain = audioCtx.createGain();
      zeroGain.gain.value = 0;

      const resampleL = createLinearResampler(audioCtx.sampleRate, targetSampleRate);
      const resampleR = createLinearResampler(audioCtx.sampleRate, targetSampleRate);
      const leftFifo: number[] = [];
      const rightFifo: number[] = [];
      let readPos = 0;

      processor.onaudioprocess = (evt) => {
        const wsCur = wsRef.current;
        if (!wsCur || wsCur.readyState !== WebSocket.OPEN) return;

        const channels = evt.inputBuffer.numberOfChannels;
        const inL = evt.inputBuffer.getChannelData(0);
        const rawInR = channels > 1 ? evt.inputBuffer.getChannelData(1) : inL;

        const quickRms = (buf: Float32Array): number => {
          let sumSq = 0;
          let n = 0;
          for (let i = 0; i < buf.length; i += 4) {
            const x = buf[i] ?? 0;
            sumSq += x * x;
            n++;
          }
          return n ? Math.sqrt(sumSq / n) : 0;
        };

        // Some browsers (notably iOS Safari) may provide a mono mic but still present a 2‑channel buffer
        // where only channel 0 has data and channel 1 is effectively zeros. Detect that and treat as mono.
        const probeL = quickRms(inL);
        const probeR = quickRms(rawInR);
        const silentRight = channels > 1 && probeR < 1e-6 && probeL > 2e-5;
        const inR = silentRight ? inL : rawInR;
        if (silentRight !== silentRightDupRef.current) {
          silentRightDupRef.current = silentRight;
          if (silentRight) {
            addLog(
              "Right channel appears silent (likely mono mic / browser up-mix). Duplicating left -> right for streaming."
            );
          } else if (channels > 1) {
            addLog("Stereo input detected. Sending distinct left/right channels.");
          }
        }

        const outL = resampleL(inL);
        const outR = resampleR(inR);
        const n = Math.min(outL.length, outR.length);
        if (n <= 0) return;

        for (let i = 0; i < n; i++) {
          leftFifo.push(outL[i] ?? 0);
          rightFifo.push(outR[i] ?? 0);
        }

        const computeRms = (frame: Float32Array): number => {
          let sumSq = 0;
          for (let i = 0; i < frame.length; i++) {
            const x = frame[i] ?? 0;
            sumSq += x * x;
          }
          return frame.length ? Math.sqrt(sumSq / frame.length) : 0;
        };

        while (leftFifo.length - readPos >= frameSamples && rightFifo.length - readPos >= frameSamples) {
          const frameL = new Float32Array(frameSamples);
          const frameR = new Float32Array(frameSamples);
          for (let i = 0; i < frameSamples; i++) {
            frameL[i] = leftFifo[readPos + i] ?? 0;
            frameR[i] = rightFifo[readPos + i] ?? 0;
          }
          readPos += frameSamples;
          if (readPos > 8192) {
            leftFifo.splice(0, readPos);
            rightFifo.splice(0, readPos);
            readPos = 0;
          }

          rmsLeftRef.current = computeRms(frameL);
          rmsRightRef.current = computeRms(frameR);

          const pcmL = floatToPcm16leBytes(frameL);
          const pcmR = floatToPcm16leBytes(frameR);
          if (roleMode === "both") {
            const combined = new Uint8Array(pcmL.length + pcmR.length);
            combined.set(pcmL, 0);
            combined.set(pcmR, pcmL.length);
            wsCur.send(combined);
          } else if (roleMode === "right") {
            wsCur.send(pcmR);
          } else {
            wsCur.send(pcmL);
          }
        }

        const nowMs = performance.now();
        if (nowMs - rmsLogLastMsRef.current >= 500) {
          rmsLogLastMsRef.current = nowMs;
          const l = rmsLeftRef.current;
          const r = rmsRightRef.current;
          setLastRmsLeft(l);
          setLastRmsRight(r);
          addLog(`Volume RMS: L=${l.toFixed(4)} R=${r.toFixed(4)}`);
        }
      };

      src.connect(processor);
      processor.connect(zeroGain);
      zeroGain.connect(audioCtx.destination);

      processorRef.current = processor;
      gainRef.current = zeroGain;

      setStreaming(true);
      addLog("Streaming started.");
    } catch (e) {
      addLog(`Start failed: ${String(e)}`);
      stop();
    }
  }, [addLog, deviceId, frameSamples, refreshDevices, roleMode, selectedMicId, serverIp, serverPort, stop, streaming]);

  const outBytesPerMessage = roleMode === "both" ? frameSamples * 4 : frameSamples * 2;
  const outLabel = roleMode === "both" ? "L+R" : roleMode.toUpperCase();

  return (
    <main className="container">
      <div className="panel">
        <h1>iOS → hudserver audio relay</h1>

        <div className="row">
          <div>
            <label>Microphone</label>
            <select value={selectedMicId} onChange={(e) => setSelectedMicId(e.target.value)} disabled={streaming}>
              <option value="">Default</option>
              {devices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `(audioinput ${d.deviceId.slice(0, 8)})`}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Device ID (base)</label>
            <input value={deviceId} onChange={(e) => setDeviceId(e.target.value)} disabled={streaming} />
          </div>
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label>Role(s)</label>
            <select value={roleMode} onChange={(e) => setRoleMode(e.target.value as RoleMode)} disabled={streaming}>
              <option value="both">Both (L+R)</option>
              <option value="left">Left only</option>
              <option value="right">Right only</option>
            </select>
          </div>
          <div>
            <label>hudserver IP</label>
            <input value={serverIp} onChange={(e) => setServerIp(e.target.value)} disabled={streaming} />
          </div>
          <div>
            <label>hudserver port</label>
            <input value={serverPort} onChange={(e) => setServerPort(e.target.value)} disabled={streaming} inputMode="numeric" />
          </div>
        </div>

        <div className="actions">
          <button className="primary" onClick={start} disabled={streaming}>
            Start
          </button>
          <button className="secondary" onClick={stop} disabled={!streaming}>
            Stop
          </button>
          <button className="secondary" onClick={refreshDevices} disabled={streaming}>
            Refresh mics
          </button>
        </div>

        <div className="status">
          Output: <code>pcm_s16le</code> @ <code>{targetSampleRate}Hz</code>, <code>{frameMs}ms</code> frames · mode{" "}
          <code>{outLabel}</code> · bytes/message <code>{outBytesPerMessage}B</code>
          {"\n"}
          Input sampleRate: <code>{inputSampleRate ?? "?"}</code> · Input channels: <code>{inputChannels ?? "?"}</code>
          {"\n"}
          Volume RMS: <code>{lastRmsLeft?.toFixed(4) ?? "?"}</code> (L) · <code>{lastRmsRight?.toFixed(4) ?? "?"}</code> (R)
          {"\n\n"}
          {log}
        </div>
      </div>
    </main>
  );
}
