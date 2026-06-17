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
