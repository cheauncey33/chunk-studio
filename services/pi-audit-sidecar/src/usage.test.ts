import assert from "node:assert/strict";
import { test } from "node:test";

import {
	requestIdForAssistantMessage,
	sidecarAuthHeaders,
	usageEventCanPost,
	usageFromAssistantMessage,
} from "./usage.ts";

const identity = {
	job_id: "job-1",
	run_id: "run-1",
	job_attempt: 2,
	case_id: "c01",
};

test("three assistant message_end payloads become three usage events", () => {
	const turns = [
		{ id: "msg-1", role: "assistant", provider: "zhipu", model: "glm-5.3-flash", usage: { input: 1000, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 1000 } },
		{ id: "msg-2", role: "assistant", provider: "zhipu", model: "glm-5.3-flash", usage: { input: 800, output: 0, cacheRead: 10, cacheWrite: 0, reasoning: 40, totalTokens: 800 } },
		{ id: "msg-3", role: "assistant", provider: "zhipu", model: "glm-5.3-flash", usage: { input: 300, output: 100, cacheRead: 0, cacheWrite: 0, totalTokens: 400 } },
	];
	const events = turns.map((message) => usageFromAssistantMessage(identity, message));
	assert.equal(events.length, 3);
	const ids = events.map((item) => item?.request_id);
	assert.equal(new Set(ids).size, 3);
	assert.ok(ids.every((id) => /^pi:[a-f0-9]{64}$/.test(String(id))));
	const total = events.reduce((sum, item) => sum + Number(item?.total_tokens || 0), 0);
	assert.equal(total, 2200);
	assert.equal(events[1]?.reasoning_tokens, 40);
	assert.equal(events[1]?.cache_read_tokens, 10);
	assert.equal(events[2]?.output_tokens, 100);
	assert.ok(events.every((item) => item?.stage === "audit_agent"));
	assert.ok(events.every((item) => item?.usage_source === "sdk"));
	assert.ok(events.every((item) => item?.job_attempt === 2));
});

test("duplicate message id yields the same request_id", () => {
	const message = {
		id: "msg-dup",
		role: "assistant",
		usage: { input: 10, output: 5, cacheRead: 0, cacheWrite: 0, totalTokens: 15 },
	};
	assert.equal(
		requestIdForAssistantMessage(identity, message),
		requestIdForAssistantMessage(identity, message),
	);
	assert.match(String(usageFromAssistantMessage(identity, message)?.request_id), /^pi:[a-f0-9]{64}$/);
});

test("same message id on a different job attempt gets a different request_id", () => {
	const message = {
		id: "msg-retry",
		role: "assistant",
		usage: { input: 10, output: 5, cacheRead: 0, cacheWrite: 0, totalTokens: 15 },
	};
	const first = requestIdForAssistantMessage(identity, message);
	const retried = requestIdForAssistantMessage({ ...identity, job_attempt: 3 }, message);
	assert.notEqual(first, retried);
	assert.match(first, /^pi:[a-f0-9]{64}$/);
	assert.match(retried, /^pi:[a-f0-9]{64}$/);
});

test("user and tool messages are ignored", () => {
	assert.equal(usageFromAssistantMessage(identity, { role: "user", usage: { input: 1 } }), null);
	assert.equal(usageFromAssistantMessage(identity, { role: "toolResult" }), null);
});

test("missing usage is unknown rather than estimated", () => {
	const event = usageFromAssistantMessage(identity, {
		id: "msg-empty",
		role: "assistant",
		provider: "zhipu",
		model: "glm-5.3-flash",
	});
	assert.ok(event);
	assert.equal(event?.usage_source, "unknown");
	assert.equal(event?.input_tokens, null);
	assert.equal(event?.total_tokens, null);
});

test("totalTokens falls back to input+output and does not add reasoning", () => {
	const event = usageFromAssistantMessage(identity, {
		id: "msg-noshape",
		role: "assistant",
		usage: { input: 100, output: 40, reasoning: 20, cacheRead: 0, cacheWrite: 0 },
	});
	assert.equal(event?.total_tokens, 140);
	assert.equal(event?.reasoning_tokens, 20);
});

test("sidecar auth headers carry the service token", () => {
	const previous = process.env.AGENT_SIDECAR_TOKEN;
	process.env.AGENT_SIDECAR_TOKEN = "secret";
	try {
		assert.deepEqual(sidecarAuthHeaders(), { Authorization: "Bearer secret" });
	} finally {
		if (previous === undefined) delete process.env.AGENT_SIDECAR_TOKEN;
		else process.env.AGENT_SIDECAR_TOKEN = previous;
	}
});

test("chat usage posts with conversation_id and does not require a job", () => {
	const event = usageFromAssistantMessage(
		{ conversation_id: "chat_1", stage: "chat_agent" },
		{
			id: "msg-chat",
			role: "assistant",
			provider: "zhipu",
			model: "glm-5.3-flash",
			usage: { input: 20, output: 5, cacheRead: 0, cacheWrite: 0, totalTokens: 25 },
		},
	);
	assert.ok(event);
	assert.equal(event?.job_id, "");
	assert.equal(event?.conversation_id, "chat_1");
	assert.equal(event?.stage, "chat_agent");
	assert.equal(event?.total_tokens, 25);
	assert.equal(usageEventCanPost(event!), true);
	assert.equal(
		usageEventCanPost({
			...event!,
			job_id: "",
			conversation_id: "",
		}),
		false,
	);
});
