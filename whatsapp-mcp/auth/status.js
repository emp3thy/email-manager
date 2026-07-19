const { verifyStatus } = require('../connection');

async function handleAuthStatus() {
  const status = await verifyStatus();
  return {
    content: [{
      type: 'text',
      text: JSON.stringify(status),
    }],
  };
}

module.exports = handleAuthStatus;
