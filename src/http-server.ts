#!/usr/bin/env node

/**
 * HTTP Server Entry Point for Premium Docker Deployment
 *
 * Provides Streamable HTTP transport for remote MCP clients.
 * Use src/index.ts for local stdio-based usage.
 */

import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID, timingSafeEqual } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { LawMcpShell } from "./shell/shell.js";
import { germanyAdapter } from "./adapters/de.js";
import { getCapabilities } from "./db/german-law-db.js";
import type { ToolName } from "./shell/types.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const PORT = parseInt(process.env.PORT || "3000", 10);

let pkgVersion = "0.0.0";
try {
  const pkg = JSON.parse(
    readFileSync(join(__dirname, "..", "package.json"), "utf8"),
  ) as { version: string };
  pkgVersion = pkg.version;
} catch {
  // fallback
}

const SERVER_NAME = "german-law-mcp";
const COMMUNITY_TOOLS = new Set([
  "search_legislation",
  "format_citation",
  "check_currency",
  "get_provision",
  "parse_citation",
  "validate_citation",
  "list_sources",
  "about",
]);
const BEARER_TOKEN = process.env.MCP_BEARER_TOKEN?.trim() ?? "";
const ALLOWED_ORIGINS = new Set(
  (process.env.ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);

function createMcpServer(): { server: Server; shell: LawMcpShell } {
  const enrichedAdapter = {
    ...germanyAdapter,
    getDbCapabilities: () => getCapabilities(),
  };
  const shell = LawMcpShell.fromAdapters([enrichedAdapter]);

  const server = new Server(
    { name: SERVER_NAME, version: pkgVersion },
    {
      capabilities: { tools: {} },
      instructions:
        "Searches the current, locally ingested German federal-law corpus. " +
        "Always preserve source_url and source_snapshot in answers. This is not legal advice.",
    },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const definitions = shell
      .getToolDefinitions()
      .filter((definition) => COMMUNITY_TOOLS.has(definition.name));
    const baseTools = definitions.map((def) => ({
      name: def.name,
      description: def.description,
      inputSchema: def.inputSchema,
      annotations: { readOnlyHint: true },
    }));
    return { tools: baseTools };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const toolName = request.params.name;
    const args = (request.params.arguments ?? {}) as Record<string, unknown>;

    if (!COMMUNITY_TOOLS.has(toolName)) {
      return {
        content: [{ type: "text" as const, text: JSON.stringify({ error: "Unknown tool" }) }],
        isError: true,
      };
    }

    // Base tool — delegate to shell
    const result = await shell.handleToolCall({
      name: toolName as ToolName,
      arguments: args,
    });

    if (result.ok) {
      return {
        content: [
          { type: "text" as const, text: JSON.stringify(result.data, null, 2) },
        ],
      };
    }

    return {
      content: [
        { type: "text" as const, text: JSON.stringify(result.error, null, 2) },
      ],
      isError: true,
    };
  });

  return { server, shell };
}

async function main() {
  const sessions = new Map<
    string,
    { transport: StreamableHTTPServerTransport; server: Server }
  >();

  const httpServer = createServer((req, res) => {
    handleRequest(req, res, sessions).catch((err) => {
      console.error(`[${SERVER_NAME}] Unhandled error:`, err);
      if (!res.headersSent) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Internal server error" }));
      }
    });
  });

  async function handleRequest(
    req: import("node:http").IncomingMessage,
    res: import("node:http").ServerResponse,
    sessions: Map<
      string,
      { transport: StreamableHTTPServerTransport; server: Server }
    >,
  ) {
    const url = new URL(req.url || "/", `http://localhost:${PORT}`);

    if (url.pathname === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", server: SERVER_NAME }));
      return;
    }

    if (url.pathname === "/mcp") {
      if (!authorize(req)) {
        res.writeHead(401, {
          "Content-Type": "application/json",
          "WWW-Authenticate": 'Bearer realm="german-law-mcp"',
        });
        res.end(JSON.stringify({ error: "Unauthorized" }));
        return;
      }

      const origin = req.headers.origin;
      if (origin && !ALLOWED_ORIGINS.has(origin)) {
        res.writeHead(403, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Origin not allowed" }));
        return;
      }
      if (origin) {
        res.setHeader("Access-Control-Allow-Origin", origin);
        res.setHeader("Vary", "Origin");
      }
      if (req.method === "OPTIONS") {
        res.writeHead(204, {
          "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
          "Access-Control-Allow-Headers":
            "Authorization, Content-Type, MCP-Protocol-Version, MCP-Session-Id",
        });
        res.end();
        return;
      }

      const sessionId = req.headers["mcp-session-id"] as string | undefined;

      if (sessionId && sessions.has(sessionId)) {
        const session = sessions.get(sessionId)!;
        await session.transport.handleRequest(req, res);
        return;
      }

      // New session — create a fresh MCP server instance per session
      const { server: mcpServer } = createMcpServer();
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- SDK type mismatch with exactOptionalPropertyTypes
      await mcpServer.connect(transport as any);
      transport.onclose = () => {
        if (transport.sessionId) {
          sessions.delete(transport.sessionId);
        }
        mcpServer.close().catch(() => {});
      };

      await transport.handleRequest(req, res);

      // Store AFTER handleRequest — sessionId is set during initialize
      if (transport.sessionId) {
        sessions.set(transport.sessionId, { transport, server: mcpServer });
      }
      return;
    }

    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Not found" }));
  }

  httpServer.listen(PORT, () => {
    console.error(
      `${SERVER_NAME} (HTTP) listening on port ${PORT}`,
    );
    console.error(`MCP endpoint: http://localhost:${PORT}/mcp`);
    console.error(`Health check: http://localhost:${PORT}/health`);
  });

  process.on("SIGTERM", () => {
    console.error("Received SIGTERM, shutting down...");
    httpServer.close(() => process.exit(0));
  });
}

function authorize(req: import("node:http").IncomingMessage): boolean {
  if (!BEARER_TOKEN) return true;
  const prefix = "Bearer ";
  const authorization = req.headers.authorization ?? "";
  if (!authorization.startsWith(prefix)) return false;
  const supplied = Buffer.from(authorization.slice(prefix.length), "utf8");
  const expected = Buffer.from(BEARER_TOKEN, "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

main().catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});
