const QRCode = require('qrcode');
const path = require('path');
const { exec } = require('child_process');
const { createConnection, getStatus } = require('../connection');

const QR_IMAGE_PATH = path.join(__dirname, '..', 'whatsapp-qr.png');

async function handleAuthenticate() {
  const status = getStatus();
  if (status.authenticated) {
    return {
      content: [{
        type: 'text',
        text: 'Already authenticated to WhatsApp.',
      }],
    };
  }

  // Return immediately after saving QR as PNG and opening it.
  // The connection continues in the background — use whatsapp-auth-status to check.
  return new Promise((resolve, reject) => {
    let resolved = false;

    const connectionPromise = createConnection({
      onQR: async (qr) => {
        if (resolved) return;
        resolved = true;
        try {
          await QRCode.toFile(QR_IMAGE_PATH, qr, { width: 400, margin: 2 });
          exec(`start "" "${QR_IMAGE_PATH}"`);
          resolve({
            content: [{
              type: 'text',
              text: `QR code saved and opened: ${QR_IMAGE_PATH}\nScan it with WhatsApp (Linked Devices > Link a Device).\nAfter scanning, use whatsapp-auth-status to confirm the connection.`,
            }],
          });
        } catch (err) {
          resolve({
            content: [{
              type: 'text',
              text: `Failed to generate QR image: ${err.message}`,
            }],
          });
        }
      },
    });

    // If connection opens before QR (e.g. saved session), resolve success
    connectionPromise.then(() => {
      if (resolved) return;
      resolved = true;
      resolve({
        content: [{
          type: 'text',
          text: 'Successfully authenticated to WhatsApp. Session credentials saved.',
        }],
      });
    }).catch((error) => {
      if (resolved) return;
      resolved = true;
      resolve({
        content: [{
          type: 'text',
          text: `Authentication failed: ${error.message}`,
        }],
      });
    });
  });
}

module.exports = handleAuthenticate;
