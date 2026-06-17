# WhatsApp-to-Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a WhatsApp MCP server that reads messages from the "gethin & helen pt" group chat, enabling the clean-inbox skill to extract weights session schedules and create/manage Outlook calendar events.

**Architecture:** New Node.js MCP server at `whatsapp-mcp/` following the same modular pattern as `outlook-mcp/`. Uses `@whiskeysockets/baileys` for WhatsApp Web connectivity via WebSocket. Tools expose read-only WhatsApp access (auth status, authenticate, list chats, get messages). The clean-inbox skill orchestrates session extraction (via Claude NLP) and calendar event creation (via existing outlook-manager tools).

**Tech Stack:** Node.js, @whiskeysockets/baileys, @modelcontextprotocol/sdk, qrcode-terminal

---

### Task 1: Project Scaffold

**Files:**
- Create: `whatsapp-mcp/package.json`
- Create: `whatsapp-mcp/.gitignore`
- Create: `whatsapp-mcp/config.js`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "whatsapp-mcp",
  "version": "1.0.0",
  "description": "MCP server for Claude to read WhatsApp messages via Baileys",
  "main": "index.js",
  "scripts": {
    "start": "node index.js",
    "test": "jest"
  },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.27.1",
    "@whiskeysockets/baileys": "^6.7.16",
    "qrcode-terminal": "^0.12.0"
  },
  "devDependencies": {
    "jest": "^29.7.0"
  },
  "engines": {
    "node": ">=18.0.0"
  }
}
```

- [ ] **Step 2: Create .gitignore**

```
node_modules/
auth-store/
```

- [ ] **Step 3: Create config.js**

```js
const path = require('path');

module.exports = {
  SERVER_NAME: 'whatsapp-assistant',
  SERVER_VERSION: '1.0.0',
  AUTH_STORE_PATH: path.join(__dirname, 'auth-store'),
};
```

- [ ] **Step 4: Install dependencies**

Run: `cd whatsapp-mcp && npm install`
Expected: `node_modules/` created, no errors

- [ ] **Step 5: Commit**

```bash
git add whatsapp-mcp/package.json whatsapp-mcp/package-lock.json whatsapp-mcp/.gitignore whatsapp-mcp/config.js
git commit -m "feat(whatsapp-mcp): project scaffold"
```

---

### Task 2: Connection Manager

**Files:**
- Create: `whatsapp-mcp/connection.js`
- Test: `whatsapp-mcp/test/connection.test.js`

The connection manager is a singleton that handles the Baileys socket lifecycle. It creates the socket on demand, manages auth state persistence, and exposes connection status. All tools use this instead of creating their own sockets.

- [ ] **Step 1: Write the failing test**

Create `whatsapp-mcp/test/connection.test.js`:

```js
const { getConnection, getStatus, disconnect } = require('../connection');

// Mock baileys to avoid real WhatsApp connection in tests
jest.mock('@whiskeysockets/baileys', () => {
  const mockSock = {
    ev: {
      on: jest.fn(),
      off: jest.fn(),
    },
    end: jest.fn(),
    groupFetchAllParticipating: jest.fn().mockResolvedValue({}),
  };
  return {
    default: jest.fn(() => mockSock),
    useMultiFileAuthState: jest.fn().mockResolvedValue({
      state: { creds: {}, keys: {} },
      saveCreds: jest.fn(),
    }),
    DisconnectReason: { loggedOut: 401, connectionLost: 408 },
    fetchLatestBaileysVersion: jest.fn().mockResolvedValue({ version: [2, 3000, 0] }),
  };
});

describe('connection manager', () => {
  beforeEach(() => {
    disconnect();
  });

  test('getStatus returns not_authenticated when no connection exists', () => {
    const status = getStatus();
    expect(status).toEqual({ authenticated: false, state: 'disconnected' });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd whatsapp-mcp && npx jest test/connection.test.js --verbose`
Expected: FAIL — `Cannot find module '../connection'`

- [ ] **Step 3: Write connection manager**

Create `whatsapp-mcp/connection.js`:

```js
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const { AUTH_STORE_PATH } = require('./config');

let sock = null;
let connectionState = 'disconnected'; // disconnected | connecting | open | close

async function createConnection({ onQR } = {}) {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_STORE_PATH);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger: require('pino')({ level: 'silent' }),
  });

  sock.ev.on('creds.update', saveCreds);

  return new Promise((resolve, reject) => {
    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr && onQR) {
        onQR(qr);
      }

      if (connection === 'open') {
        connectionState = 'open';
        console.error('WhatsApp connection established');
        resolve(sock);
      }

      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        connectionState = 'disconnected';
        sock = null;

        if (statusCode === DisconnectReason.loggedOut) {
          reject(new Error('Logged out. Please re-authenticate.'));
        } else {
          reject(new Error(`Connection closed: ${lastDisconnect?.error?.message || 'unknown'}`));
        }
      }
    });

    connectionState = 'connecting';
  });
}

