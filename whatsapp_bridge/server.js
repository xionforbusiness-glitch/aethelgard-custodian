#!/usr/bin/env node
'use strict';

const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const pino = require('pino');
const { Boom } = require('@hapi/boom');
const {
  default: makeWASocket,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} = require('@whiskeysockets/baileys');

const PORT = parseInt(process.env.WHATSAPP_BRIDGE_PORT || '20135', 10);
const HOST = '127.0.0.1';
const MAX_RING_MESSAGES = 500;

function resolveCredDir() {
  if (process.env.AETHELGARD_CREDENTIALS_DIR) {
    return path.resolve(process.env.AETHELGARD_CREDENTIALS_DIR);
  }
  const home = process.env.AETHELGARD_HOME || path.join(os.homedir(), '.aethelgard');
  return path.join(home, 'credentials');
}

function resolveSessionsDir() {
  const home = process.env.AETHELGARD_HOME || path.join(os.homedir(), '.aethelgard');
  return path.join(home, 'sessions');
}

const AUTH_DIR = path.join(resolveCredDir(), 'whatsapp_auth');
const MSG_STORE_PATH = path.join(resolveSessionsDir(), 'whatsapp_messages.json');
fs.mkdirSync(AUTH_DIR, { recursive: true });
fs.mkdirSync(path.dirname(MSG_STORE_PATH), { recursive: true });

const logger = pino({ level: process.env.WA_LOG_LEVEL || 'silent' });

let sock = null;
let connectionState = 'initializing';
let connectedJid = '';
let connectedPhone = '';
let lastQr = '';
let lastPairingCode = '';
let lastError = '';
let messageRing = [];

function loadRing() {
  try {
    if (fs.existsSync(MSG_STORE_PATH)) {
      const parsed = JSON.parse(fs.readFileSync(MSG_STORE_PATH, 'utf8'));
      if (Array.isArray(parsed)) {
        messageRing = parsed.slice(-MAX_RING_MESSAGES);
      }
    }
  } catch (_) {
    messageRing = [];
  }
}

