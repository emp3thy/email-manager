const fs = require('fs');
const os = require('os');
const path = require('path');

// Point AUTH_STORE_PATH at a temp dir we control per-test
const mockAuthPath = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-auth-test-'));

jest.mock('../config', () => ({
  SERVER_NAME: 'whatsapp-assistant',
  SERVER_VERSION: '1.0.0',
  AUTH_STORE_PATH: mockAuthPath,
}));

// Mock baileys; capture connection.update handlers so tests can fire events
const mockUpdateHandlers = [];
jest.mock('@whiskeysockets/baileys', () => {
  const mockSock = {
    ev: {
      on: jest.fn((evt, handler) => {
        if (evt === 'connection.update') mockUpdateHandlers.push(handler);
      }),
      off: jest.fn(),
    },
    end: jest.fn(),
  };
  return {
    default: jest.fn(() => mockSock),
    useMultiFileAuthState: jest.fn().mockResolvedValue({
      state: { creds: {}, keys: {} },
      saveCreds: jest.fn(),
    }),
    DisconnectReason: { loggedOut: 401, connectionLost: 408 },
    fetchLatestBaileysVersion: jest.fn().mockResolvedValue({ version: [2, 3000, 0] }),
  };
});

const baileys = require('@whiskeysockets/baileys');
const { verifyStatus, getStatus, disconnect, createConnection } = require('../connection');

const CREDS_FILE = path.join(mockAuthPath, 'creds.json');

function writeCreds() {
  fs.writeFileSync(CREDS_FILE, JSON.stringify({ me: { id: '447000000000:1@s.whatsapp.net' } }));
}

function fireUpdate(update) {
  for (const h of [...mockUpdateHandlers]) h(update);
}

describe('verifyStatus', () => {
  beforeEach(() => {
    disconnect();
    mockUpdateHandlers.length = 0;
    if (fs.existsSync(CREDS_FILE)) fs.unlinkSync(CREDS_FILE);
    baileys.default.mockClear();
  });

  test('reports no-credentials without attempting connection when creds.json missing', async () => {
    const status = await verifyStatus();
    expect(status).toEqual({ authenticated: false, state: 'no-credentials' });
    expect(baileys.default).not.toHaveBeenCalled();
  });

  test('connects and reports open when saved credentials exist', async () => {
    writeCreds();
    const promise = verifyStatus({ timeoutMs: 5000 });
    // wait for async createConnection to register its handlers
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    fireUpdate({ connection: 'open' });
    const status = await promise;
    expect(status.authenticated).toBe(true);
    expect(status.state).toBe('open');
  });

  test('reports failure when connection does not open within timeout', async () => {
    writeCreds();
    const status = await verifyStatus({ timeoutMs: 50 });
    expect(status.authenticated).toBe(false);
    expect(status.error).toMatch(/50ms/);
  });
});

describe('connection state tracking after open', () => {
  beforeEach(() => {
    disconnect();
    mockUpdateHandlers.length = 0;
    baileys.default.mockClear();
  });

  test('a close event after open marks state disconnected (no stale open)', async () => {
    const promise = createConnection();
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    fireUpdate({ connection: 'open' });
    await promise;
    expect(getStatus().state).toBe('open');

    fireUpdate({ connection: 'close', lastDisconnect: { error: null } });
    expect(getStatus()).toEqual({ authenticated: false, state: 'disconnected' });
  });
});