async function getConnection() {
  if (sock && connectionState === 'open') {
    return sock;
  }
  return createConnection();
}

function getStatus() {
  return {
    authenticated: connectionState === 'open',
    state: connectionState,
  };
}

function disconnect() {
  if (sock) {
    sock.end();
    sock = null;
  }
  connectionState = 'disconnected';
}

module.exports = { getConnection, getStatus, disconnect, createConnection };
```

Note: `pino` is a transitive dependency of Baileys — no need to add it to package.json.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd whatsapp-mcp && npx jest test/connection.test.js --verbose`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add whatsapp-mcp/connection.js whatsapp-mcp/test/connection.test.js
git commit -m "feat(whatsapp-mcp): connection manager with auth state persistence"
```

---

### Task 3: Auth Tools

**Files:**
- Create: `whatsapp-mcp/auth/index.js`
- Create: `whatsapp-mcp/auth/status.js`
- Create: `whatsapp-mcp/auth/authenticate.js`
- Test: `whatsapp-mcp/test/auth/status.test.js`

These tools let Claude check auth status and trigger QR-code authentication.

- [ ] **Step 1: Write the failing test**

Create `whatsapp-mcp/test/auth/status.test.js`:

```js
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd whatsapp-mcp && npx jest test/auth/status.test.js --verbose`
Expected: FAIL — `Cannot find module '../../auth/status'`

- [ ] **Step 3: Write auth-status handler**

Create `whatsapp-mcp/auth/status.js`:

```js
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd whatsapp-mcp && npx jest test/auth/status.test.js --verbose`
Expected: PASS

- [ ] **Step 5: Write authenticate handler**

Create `whatsapp-mcp/auth/authenticate.js`:

```js
const qrcode = require('qrcode-terminal');
const { createConnection, getStatus } = require('../connection');

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

  try {
    await createConnection({
      onQR: (qr) => {
        console.error('\n=== SCAN THIS QR CODE WITH WHATSAPP ===');
        qrcode.generate(qr, { small: true }, (output) => {
          console.error(output);
        });
        console.error('========================================\n');
      },
    });

    return {
      content: [{
        type: 'text',
        text: 'Successfully authenticated to WhatsApp. Session credentials saved.',
      }],
    };
  } catch (error) {
    return {
      content: [{
        type: 'text',
        text: `Authentication failed: ${error.message}`,
      }],
    };
  }
}

module.exports = handleAuthenticate;
```

- [ ] **Step 6: Write auth module index**

Create `whatsapp-mcp/auth/index.js`:

```js
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
```

- [ ] **Step 7: Commit**

```bash
git add whatsapp-mcp/auth/
git commit -m "feat(whatsapp-mcp): auth-status and authenticate tools"
```

---

### Task 4: Chat Tools

**Files:**
- Create: `whatsapp-mcp/chat/index.js`
- Create: `whatsapp-mcp/chat/list-chats.js`
- Create: `whatsapp-mcp/chat/get-messages.js`
- Test: `whatsapp-mcp/test/chat/get-messages.test.js`

- [ ] **Step 1: Write the failing test**

Create `whatsapp-mcp/test/chat/get-messages.test.js`:

```js
const handleGetMessages = require('../../chat/get-messages');

const mockMessages = [
  {
    key: { remoteJid: '123@g.us', fromMe: false, id: 'msg1', participant: '447877777748@s.whatsapp.net' },
    message: { conversation: 'Wednesday the 11th at 9 for you both' },
    messageTimestamp: Math.floor(new Date('2026-04-05T08:24:00Z').getTime() / 1000),
    pushName: 'Hanna Raymond',
  },
  {
    key: { remoteJid: '123@g.us', fromMe: true, id: 'msg2' },
    message: { conversation: 'Yes that works for me' },
    messageTimestamp: Math.floor(new Date('2026-04-05T09:38:00Z').getTime() / 1000),
    pushName: 'Gethin',
  },
];

