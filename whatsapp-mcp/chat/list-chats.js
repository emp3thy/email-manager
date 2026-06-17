const { getConnection } = require('../connection');

async function handleListChats({ limit = 20 } = {}) {
  try {
    const sock = await getConnection();
    const groups = await sock.groupFetchAllParticipating();

    const chats = Object.values(groups)
      .slice(0, limit)
      .map((g) => ({
        name: g.subject,
        id: g.id,
      }));

    return {
      content: [{
        type: 'text',
        text: JSON.stringify({ count: chats.length, chats }),
      }],
    };
  } catch (error) {
    return {
      content: [{
        type: 'text',
        text: `Error listing chats: ${error.message}`,
      }],
    };
  }
}

module.exports = handleListChats;
