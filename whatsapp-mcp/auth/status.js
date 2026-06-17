const { getStatus } = require('../connection');

async function handleAuthStatus() {
  const status = getStatus();
  return {
    content: [{
      type: 'text',
      text: JSON.stringify(status),
    }],
  };
}

module.exports = handleAuthStatus;
