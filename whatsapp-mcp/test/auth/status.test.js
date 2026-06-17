const handleAuthStatus = require('../../auth/status');

jest.mock('../../connection', () => ({
  getStatus: jest.fn().mockReturnValue({ authenticated: false, state: 'disconnected' }),
}));

describe('auth-status handler', () => {
  test('returns authentication status', async () => {
    const result = await handleAuthStatus({});
    expect(result.content).toBeDefined();
    expect(result.content[0].type).toBe('text');
    const data = JSON.parse(result.content[0].text);
    expect(data.authenticated).toBe(false);
  });
});
