const { getConnection } = require('../connection');
const { jidNormalizedUser } = require('@whiskeysockets/baileys');

// Resolve the destination JID for a send.
// Priority: explicit group chatName -> explicit phone number `to` -> self (Message Yourself).
async function resolveJid({ to, chatName }, sock) {
  if (chatName) {
    const groups = await sock.groupFetchAllParticipating();
    const target = Object.values(groups).find(
      (g) => g.subject.toLowerCase() === chatName.toLowerCase()
    );
    return target ? target.id : null;
  }

  if (to && to !== 'self') {
    const digits = String(to).replace(/[^0-9]/g, '');
    return digits ? `${digits}@s.whatsapp.net` : null;
  }

  // Default: message yourself. Uses the authenticated account's own number,
  // so no phone number needs to be stored anywhere.
  const selfId = sock.user && sock.user.id;
  return selfId ? jidNormalizedUser(selfId) : null;
}

async function handleSendMessage({ text, to, chatName }) {
  if (!text || !String(text).trim()) {
    return {
      content: [{
        type: 'text',
        text: 'Error: "text" is required and cannot be empty.',
      }],
    };
  }

  try {
    const sock = await getConnection();
    const jid = await resolveJid({ to, chatName }, sock);

    if (!jid) {
      const reason = chatName
        ? `Chat "${chatName}" not found. Use whatsapp-list-chats to see available chats.`
        : 'Could not resolve a destination. Provide "chatName" (group), "to" (phone number), or omit both to message yourself.';
      return { content: [{ type: 'text', text: reason }] };
    }

    const result = await sock.sendMessage(jid, { text: String(text) });

    return {
      content: [{
        type: 'text',
        text: JSON.stringify({
          sent: true,
          to: jid,
          messageId: (result && result.key && result.key.id) || null,
        }),
      }],
    };
  } catch (error) {
    return {
      content: [{
        type: 'text',
        text: `Error sending message: ${error.message}`,
      }],
    };
  }
}

module.exports = handleSendMessage;