const mockSock = {
  groupFetchAllParticipating: jest.fn().mockResolvedValue({
    '123@g.us': { subject: 'gethin & helen pt', id: '123@g.us' },
    '456@g.us': { subject: 'Family', id: '456@g.us' },
  }),
  fetchMessagesFromWA: jest.fn().mockResolvedValue(mockMessages),
};

jest.mock('../../connection', () => ({
  getConnection: jest.fn().mockResolvedValue(mockSock),
}));

describe('get-messages handler', () => {
  test('returns messages from named chat', async () => {
    const result = await handleGetMessages({ chatName: 'gethin & helen pt' });
    expect(result.content).toBeDefined();
    const data = JSON.parse(result.content[0].text);
    expect(data.messages).toHaveLength(2);
    expect(data.messages[0].sender).toBe('Hanna Raymond');
    expect(data.messages[0].text).toBe('Wednesday the 11th at 9 for you both');
  });

  test('returns error for unknown chat', async () => {
    const result = await handleGetMessages({ chatName: 'nonexistent' });
    expect(result.content[0].text).toContain('not found');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd whatsapp-mcp && npx jest test/chat/get-messages.test.js --verbose`
Expected: FAIL — `Cannot find module '../../chat/get-messages'`

- [ ] **Step 3: Write get-messages handler**

Create `whatsapp-mcp/chat/get-messages.js`:

```js
const { getConnection } = require('../connection');

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

    // Fetch messages from the chat
    const rawMessages = await sock.fetchMessagesFromWA(target.id, 100);

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd whatsapp-mcp && npx jest test/chat/get-messages.test.js --verbose`
Expected: PASS

Note: `fetchMessagesFromWA` is the Baileys method for fetching message history from a chat. If the actual Baileys API differs at implementation time (e.g. uses `store.loadMessages` or a different method name), adjust accordingly — the mock in the test will need to match. Check the Baileys README/types at implementation time.

- [ ] **Step 5: Write list-chats handler**

Create `whatsapp-mcp/chat/list-chats.js`:

```js
const { getConnection } = require('../connection');

async function handleListChats({ limit = 20 }) {
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
```

- [ ] **Step 6: Write chat module index**

Create `whatsapp-mcp/chat/index.js`:

```js
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
```

- [ ] **Step 7: Commit**

```bash
git add whatsapp-mcp/chat/ whatsapp-mcp/test/chat/
git commit -m "feat(whatsapp-mcp): list-chats and get-messages tools"
```

---

### Task 5: MCP Server Entry Point

**Files:**
- Create: `whatsapp-mcp/index.js`

- [ ] **Step 1: Write index.js**

Create `whatsapp-mcp/index.js`:

```js
#!/usr/bin/env node
const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { SERVER_NAME, SERVER_VERSION } = require('./config');
const { authTools } = require('./auth');
const { chatTools } = require('./chat');

console.error(`STARTING ${SERVER_NAME.toUpperCase()} MCP SERVER`);

const TOOLS = [...authTools, ...chatTools];

const server = new Server(
  { name: SERVER_NAME, version: SERVER_VERSION },
  { capabilities: { tools: {} } }
);

server.fallbackRequestHandler = async (request) => {
  try {
    const { method, params, id } = request;
    console.error(`REQUEST: ${method} [${id}]`);

    if (method === 'initialize') {
      return {
        protocolVersion: '2025-11-25',
        capabilities: { tools: {} },
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
      };
    }

    if (method === 'tools/list') {
      return {
        tools: TOOLS.map((tool) => ({
          name: tool.name,
          description: tool.description,
          inputSchema: tool.inputSchema,
        })),
      };
    }

    if (method === 'resources/list') return { resources: [] };
    if (method === 'prompts/list') return { prompts: [] };

    if (method === 'tools/call') {
      const { name, arguments: args = {} } = params || {};
      console.error(`TOOL CALL: ${name}`);
      const tool = TOOLS.find((t) => t.name === name);
      if (tool && tool.handler) {
        return await tool.handler(args);
      }
      return { error: { code: -32601, message: `Tool not found: ${name}` } };
    }

    return { error: { code: -32601, message: `Method not found: ${method}` } };
  } catch (error) {
    console.error('Error in fallbackRequestHandler:', error);
    return { error: { code: -32603, message: `Error: ${error.message}` } };
  }
};

process.on('SIGTERM', () => {
  console.error('SIGTERM received but staying alive');
});

const transport = new StdioServerTransport();
server.connect(transport)
  .then(() => console.error(`${SERVER_NAME} connected and listening`))
  .catch((error) => {
    console.error(`Connection error: ${error.message}`);
    process.exit(1);
  });
```

- [ ] **Step 2: Verify server starts without errors**

Run: `cd whatsapp-mcp && timeout 3 node index.js 2>&1 || true`
Expected: stderr shows `STARTING WHATSAPP-ASSISTANT MCP SERVER` then `whatsapp-assistant connected and listening`. Process hangs waiting for stdio input — that's correct.

- [ ] **Step 3: Commit**

```bash
git add whatsapp-mcp/index.js
git commit -m "feat(whatsapp-mcp): MCP server entry point"
```

---

### Task 6: Fix outlook-mcp create-event to Return Event ID

**Files:**
- Modify: `outlook-mcp/calendar/create.js`

The `create-event` handler currently discards the Graph API response, which contains the event ID needed for cancellation dedup. We need it to return the ID so the clean-inbox skill can store it in `state.json`.

- [ ] **Step 1: Update create-event handler to return event ID**

In `outlook-mcp/calendar/create.js`, change the success return (around line 44-49) from:

```js
    return {
      content: [{
        type: "text",
        text: `Event '${subject}' has been successfully created.`
      }]
    };
```

To:

```js
    return {
      content: [{
        type: "text",
        text: JSON.stringify({
          message: `Event '${subject}' has been successfully created.`,
          eventId: response.id,
          subject: response.subject,
          start: response.start,
          end: response.end,
        })
      }]
    };
```

- [ ] **Step 2: Verify existing outlook-mcp tests still pass**

Run: `cd outlook-mcp && npm test`
Expected: All tests pass (the create test may need updating if it asserts on the response text format).

- [ ] **Step 3: Commit**

```bash
git add outlook-mcp/calendar/create.js
git commit -m "feat(outlook-mcp): return event ID from create-event response"
```

---

### Task 7: Register in .mcp.json

**Files:**
- Modify: `.mcp.json`

- [ ] **Step 1: Add whatsapp-manager to .mcp.json**

Add the new server entry after the `outlook-manager` entry in `.mcp.json`:

```json
"whatsapp-manager": {
  "type": "stdio",
  "command": "node",
  "args": [
    "C:/Users/gethi/source/email-manager/whatsapp-mcp/index.js"
  ]
}
```

The full `.mcp.json` should now have three servers: `gmail-manager`, `outlook-manager`, `whatsapp-manager`.

- [ ] **Step 2: Commit**

```bash
git add .mcp.json
git commit -m "feat: register whatsapp-manager MCP server"
```

---

### Task 8: Update Clean-Inbox Skill

**Files:**
- Modify: The `clean-inbox` skill (location depends on skill storage — check `.claude/` or plugins directory)

This task updates the clean-inbox skill to add the WhatsApp processing phase. The skill is invoked by Claude, so the changes are to the skill's prompt/instructions rather than executable code.

- [ ] **Step 1: Find the clean-inbox skill file**

Run: `find /c/Users/gethi/.claude -name "*.md" -path "*clean-inbox*" 2>/dev/null`

The skill file will contain the inbox cleaning workflow. The exact path depends on the skill storage location.

- [ ] **Step 2: Add WhatsApp phase to the skill**

After the email processing phases in the skill, add this section:

```markdown
## Phase: WhatsApp Session Sync

After email processing is complete, process WhatsApp for weights session scheduling.

### Prerequisites
- whatsapp-manager MCP server must be registered in .mcp.json
- User must have authenticated at least once via `whatsapp-authenticate`

### Steps

1. **Check auth**: Call `whatsapp-auth-status`. If `authenticated: false`, log "WhatsApp not authenticated — skipping session sync" and continue to summary. Do not block email cleaning.

2. **Read state**: Read `state.json` and check `whatsapp.last_run`. Calculate `since` as `last_run - 5 days`. If no `last_run` exists, use 30 days ago.

3. **Fetch messages**: Call `whatsapp-get-messages` with:
   - `chatName`: value from `state.json` `whatsapp.chat_name` (default: "gethin & helen pt")
   - `since`: the calculated since timestamp

4. **Extract sessions**: From the returned messages, identify all weights sessions proposed or confirmed by Hanna (sender number contains 7877777748 or sender name contains "Hanna"). For each session extract:
   - Date (resolve relative dates like "Wednesday the 11th" to actual calendar dates)
   - Time (assume AM if no qualifier and time < 12)
   - Duration: 1 hour
   - Who: Gethin, Helen, or both (look for G/Gethin, H/Helen, "you both", or context)
   - Status: confirmed or cancelled (follow the conversation thread)

   Only include sessions with **future dates** (on or after today).

5. **Deduplicate and create**: For each confirmed future session:
   - Check `state.json` `whatsapp.created_events` for matching date + time
   - Check Outlook calendar via `list-events` for existing "Weights session" events at that date/time
   - If not found in either, create the event via `create-event`:
     - Subject: `Weights session (Gethin)`, `Weights session (Helen)`, or `Weights session (Gethin & Helen)`
     - Start: ISO 8601 datetime
     - End: start + 1 hour
     - Body: `Scheduled by Hanna via WhatsApp`

6. **Handle cancellations**: For each cancelled session:
   - Check `state.json` `whatsapp.created_events` for a matching date + time
   - If found, delete the Outlook event via `delete-event` using the stored `outlook_event_id`
   - Remove from `created_events`
   - Log: "Deleted: Weights session (X) on DATE at TIME — cancelled in chat"

7. **Update state**: Write to `state.json`:
   - Set `whatsapp.last_run` to current timestamp
   - Add newly created events to `whatsapp.created_events` with: date, time, for, outlook_event_id, created_at
   - Remove events with past dates from `created_events`
   - If `whatsapp` key doesn't exist yet, create it with `chat_name: "gethin & helen pt"`

### Summary output

Include in the run summary:
- Number of messages processed
- Sessions created (with dates/times)
- Sessions deleted (cancellations)
- Sessions skipped (already existed)
- Any ambiguous sessions skipped
```

- [ ] **Step 3: Commit**

```bash
git add <skill-file-path>
git commit -m "feat(clean-inbox): add WhatsApp session sync phase"
```

---

### Task 9: Integration Test

**Files:** None created — this is a manual verification task.

- [ ] **Step 1: Start the WhatsApp MCP server in inspector**

Run: `cd whatsapp-mcp && npx @modelcontextprotocol/inspector node index.js`

Verify the inspector shows 4 tools: `whatsapp-auth-status`, `whatsapp-authenticate`, `whatsapp-list-chats`, `whatsapp-get-messages`.

- [ ] **Step 2: Test auth-status (unauthenticated)**

Call `whatsapp-auth-status` in the inspector.
Expected response: `{ "authenticated": false, "state": "disconnected" }`

- [ ] **Step 3: Test authenticate**

Call `whatsapp-authenticate` in the inspector. A QR code should appear in the server's stderr output. Scan it with WhatsApp on your phone.
Expected response: `Successfully authenticated to WhatsApp. Session credentials saved.`
Verify: `whatsapp-mcp/auth-store/` directory now contains credential files.

- [ ] **Step 4: Test list-chats**

Call `whatsapp-list-chats` in the inspector.
Expected: response includes a chat with name `gethin & helen pt`.

- [ ] **Step 5: Test get-messages**

Call `whatsapp-get-messages` with `{ "chatName": "gethin & helen pt", "since": "2026-04-01T00:00:00Z" }`.
Expected: messages from the chat since April 1st, with sender names, timestamps, and text content.

- [ ] **Step 6: Test the full clean-inbox flow**

In a Claude Code session in the email-manager directory, run the clean-inbox skill. Verify:
- Email cleaning runs as before
- WhatsApp phase runs after email
- Sessions are extracted from chat messages
- Calendar events are created in Outlook
- `state.json` is updated with `whatsapp` key

- [ ] **Step 7: Commit any fixes from integration testing**

```bash
git add -A
git commit -m "fix(whatsapp-mcp): integration test fixes"
```
