const fs = require('fs');
const path = require('path');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const { AUTH_STORE_PATH } = require('./config');

let sock = null;
let connectionState = 'disconnected'; // disconnected | connecting | open

// In-memory message store — populated from Baileys history sync events
const messageStore = new Map(); // jid -> Map<msgId, WAMessage>

function getMessageStore() {
  return {
    async loadMessages(jid, count) {
      const inner = messageStore.get(jid);
      if (!inner) return [];
      return Array.from(inner.values())
        .sort((a, b) => (a.messageTimestamp || 0) - (b.messageTimestamp || 0))
        .slice(-count);
    },
    getStoreStats() {
      const stats = {};
      for (const [jid, msgs] of messageStore) {
        stats[jid] = msgs.size;
      }
      return { totalChats: messageStore.size, chats: stats };
    },
  };
}

async function createConnection({ onQR } = {}) {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_STORE_PATH);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    syncFullHistory: true,
    logger: require('pino')({ level: 'warn' }),
  });

  sock.ev.on('creds.update', saveCreds);

  // Persistent state tracking: the promise handler below unsubscribes once the
  // connection opens, so without this a later close would leave connectionState
  // stuck at 'open' with a dead socket.
  const mySock = sock;
  sock.ev.on('connection.update', ({ connection }) => {
    if (connection === 'open' && sock === mySock) {
      connectionState = 'open';
    } else if (connection === 'close' && sock === mySock) {
      connectionState = 'disconnected';
      sock = null;
    }
  });

  // Collect messages from history sync into the in-memory store
  sock.ev.on('messaging-history.set', ({ messages } = {}) => {
    for (const msg of (messages || [])) {
      const jid = msg.key.remoteJid;
      if (!jid) continue;
      if (!messageStore.has(jid)) messageStore.set(jid, new Map());
      messageStore.get(jid).set(msg.key.id, msg);
    }
  });

  // Collect new incoming messages
  sock.ev.on('messages.upsert', ({ messages } = {}) => {
    for (const msg of (messages || [])) {
      const jid = msg.key.remoteJid;
      if (!jid) continue;
      if (!messageStore.has(jid)) messageStore.set(jid, new Map());
      messageStore.get(jid).set(msg.key.id, msg);
    }
  });

  return new Promise((resolve, reject) => {
    const onUpdate = (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr && onQR) {
        onQR(qr);
      }

      if (connection === 'open') {
        mySock.ev.off('connection.update', onUpdate);
        console.error('WhatsApp connection established');
        resolve(mySock);
      }

      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        mySock.ev.off('connection.update', onUpdate);

        if (statusCode === DisconnectReason.loggedOut) {
          reject(new Error('Logged out. Please re-authenticate.'));
        } else {
          reject(new Error(`Connection closed (code ${statusCode}): ${lastDisconnect?.error?.message || 'unknown'} | ${JSON.stringify(lastDisconnect?.error?.output || {})}`));
        }
      }
    };

    mySock.ev.on('connection.update', onUpdate);
    connectionState = 'connecting';
  });
}

// Caller must ensure WhatsApp is authenticated. If no auth credentials exist,
// this will hang waiting for QR scan that never happens. Use whatsapp-auth-status to verify first.
async function getConnection() {
  if (sock && connectionState === 'open') {
    return sock;
  }
  return createConnection();
}

function getStatus() {
  return {
    authenticated: connectionState === 'open',
    state: connectionState,
  };
}

// True when a previously paired session exists on disk (creds.json with an
// account id). Says nothing about whether the socket is currently connected.
function hasSavedCredentials() {
  try {
    const creds = JSON.parse(fs.readFileSync(path.join(AUTH_STORE_PATH, 'creds.json'), 'utf8'));
    return !!(creds && creds.me && creds.me.id);
  } catch {
    return false;
  }
}

// Verified status for unattended callers: the in-process state starts
// 'disconnected' even when valid credentials exist, so attempt a real
// connection (bounded by timeoutMs) before reporting. Never triggers a QR
// wait — if no saved credentials exist it reports without connecting.
async function verifyStatus({ timeoutMs = 30000 } = {}) {
  if (sock && connectionState === 'open') {
    return getStatus();
  }
  if (!hasSavedCredentials()) {
    return { authenticated: false, state: 'no-credentials' };
  }
  let timer;
  try {
    await Promise.race([
      getConnection(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`connection not open within ${timeoutMs}ms`)), timeoutMs);
      }),
    ]);
    return getStatus();
  } catch (err) {
    return { ...getStatus(), error: err.message };
  } finally {
    clearTimeout(timer);
  }
}

function disconnect() {
  if (sock) {
    sock.end();
    sock = null;
  }
  connectionState = 'disconnected';
}

module.exports = { getConnection, getStatus, verifyStatus, hasSavedCredentials, disconnect, createConnection, getMessageStore };
