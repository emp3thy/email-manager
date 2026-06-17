# Email Manager

Multi-account email management (Gmail + Outlook) with a WhatsApp/calendar companion.
Classification rules live in `rules.yaml`; per-account run state lives in `state.json`
(both gitignored where they hold personal data). Email operations run through three local
MCP servers that Claude Code launches via `.mcp.json`.

## Components

| Server | Dir | Purpose |
|--------|-----|---------|
| `gmail-manager` | (npx package) | Gmail read/label/search/send |
| `outlook-manager` | `outlook-mcp/` | Outlook + OneDrive + Power Automate via MS Graph |
| `whatsapp-manager` | `whatsapp-mcp/` | WhatsApp chats/messages |

## Setup

### 1. Install dependencies

```bash
cd outlook-mcp  && npm install && cd ..
cd whatsapp-mcp && npm install && cd ..
```

### 2. Configure Claude Code MCP servers

Copy the example and edit paths for your machine:

```bash
cp .mcp.json.example .mcp.json
```

In `.mcp.json` (gitignored):
- Set the `outlook-manager` and `whatsapp-manager` `args` to the absolute path of
  `outlook-mcp/index.js` / `whatsapp-mcp/index.js` on your machine.
- Set the `gmail-manager` `GMAIL_OAUTH_PATH` / `GMAIL_CREDENTIALS_PATH` to your local
  Gmail OAuth files (see Gmail setup below).

Outlook credentials are **not** placed in `.mcp.json` — the outlook server loads them from
`outlook-mcp/.env` (next step).

### 3. Outlook credentials (Azure)

1. Register an app at Azure Portal → App registrations. Add a client secret and grant the
   Graph delegated scopes listed in `outlook-mcp/CLAUDE.md`.
2. Create the env file from the template:

   ```bash
   cp outlook-mcp/.env.example outlook-mcp/.env
   ```

3. Fill in `outlook-mcp/.env` (gitignored) with the **secret VALUE** (not the Secret ID):

   ```
   MS_CLIENT_ID=<application (client) id>
   MS_CLIENT_SECRET=<client secret value>
   MS_TENANT_ID=consumers        # or your tenant GUID for single-tenant apps
   USE_TEST_MODE=false
   ```

   `outlook-mcp/index.js` loads this file via dotenv at startup, so the same `.env` serves
   both the MCP server and the auth server.

4. Authenticate (one-time, fetches OAuth tokens to `~/.outlook-mcp-tokens.json`):

   ```bash
   cd outlook-mcp && npm run auth-server
   ```

   Then use the `authenticate` tool and complete the browser login.

### 4. Gmail credentials

Follow the `@gongrzhe/server-gmail-autoauth-mcp` package instructions to produce
`gcp-oauth.keys.json` and `credentials.json`, place them outside the repo (e.g.
`~/.gmail-mcp/`), and point `.mcp.json` at them.

### 5. WhatsApp

On first run the `whatsapp-manager` server prints a QR code to authenticate; session keys
are stored in `whatsapp-mcp/auth-store/` (gitignored — never commit these).

## Security notes

- `.env`, `.mcp.json`, `whatsapp-mcp/auth-store/`, `state.json`, and the Outlook data dumps
  are gitignored. Do not commit credentials or exported mailbox data.
- If a secret is ever committed, rotate it — removing it from a later commit does not purge
  it from history.
