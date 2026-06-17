const { getConnection, getMessageStore } = require('../connection');

const HISTORY_WAIT_MS = 30_000; // wait up to 30s for history sync

function extractText(message) {
  if (!message) return null;
  return message.conversation
    || message.extendedTextMessage?.text
    || message.imageMessage?.caption
    || message.videoMessage?.caption
    || message.documentMessage?.caption
    || null;
}

function getMediaType(message) {
  if (!message) return null;
  if (message.imageMessage) return 'image';
  if (message.videoMessage) return 'video';
  if (message.audioMessage) return 'audio';
  if (message.documentMessage) return 'document';
  if (message.stickerMessage) return 'sticker';
  return null;
}

/**
 * Wait for history sync to deliver messages for a specific chat JID.
 * Resolves with collected messages, or empty array on timeout.
 */
function waitForHistorySync(sock, targetJid, timeoutMs) {
  return new Promise((resolve) => {
    const collected = [];
    const timer = setTimeout(() => {
      sock.ev.off('messaging-history.set', onHistory);
      resolve(collected);
    }, timeoutMs);

    function onHistory({ messages } = {}) {
      for (const msg of (messages || [])) {
        if (msg.key.remoteJid === targetJid) {
          collected.push(msg);
        }
      }
      // If we got messages for this chat, wait a bit more for additional batches
      // then resolve
      if (collected.length > 0) {
        clearTimeout(timer);
        // Give 3 more seconds for follow-up batches
        setTimeout(() => {
          sock.ev.off('messaging-history.set', onHistory);
          resolve(collected);
        }, 3000);
      }
    }

    sock.ev.on('messaging-history.set', onHistory);
  });
}

async function handleGetMessages({ chatName, since }) {
  try {
    const sock = await getConnection();

    // Find the chat by name (case-insensitive)
    const groups = await sock.groupFetchAllParticipating();
    const target = Object.values(groups).find(
      (g) => g.subject.toLowerCase() === chatName.toLowerCase()
    );

    if (!target) {
      return {
        content: [{
          type: 'text',
          text: `Chat "${chatName}" not found. Use whatsapp-list-chats to see available chats.`,
        }],
      };
    }

    // Load messages from the in-memory store
    const store = getMessageStore();
    let rawMessages = await store.loadMessages(target.id, 100);

    // If store is empty for this chat, wait for history sync to deliver messages
    if (rawMessages.length === 0) {
      console.error(`No messages in store for "${target.subject}", waiting for history sync...`);
      const synced = await waitForHistorySync(sock, target.id, HISTORY_WAIT_MS);
      if (synced.length > 0) {
        console.error(`History sync delivered ${synced.length} messages for "${target.subject}"`);
        rawMessages = synced;
      } else {
        console.error(`History sync timed out for "${target.subject}" after ${HISTORY_WAIT_MS}ms`);
        // Report store state for debugging
        const stats = store.getStoreStats();
        return {
          content: [{
            type: 'text',
            text: JSON.stringify({
              chat: target.subject,
              chatJid: target.id,
              messageCount: 0,
              messages: [],
              debug: {
                note: 'No messages found after waiting for history sync',
                storeStats: stats,
              },
            }),
          }],
        };
      }
    }

    // Filter by since date if provided
    const sinceTs = since ? new Date(since).getTime() / 1000 : 0;

    const messages = rawMessages
      .filter((msg) => msg.messageTimestamp >= sinceTs)
      .map((msg) => {
        const text = extractText(msg.message);
        const mediaType = getMediaType(msg.message);
        const sender = msg.pushName || msg.key.participant || (msg.key.fromMe ? 'me' : 'unknown');
        const timestamp = new Date(msg.messageTimestamp * 1000).toISOString();

        return {
          sender,
          senderNumber: msg.key.participant || null,
          timestamp,
          text,
          mediaType,
        };
      })
      .filter((msg) => msg.text || msg.mediaType); // skip empty messages

    return {
      content: [{
        type: 'text',
        text: JSON.stringify({ chat: target.subject, messageCount: messages.length, messages }),
      }],
    };
  } catch (error) {
    return {
      content: [{
        type: 'text',
        text: `Error fetching messages: ${error.message}`,
      }],
    };
  }
}

module.exports = handleGetMessages;
