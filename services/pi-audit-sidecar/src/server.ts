/**
 * Stateless HTTP executor for Pi-agent audit cases and one-shot chat turns.
 *
 * Endpoints:
 *   GET  /health       — liveness + model info
 *   POST /audit/case   — run one audit case in a fresh agent session
 *   POST /chat/turn    — run one Q&A turn in a fresh in-memory session
 *
 * Concurrency is bounded by AGENT_CONCURRENCY (default 5); extra requests queue.
 * Auth: when AGENT_SIDECAR_TOKEN is set, requests must send
 * "Authorization: Bearer <token>" (health excepted).
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readdir, unlink } from "node:fs/promises";
import { join } from "node:path";

import { runCase, type AgentCaseInput, type AgentCaseOutcome } from "./agent.ts";
import { runChatTurn, type ChatTurnInput, type ChatTurnOutcome } from "./chat.ts";
import { verdictToStatus } from "./parse.ts";

const PORT = Number(process.env.PORT ?? "8787");
const CONCURRENCY = Math.max(1, Number(process.env.AGENT_CONCURRENCY ?? "5"));
const TOKEN = (process.env.AGENT_SIDECAR_TOKEN ?? "").trim();
const MAX_BODY_BYTES = 2 * 1024 * 1024;

function send(res: ServerResponse, status: number, payload: unknown): void {
	const body = JSON.stringify(payload);
	res.writeHead(status, {
		"Content-Type": "application/json; charset=utf-8",
		"Content-Length": Buffer.byteLength(body),
	});
	res.end(body);
}

function authorized(req: IncomingMessage): boolean {
	if (!TOKEN) return true;
	const header = String(req.headers.authorization ?? "");
	return header === `Bearer ${TOKEN}`;
}

function readBody(req: IncomingMessage): Promise<string> {
	return new Promise((resolve, reject) => {
		const chunks: Buffer[] = [];
		let size = 0;
		req.on("data", (chunk: Buffer) => {
			size += chunk.length;
			if (size > MAX_BODY_BYTES) {
				reject(new Error("request body too large"));
				req.destroy();
				return;
			}
			chunks.push(chunk);
		});
		req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
		req.on("error", reject);
	});
}

// ---- bounded concurrency ----------------------------------------------------

let active = 0;
const waiters: Array<() => void> = [];

async function withSlot<T>(fn: () => Promise<T>): Promise<T> {
	if (active >= CONCURRENCY) {
		await new Promise<void>((resolve) => waiters.push(resolve));
	}
	active += 1;
	try {
		return await fn();
	} finally {
		active -= 1;
		const next = waiters.shift();
		if (next) next();
	}
}

// ---- trace housekeeping -----------------------------------------------------

async function pruneTraces(): Promise<void> {
	const keep = Number(process.env.TRACE_KEEP ?? "200");
	if (!(keep > 0)) return;
	const dir = process.env.TRACE_DIR || join(process.cwd(), "traces");
	try {
		const files = (await readdir(dir)).filter((f) => f.endsWith(".jsonl")).sort();
		const excess = files.length - keep;
		for (const name of excess > 0 ? files.slice(0, excess) : []) {
			await unlink(join(dir, name)).catch(() => {});
		}
	} catch {
		// trace dir absent or unreadable — pruning is best-effort
	}
}

// ---- handlers ---------------------------------------------------------------

async function handleAuditCase(req: IncomingMessage, res: ServerResponse): Promise<void> {
	let raw: string;
	try {
		raw = await readBody(req);
	} catch (err) {
		send(res, 413, { ok: false, error: String(err instanceof Error ? err.message : err) });
		return;
	}
	let input: AgentCaseInput;
	try {
		input = JSON.parse(raw) as AgentCaseInput;
	} catch {
		send(res, 400, { ok: false, error: "invalid JSON body" });
		return;
	}
	if (!input || typeof input.case_id !== "string" || !input.case_id.trim()) {
		send(res, 422, { ok: false, error: "case_id is required" });
		return;
	}

	try {
		const outcome: AgentCaseOutcome = await withSlot(() => runCase(input));
		const verdict = outcome.result?.verdict ?? null;
		send(res, 200, {
			ok: true,
			case_id: input.case_id,
			result: outcome.result,
			status: verdictToStatus(verdict),
			parse_mode: outcome.parse_mode,
			stats: outcome.stats,
			trace_file: outcome.trace_file,
			final_text_preview: outcome.final_text_preview,
		});
	} catch (err) {
		const message = String(err instanceof Error ? err.message : err);
		// Timeouts and transient fetch failures are retryable; request-shape
		// errors (4xx from this handler) never reach here.
		send(res, 502, { ok: false, error: message, retryable: true });
	}
}

async function handleChatTurn(req: IncomingMessage, res: ServerResponse): Promise<void> {
	let raw: string;
	try {
		raw = await readBody(req);
	} catch (err) {
		send(res, 413, { ok: false, error: String(err instanceof Error ? err.message : err) });
		return;
	}
	let input: ChatTurnInput;
	try {
		input = JSON.parse(raw) as ChatTurnInput;
	} catch {
		send(res, 400, { ok: false, error: "invalid JSON body" });
		return;
	}
	if (!input || typeof input.current_question !== "string" || !input.current_question.trim()) {
		send(res, 422, { ok: false, error: "current_question is required" });
		return;
	}

	try {
		const outcome: ChatTurnOutcome = await withSlot(() => runChatTurn(input));
		send(res, 200, {
			ok: true,
			answer: outcome.answer,
			citations: outcome.citations,
			charts: outcome.charts,
			stats: outcome.stats,
			trace_file: outcome.trace_file,
		});
	} catch (err) {
		const message = String(err instanceof Error ? err.message : err);
		send(res, 502, { ok: false, error: message, retryable: true });
	}
}

const server = createServer((req, res) => {
	const url = (req.url ?? "").split("?")[0];
	if (req.method === "GET" && url === "/health") {
		send(res, 200, {
			ok: true,
			model: process.env.PI_MODEL ?? "glm-5.3-flash",
			base_url: process.env.PI_BASE_URL ?? "https://open.bigmodel.cn/api/paas/v4",
			thinking: process.env.PI_THINKING_LEVEL ?? "low",
			concurrency: CONCURRENCY,
			active,
			queued: waiters.length,
		});
		return;
	}
	if (!authorized(req)) {
		send(res, 401, { ok: false, error: "unauthorized" });
		return;
	}
	if (req.method === "POST" && url === "/audit/case") {
		handleAuditCase(req, res).catch((err) => {
			send(res, 500, { ok: false, error: String(err instanceof Error ? err.message : err) });
		});
		return;
	}
	if (req.method === "POST" && url === "/chat/turn") {
		handleChatTurn(req, res).catch((err) => {
			send(res, 500, { ok: false, error: String(err instanceof Error ? err.message : err) });
		});
		return;
	}
	send(res, 404, { ok: false, error: "not found" });
});

server.listen(PORT, "127.0.0.1", () => {
	console.log(`pi-audit-sidecar listening on http://127.0.0.1:${PORT}`);
	console.log(
		`model=${process.env.PI_MODEL ?? "glm-5.3-flash"} thinking=${process.env.PI_THINKING_LEVEL ?? "low"} concurrency=${CONCURRENCY} budget=${process.env.PI_MAX_TOOL_CALLS ?? "12"} token=${TOKEN ? "on" : "off"}`,
	);
	setInterval(() => void pruneTraces(), 60_000).unref();
});