function saveRing() {
  try {
    const tmp = `${MSG_STORE_PATH}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(messageRing.slice(-MAX_RING_MESSAGES), null, 2), 'utf8');
    fs.renameSync(tmp, MSG_STORE_PATH);
  } catch (_) {}
}

function appendMessage(entry) {
  messageRing.push(entry);
  if (messageRing.length > MAX_RING_MESSAGES) {
    messageRing = messageRing.slice(-MAX_RING_MESSAGES);
  }
  saveRing();
}

function extractText(msgObj) {
  const m = (msgObj && msgObj.message) || {};
  return (
    m.conversation ||
    (m.extendedTextMessage && m.extendedTextMessage.text) ||
    (m.imageMessage && m.imageMessage.caption) ||
    (m.videoMessage && m.videoMessage.caption) ||
    (m.documentMessage && m.documentMessage.caption) ||
    ''
  );
}

function normalizePhoneToJid(target) {
  const raw = String(target || '').trim();
  if (!raw) return '';
  if (raw.includes('@')) return raw;
  const digits = raw.replace(/[^0-9]/g, '');
  return digits ? `${digits}@s.whatsapp.net` : raw;
}

async function startSocket() {
  loadRing();
  try {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    let version;
    try {
      const verRes = await fetchLatestBaileysVersion();
      version = verRes.version;
    } catch (_) {
      version = undefined;
    }

    sock = makeWASocket({
      version,
      logger,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
      browser: ['Aethelgard', 'Chrome', '120.0'],
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;
      if (qr) {
        lastQr = qr;
        connectionState = 'awaiting_pair';
      }
      if (connection === 'open') {
        connectionState = 'connected';
        lastQr = '';
        lastError = '';
        connectedJid = (sock.user && sock.user.id) || '';
        connectedPhone = connectedJid.split(':')[0].split('@')[0] || '';
      } else if (connection === 'close') {
        const statusCode =
          lastDisconnect && lastDisconnect.error
            ? new Boom(lastDisconnect.error).output?.statusCode
            : null;
        lastError = (lastDisconnect && lastDisconnect.error && lastDisconnect.error.message) || 'disconnected';
        if (statusCode === DisconnectReason.loggedOut) {
          connectionState = 'logged_out';
          connectedJid = '';
          connectedPhone = '';
        } else {
          connectionState = 'reconnecting';
          setTimeout(() => startSocket(), 4000);
        }
      }
    });

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      if (!Array.isArray(messages)) return;
      for (const msg of messages) {
        if (!msg || !msg.key) continue;
        const text = extractText(msg);
        if (!text) continue;
        const remoteJid = msg.key.remoteJid || '';
        if (remoteJid === 'status@broadcast') continue;
        appendMessage({
          id: msg.key.id || '',
          from: remoteJid,
          sender_name: msg.pushName || remoteJid.split('@')[0],
          from_me: Boolean(msg.key.fromMe),
          unread: !Boolean(msg.key.fromMe),
          text,
          upsert_type: type || 'notify',
          timestamp: msg.messageTimestamp
            ? new Date(Number(msg.messageTimestamp) * 1000).toISOString()
            : new Date().toISOString(),
        });
      }
    });
  } catch (err) {
    connectionState = 'error';
    lastError = String(err && err.message ? err.message : err);
    setTimeout(() => startSocket(), 8000);
  }
}

function readJsonBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf8');
        resolve(raw ? JSON.parse(raw) : {});
      } catch (_) {
        resolve({});
      }
    });
  });
}

function sendJson(res, status, obj) {
  const payload = Buffer.from(JSON.stringify(obj), 'utf8');
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': String(payload.length),
  });
  res.end(payload);
}

const server = http.createServer(async (req, res) => {
  const urlObj = new URL(req.url, `http://${HOST}:${PORT}`);
  const pathname = urlObj.pathname;

  if (req.method === 'GET' && pathname === '/status') {
    return sendJson(res, 200, {
      ok: true,
      connected: connectionState === 'connected',
      state: connectionState,
      jid: connectedJid,
      phone: connectedPhone,
      last_qr: lastQr,
      pairing_code: lastPairingCode,
      last_error: lastError,
      buffered_messages: messageRing.length,
    });
  }

  if (req.method === 'POST' && pathname === '/pair') {
    const body = await readJsonBody(req);
    const digits = String(body.phone || '').replace(/[^0-9]/g, '');
    if (!sock) {
      return sendJson(res, 503, { success: false, error: 'WhatsApp socket not initialized yet' });
    }
    if (connectionState === 'connected') {
      return sendJson(res, 200, {
        success: true,
        already_connected: true,
        phone: connectedPhone,
        jid: connectedJid,
      });
    }
    if (digits && typeof sock.requestPairingCode === 'function') {
      try {
        const code = await sock.requestPairingCode(digits);
        lastPairingCode = String(code || '').trim();
        return sendJson(res, 200, {
          success: true,
          pairing_code: lastPairingCode,
          qr_data: lastQr,
          state: connectionState,
        });
      } catch (err) {
        return sendJson(res, 500, {
          success: false,
          error: String(err && err.message ? err.message : err),
          qr_data: lastQr,
        });
      }
    }
    return sendJson(res, 200, {
      success: Boolean(lastQr || lastPairingCode),
      pairing_code: lastPairingCode,
      qr_data: lastQr,
      state: connectionState,
    });
  }

  if (req.method === 'GET' && pathname === '/messages') {
    const limit = Math.max(1, Math.min(parseInt(urlObj.searchParams.get('limit') || '20', 10), 100));
    const unreadOnly = urlObj.searchParams.get('unread_only') === '1';
    const query = (urlObj.searchParams.get('query') || '').trim().toLowerCase();
    let list = messageRing.slice().reverse();
    if (unreadOnly) list = list.filter((m) => m.unread);
    if (query) {
      list = list.filter(
        (m) =>
          String(m.text || '').toLowerCase().includes(query) ||
          String(m.sender_name || '').toLowerCase().includes(query) ||
          String(m.from || '').toLowerCase().includes(query)
      );
    }
    return sendJson(res, 200, {
      ok: true,
      connected: connectionState === 'connected',
      messages: list.slice(0, limit),
    });
  }

  if (req.method === 'POST' && pathname === '/send') {
    const body = await readJsonBody(req);
    const to = normalizePhoneToJid(body.to);
    const text = String(body.body || body.text || '').trim();
    if (!to || !text) {
      return sendJson(res, 400, { success: false, error: 'Missing to or body' });
    }
    if (!sock || connectionState !== 'connected') {
      return sendJson(res, 503, {
        success: false,
        error: `WhatsApp bridge is ${connectionState}. Run /connect whatsapp <phone> first.`,
      });
    }
    try {
      const sent = await sock.sendMessage(to, { text });
      const entry = {
        id: (sent && sent.key && sent.key.id) || `wa_${Date.now()}`,
        from: to,
        sender_name: 'Omar (Aethelgard)',
        from_me: true,
        unread: false,
        text,
        timestamp: new Date().toISOString(),
      };
      appendMessage(entry);
      return sendJson(res, 200, {
        success: true,
        platform: 'whatsapp',
        to,
        message_id: entry.id,
        sent_at: entry.timestamp,
      });
    } catch (err) {
      return sendJson(res, 500, {
        success: false,
        error: String(err && err.message ? err.message : err),
      });
    }
  }

  return sendJson(res, 404, { error: 'Not found' });
});

loadRing();
server.listen(PORT, HOST, () => {
  console.log(`[WhatsAppBridge] Listening on http://${HOST}:${PORT}`);
  startSocket();
});
