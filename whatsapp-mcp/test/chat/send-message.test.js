const mockSock = {
  user: { id: '447700900000:12@s.whatsapp.net' },
  sendMessage: jest.fn().mockResolvedValue({ key: { id: 'SENT123' } }),
  groupFetchAllParticipating: jest.fn().mockResolvedValue({
    '123@g.us': { subject: 'gethin & helen pt', id: '123@g.us' },
    '456@g.us': { subject: 'Family', id: '456@g.us' },
  }),
};

jest.mock('../../connection', () => ({
  getConnection: jest.fn().mockResolvedValue(mockSock),
}));

jest.mock('@whiskeysockets/baileys', () => ({
  // Strip the device suffix, mirroring the real jidNormalizedUser
  jidNormalizedUser: (id) => `${String(id).split(':')[0]}@s.whatsapp.net`,
}));

const handleSendMessage = require('../../chat/send-message');

describe('send-message handler', () => {
  beforeEach(() => {
    mockSock.sendMessage.mockClear();
  });

  test('messages yourself when no target is given', async () => {
    const result = await handleSendMessage({ text: 'digest' });
    const data = JSON.parse(result.content[0].text);
    expect(data.sent).toBe(true);
    expect(data.to).toBe('447700900000@s.whatsapp.net');
    expect(mockSock.sendMessage).toHaveBeenCalledWith(
      '447700900000@s.whatsapp.net',
      { text: 'digest' }
    );
  });

  test('sends to a group by name', async () => {
    const result = await handleSendMessage({ text: 'hi', chatName: 'gethin & helen pt' });
    const data = JSON.parse(result.content[0].text);
    expect(data.to).toBe('123@g.us');
    expect(mockSock.sendMessage).toHaveBeenCalledWith('123@g.us', { text: 'hi' });
  });

  test('sends to a phone number, stripping non-digits', async () => {
    const result = await handleSendMessage({ text: 'hi', to: '+44 7700 900111' });
    const data = JSON.parse(result.content[0].text);
    expect(data.to).toBe('447700900111@s.whatsapp.net');
  });

  test('returns an error for empty text', async () => {
    const result = await handleSendMessage({ text: '   ' });
    expect(result.content[0].text).toContain('required');
    expect(mockSock.sendMessage).not.toHaveBeenCalled();
  });

  test('returns an error for an unknown group', async () => {
    const result = await handleSendMessage({ text: 'hi', chatName: 'nonexistent' });
    expect(result.content[0].text).toContain('not found');
    expect(mockSock.sendMessage).not.toHaveBeenCalled();
  });
});
