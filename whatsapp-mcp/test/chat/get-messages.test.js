const mockMessages = [
  {
    key: { remoteJid: '123@g.us', fromMe: false, id: 'msg1', participant: '447877777748@s.whatsapp.net' },
    message: { conversation: 'Wednesday the 11th at 9 for you both' },
    messageTimestamp: Math.floor(new Date('2026-04-05T08:24:00Z').getTime() / 1000),
    pushName: 'Hanna Raymond',
  },
  {
    key: { remoteJid: '123@g.us', fromMe: true, id: 'msg2' },
    message: { conversation: 'Yes that works for me' },
    messageTimestamp: Math.floor(new Date('2026-04-05T09:38:00Z').getTime() / 1000),
    pushName: 'Gethin',
  },
];

const mockSock = {
  groupFetchAllParticipating: jest.fn().mockResolvedValue({
    '123@g.us': { subject: 'gethin & helen pt', id: '123@g.us' },
    '456@g.us': { subject: 'Family', id: '456@g.us' },
  }),
};

const mockStore = {
  loadMessages: jest.fn().mockResolvedValue(mockMessages),
};

jest.mock('../../connection', () => ({
  getConnection: jest.fn().mockResolvedValue(mockSock),
  getMessageStore: jest.fn().mockReturnValue(mockStore),
}));

const handleGetMessages = require('../../chat/get-messages');

describe('get-messages handler', () => {
  test('returns messages from named chat', async () => {
    const result = await handleGetMessages({ chatName: 'gethin & helen pt' });
    expect(result.content).toBeDefined();
    const data = JSON.parse(result.content[0].text);
    expect(data.messages).toHaveLength(2);
    expect(data.messages[0].sender).toBe('Hanna Raymond');
    expect(data.messages[0].text).toBe('Wednesday the 11th at 9 for you both');
  });

  test('returns error for unknown chat', async () => {
    const result = await handleGetMessages({ chatName: 'nonexistent' });
    expect(result.content[0].text).toContain('not found');
  });

  test('filters messages by since parameter', async () => {
    const result = await handleGetMessages({
      chatName: 'gethin & helen pt',
      since: '2026-04-05T09:00:00Z',
    });
    const data = JSON.parse(result.content[0].text);
    expect(data.messages).toHaveLength(1);
    expect(data.messages[0].sender).toBe('Gethin');
  });
});
