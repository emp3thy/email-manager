const handleAuthStatus = require('./status');
const handleAuthenticate = require('./authenticate');

const authTools = [
  {
    name: 'whatsapp-auth-status',
    description: 'Check if the WhatsApp session is authenticated',
    inputSchema: {
      type: 'object',
      properties: {},
      required: [],
    },
    handler: handleAuthStatus,
  },
  {
    name: 'whatsapp-authenticate',
    description: 'Start WhatsApp authentication by displaying a QR code to scan with your phone',
    inputSchema: {
      type: 'object',
      properties: {},
      required: [],
    },
    handler: handleAuthenticate,
  },
];

module.exports = { authTools };
