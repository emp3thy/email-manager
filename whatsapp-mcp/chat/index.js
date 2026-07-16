const handleListChats = require('./list-chats');
const handleGetMessages = require('./get-messages');
const handleSendMessage = require('./send-message');

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
  {
    name: 'whatsapp-send-message',
    description: 'Send a WhatsApp text message. With no target it messages yourself (Message Yourself chat) — useful for digests and notifications. Provide chatName to send to a group, or to (phone number) to send to a contact.',
    inputSchema: {
      type: 'object',
      properties: {
        text: {
          type: 'string',
          description: 'The message text to send',
        },
        chatName: {
          type: 'string',
          description: 'Optional group chat name (case-insensitive) to send to',
        },
        to: {
          type: 'string',
          description: 'Optional recipient phone number in international format (digits only, e.g. 447700900000). Omit to message yourself.',
        },
      },
      required: ['text'],
    },
    handler: handleSendMessage,
  },
];

module.exports = { chatTools };
