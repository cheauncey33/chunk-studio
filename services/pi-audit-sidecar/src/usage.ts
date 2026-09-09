/**
 * Per-turn LLM usage extraction for Pi agent sessions.
 *
 * Source of truth (pi-coding-agent 0.85.1 / pi-ai Usage):
 *   session event type === "message_end"
 *   event.message.role === "assistant"
 *   event.message.usage = {
 *     input, output, cacheRead, cacheWrite,
 *     reasoning?,          // optional
 *     totalTokens?,        // native total when the provider supplies it
 *     cost: { input, output, cacheRead, cacheWrite, total }
 *   }
 *
 * ``cost`` is the SDK's own dollar estimate from ModelRuntime.cost (this
 * sidecar registers zeros) and is never used as our ledger cost.
 *
 * One audit case can produce many assistant messages (tool-call turns).
 * Each message_end is one usage event, posted immediately to the Python
 * ledger so a later sidecar crash cannot drop already-spent tokens.
 */
import { createHash, randomUUID } from "node:crypto";

export type ExecutionIdentity = {
	job_id?: string | null;
	run_id?: string | null;
	job_attempt?: number | null;
	case_id?: string | null;
};

export type NormalizedTurnUsage = {
	request_id: string;
	job_id: string;
	run_id: string;
	case_id: string;
	job_attempt: number | null;
	stage: "audit_agent";
	provider: string;
	model: string;
	status: "success" | "failed";
	usage_source: "sdk" | "unknown";
	input_tokens: number | null;
	output_tokens: number | null;
	reasoning_tokens: number | null;
	cache_read_tokens: number | null;
	cache_write_tokens: number | null;
	total_tokens: number | null;
	usage: Record<string, unknown>;
};

function asNonNegInt(value: unknown): number | null {
	if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
	return Math.floor(value);
}

function usageObject(message: Record<string, unknown>): Record<string, unknown> | null {
	const usage = message.usage;
	return usage && typeof usage === "object" && !Array.isArray(usage)
		? (usage as Record<string, unknown>)
		: null;
}

function stableRequestHash(parts: string[]): string {
	return createHash("sha256").update(parts.join("\0")).digest("hex");
}

export function requestIdForAssistantMessage(
	identity: ExecutionIdentity,
	message: Record<string, unknown>,
): string {
	const messageId = String(message.id || message.messageId || "").trim();
	const executionSeed = [
		"pi",
		String(identity.job_id || ""),
		String(identity.job_attempt ?? ""),
		String(identity.case_id || ""),
	];
	if (messageId) {
		return `pi:${stableRequestHash([...executionSeed, messageId])}`;
	}
	const usage = usageObject(message) || {};
	const fallbackParts = [
		...executionSeed,
		String(message.timestamp || ""),
		String(usage.input ?? ""),
		String(usage.output ?? ""),
		String(message.stopReason || ""),
	];
	const hasStableSeed = fallbackParts.slice(1).some((part) => part !== "");
	if (!hasStableSeed) return `pi:${randomUUID()}`;
	return `pi:${stableRequestHash(fallbackParts)}`;
}

export function usageFromAssistantMessage(
	identity: ExecutionIdentity,
	message: Record<string, unknown> | null | undefined,
): NormalizedTurnUsage | null {
	if (!message || typeof message !== "object") return null;
	if (String(message.role || "") !== "assistant") return null;
	const usage = usageObject(message);
	const input = usage ? asNonNegInt(usage.input) : null;
	const output = usage ? asNonNegInt(usage.output) : null;
	const reasoning = usage ? asNonNegInt(usage.reasoning) : null;
	const cacheRead = usage ? asNonNegInt(usage.cacheRead) : null;
	const cacheWrite = usage ? asNonNegInt(usage.cacheWrite) : null;
	const reportedTotal = usage
		? asNonNegInt(usage.totalTokens ?? usage.total_tokens)
		: null;
	const known = usage != null && (input != null || output != null || reportedTotal != null);
	const total =
		reportedTotal != null
			? reportedTotal
			: input != null && output != null
				? input + output
				: null;
	const failed = Boolean(message.errorMessage) || String(message.stopReason || "") === "error";
	return {
		request_id: requestIdForAssistantMessage(identity, message),
		job_id: String(identity.job_id || ""),
		run_id: String(identity.run_id || ""),
		case_id: String(identity.case_id || ""),
		job_attempt:
			typeof identity.job_attempt === "number" && Number.isFinite(identity.job_attempt)
				? Math.floor(identity.job_attempt)
				: null,
		stage: "audit_agent",
		provider: String(message.provider || process.env.PI_PROVIDER || "zhipu"),
		model: String(message.model || process.env.PI_MODEL || "glm-5.3-flash"),
		status: failed ? "failed" : "success",
		usage_source: known ? "sdk" : "unknown",
		input_tokens: known ? input ?? 0 : null,
		output_tokens: known ? output ?? 0 : null,
		reasoning_tokens: known ? reasoning ?? 0 : null,
		cache_read_tokens: known ? cacheRead ?? 0 : null,
		cache_write_tokens: known ? cacheWrite ?? 0 : null,
		total_tokens: known ? total : null,
		usage: usage ? { ...usage } : {},
	};
}

function apiBase(): string {
	return (process.env.CHUNK_STUDIO_API_BASE ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

function authHeader(): Record<string, string> {
	const token = String(process.env.AGENT_SIDECAR_TOKEN || "").trim();
	return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function postUsageEvent(event: NormalizedTurnUsage): Promise<boolean> {
	if (!event.job_id || !event.request_id) return false;
	try {
		const res = await fetch(`${apiBase()}/internal/llm-usage`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				...authHeader(),
			},
			body: JSON.stringify(event),
		});
		if (!res.ok) {
			console.warn(
				`usage ingest HTTP ${res.status} request_id=${event.request_id} case=${event.case_id}`,
			);
			return false;
		}
		return true;
	} catch (err) {
		console.warn(
			`usage ingest failed request_id=${event.request_id}: ${err instanceof Error ? err.message : err}`,
		);
		return false;
	}
}

export function recordAssistantMessageUsage(
	identity: ExecutionIdentity,
	message: Record<string, unknown> | null | undefined,
): Promise<boolean> {
	try {
		const event = usageFromAssistantMessage(identity, message);
		if (!event) return Promise.resolve(false);
		return postUsageEvent(event);
	} catch (err) {
		console.warn(`usage extract failed: ${err instanceof Error ? err.message : err}`);
		return Promise.resolve(false);
	}
}
