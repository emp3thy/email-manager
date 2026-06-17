const { getConnection, getStatus, disconnect } = require('../connection');

// Mock baileys to avoid real WhatsApp connection in tests
jest.mock('@whiskeysockets/baileys', () => {
  const mockSock = {
    ev: {
      on: jest.fn(),
      off: jest.fn(),
    },
    end: jest.fn(),
    groupFetchAllParticipating: jest.fn().mockResolvedValue({}),
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

describe('connection manager', () => {
  beforeEach(() => {
    disconnect();
  });

  test('getStatus returns not_authenticated when no connection exists', () => {
    const status = getStatus();
    expect(status).toEqual({ authenticated: false, state: 'disconnected' });
  });
});
