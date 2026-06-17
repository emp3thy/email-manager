const handleListChats = require('./list-chats');
const handleGetMessages = require('./get-messages');

const chatTools = [
  {
    name: 'whatsapp-list-chats',
    description: 'List available WhatsApp group chats',
    inputSchema: {
      type: 'object',
      properties: {
        limit: {
          type: 'number',
          description: 'Maximum number of chats to return (default: 20)',
        },
      },
      required: [],
    },
    handler: handleListChats,
  },
  {
    name: 'whatsapp-get-messages',
    description: 'Get messages from a WhatsApp group chat. Returns sender, timestamp, and text for each message.',
    inputSchema: {
      type: 'object',
      properties: {
        chatName: {
          type: 'string',
          description: 'The name of the group chat (case-insensitive)',
        },
        since: {
          type: 'string',
          description: 'Only return messages after this ISO 8601 timestamp',
        },
      },
      required: ['chatName'],
    },
    handler: handleGetMessages,
  },
];

module.exports = { chatTools };
