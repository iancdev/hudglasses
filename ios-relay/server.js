/* eslint-disable no-console */
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");

const next = require("next");
const WebSocket = require("ws");

const dev = process.env.NODE_ENV !== "production";
const host = process.env.HOST || "0.0.0.0";
const port = Number(process.env.PORT || 3000);

const useHttps = String(process.env.RELAY_HTTPS || "").trim() === "1";
const certDir = process.env.RELAY_CERT_DIR || path.join(__dirname, ".cert");
const certPath = process.env.RELAY_CERT_PATH || path.join(certDir, "cert.pem");
const keyPath = process.env.RELAY_KEY_PATH || path.join(certDir, "key.pem");

function sendJson(ws, obj) {
  try {
    ws.send(JSON.stringify(obj));
  } catch {}
}

async function main() {
  const app = next({ dev, hostname: host, port });
  const handle = app.getRequestHandler();
  await app.prepare();

  let server;
  if (useHttps) {
    if (!fs.existsSync(certPath) || !fs.existsSync(keyPath)) {
      console.error(`Missing TLS certs for iOS mic access.\nExpected:\n- ${certPath}\n- ${keyPath}`);
      console.error("Create them (locally) and re-run: RELAY_HTTPS=1 node server.js");
      process.exit(1);
    }
    const cert = fs.readFileSync(certPath);
    const key = fs.readFileSync(keyPath);
    server = https.createServer({ cert, key }, (req, res) => handle(req, res));
  } else {
    server = http.createServer((req, res) => handle(req, res));
  }

  const wss = new WebSocket.WebSocketServer({ noServer: true });
  server.on("upgrade", (req, socket, head) => {
    try {
      const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
      if (url.pathname !== "/ws") {
        socket.destroy();
        return;
      }
      wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
    } catch {
      socket.destroy();
    }
  });

  wss.on("connection", (ws) => {
    let left = null;
    let right = null;
    let ready = false;
    let expectedBytes = 640;

    const cleanup = () => {
      ready = false;
      for (const c of [left, right]) {
        if (!c) continue;
        try {
          c.close();
        } catch {}
      }
      left = null;
      right = null;
    };

    const connectToHudServer = async (cfg) => {
      cleanup();
      const serverIp = String(cfg.serverIp || "").trim() || "127.0.0.1";
      const serverPort = Number(cfg.serverPort || 8765);
      const baseId = String(cfg.deviceIdBase || "ios-relay").trim() || "ios-relay";
      const audio = cfg.audio || {};
      const sampleRateHz = Number(audio.sampleRateHz || 16000);
      const frameMs = Number(audio.frameMs || 20);
      expectedBytes = Math.round(sampleRateHz * (frameMs / 1000) * 2);

      const mkClient = (role) => {
        const deviceId = `${baseId}-${role}`;
        const uri = `ws://${serverIp}:${serverPort}/esp32/audio?deviceId=${encodeURIComponent(deviceId)}&role=${encodeURIComponent(role)}`;
        const client = new WebSocket(uri, { handshakeTimeout: 5000, maxPayload: 2 * 1024 * 1024 });
        client.on("open", () => {
          const hello = {
            v: 1,
            type: "hello",
            deviceId,
            role,
            fwVersion: "ios-relay",
            audio: { format: "pcm_s16le", sampleRateHz, channels: 1, frameMs },
          };
          client.send(JSON.stringify(hello));
        });
        client.on("close", () => {
          ready = false;
          sendJson(ws, { type: "status", state: "hudserver_disconnected", message: role });
        });
        client.on("error", (e) => {
          ready = false;
          sendJson(ws, { type: "error", message: `hudserver ${role} ws error: ${String(e?.message || e)}` });
        });
        return client;
      };

      sendJson(ws, { type: "status", state: "connecting_hudserver", message: `${serverIp}:${serverPort}` });

      left = mkClient("left");
      right = mkClient("right");

      await new Promise((resolve, reject) => {
        const deadline = setTimeout(() => reject(new Error("timeout connecting to hudserver")), 6000);
        const check = () => {
          if (!left || !right) return;
          if (left.readyState === WebSocket.OPEN && right.readyState === WebSocket.OPEN) {
            clearTimeout(deadline);
            resolve();
          }
        };
        left.on("open", check);
        right.on("open", check);
        left.on("error", () => {});
        right.on("error", () => {});
        check();
      });

      ready = true;
      sendJson(ws, { type: "ready" });
    };

    ws.on("message", async (data, isBinary) => {
      if (!isBinary) {
        let obj = null;
        try {
          obj = JSON.parse(String(data));
        } catch {
          return;
        }
        if (!obj || typeof obj !== "object") return;
        if (obj.type === "start") {
          try {
            await connectToHudServer(obj);
          } catch (e) {
            sendJson(ws, { type: "error", message: String(e?.message || e) });
          }
        } else if (obj.type === "stop") {
          cleanup();
        }
        return;
      }

      if (!ready) return;
      try {
        const monoBytes = expectedBytes;
        const stereoBytes = expectedBytes * 2;

        if (monoBytes > 0 && data.length !== monoBytes && data.length !== stereoBytes) {
          sendJson(ws, {
            type: "status",
            state: "unexpected_frame_bytes",
            message: `${data.length} (expected ${monoBytes} or ${stereoBytes})`,
          });
          return;
        }

        if (data.length === stereoBytes) {
          const l = data.subarray(0, monoBytes);
          const r = data.subarray(monoBytes, monoBytes + monoBytes);
          if (left && left.readyState === WebSocket.OPEN) left.send(l);
          if (right && right.readyState === WebSocket.OPEN) right.send(r);
        } else {
          // Mono fallback: duplicate to both roles.
          if (left && left.readyState === WebSocket.OPEN) left.send(data);
          if (right && right.readyState === WebSocket.OPEN) right.send(data);
        }
      } catch {}
    });

    ws.on("close", () => cleanup());
    ws.on("error", () => cleanup());
  });

  server.listen(port, host, () => {
    const proto = useHttps ? "https" : "http";
    console.log(`ios-relay listening on ${proto}://${host}:${port}`);
    console.log(`ws relay on ${useHttps ? "wss" : "ws"}://${host}:${port}/ws`);
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
