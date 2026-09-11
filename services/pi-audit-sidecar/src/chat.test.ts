import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { needsKnowledgeReadGate, looksLikeAbstain } from "./chat.ts";
import { chatSystemPrompt } from "./chatPrompt.ts";
import {
	searchKnowledgeBaseRequestBody,
	rememberSearchChunkIds,
	denyUnreadChunk,
	denyChunkFileScope,
} from "./chatTools.ts";

test("chat search binds production to the current user question", () => {
	const body = searchKnowledgeBaseRequestBody({
		currentQuestion: "original user question",
		modelQuery: "model rewrite",
		top_k: 8,
		scope: ["file-a"],
		workspaceId: "ws-1",
	});
	assert.equal(body.query, "original user question");
	assert.deepEqual(body.query_routes, {
		production: "original user question",
		semantic: "model rewrite",
	});
	assert.deepEqual(body.file_ids, ["file-a"]);
	assert.equal(body.workspace_id, "ws-1");
	assert.equal("job_id" in body, false);
	assert.equal("case_id" in body, false);
});

test("chat search omits rewrite when it matches the current question", () => {
	const body = searchKnowledgeBaseRequestBody({
		currentQuestion: "绝缘电阻要求是什么？",
		modelQuery: "绝缘电阻要求是什么？",
		scope: ["f1"],
	});
	assert.deepEqual(body.query_routes, { production: "绝缘电阻要求是什么？" });
});

test("chat tool parameters do not expose file_ids or job identity", () => {
	const source = readFileSync(fileURLToPath(new URL("./chatTools.ts", import.meta.url)), "utf8");
	const searchParams = source.split('name: "search_knowledge_base"')[1].split("async execute")[0];
	assert.equal(searchParams.includes("file_ids"), false);
	assert.equal(searchParams.includes("job_id"), false);
	assert.equal(searchParams.includes("workspace_id"), false);
	const readParams = source.split('name: "read_chunk"')[1].split("async execute")[0];
	assert.equal(readParams.includes("file_ids"), false);
	assert.equal(readParams.includes("job_id"), false);
});

test("chat prompt is Q&A not audit JSON", () => {
	const prompt = chatSystemPrompt({ hasKnowledgeBase: true });
	assert.match(prompt, /search_knowledge_base/);
	assert.match(prompt, /read_chunk/);
	assert.match(prompt, /query_business_data/);
	assert.equal(prompt.includes("verdict"), false);
	assert.equal(prompt.includes("search_report_context"), false);
	assert.equal(prompt.includes("search_standards"), false);
	assert.equal(prompt.includes("file_ids"), false);
	assert.equal(prompt.includes("unevaluable"), false);
});

test("sidecar still serves audit case and chat turn as separate routes", () => {
	const source = readFileSync(fileURLToPath(new URL("./server.ts", import.meta.url)), "utf8");
	assert.match(source, /POST \/audit\/case/);
	assert.match(source, /POST \/chat\/turn/);
	assert.match(source, /handleAuditCase/);
	assert.match(source, /handleChatTurn/);
});

test("knowledge read gate asks for a read when search happened without read", () => {
	assert.equal(
		needsKnowledgeReadGate({
			searchCalls: 1,
			readCount: 0,
			answer: "要求见 GB/T 1094.1 第 3 页。",
		}),
		true,
	);
	assert.equal(
		needsKnowledgeReadGate({
			searchCalls: 1,
			readCount: 1,
			answer: "要求见 GB/T 1094.1 第 3 页。",
		}),
		false,
	);
	assert.equal(
		needsKnowledgeReadGate({
			searchCalls: 1,
			readCount: 0,
			answer: "知识库中没有检索到足够证据，暂时无法可靠回答。",
		}),
		false,
	);
	assert.equal(looksLikeAbstain("当前助手未绑定知识库文件，无法检索文档。"), true);
});

test("read_chunk allows only search hits in the bound file scope", () => {
	const allowed = new Set<string>();
	rememberSearchChunkIds(allowed, [{ chunk_id: "c1" }, { chunk_id: "c2" }, {}]);
	assert.deepEqual([...allowed].sort(), ["c1", "c2"]);
	assert.equal(denyUnreadChunk("c1", allowed), null);
	assert.equal(
		denyUnreadChunk("other-kb-chunk", allowed),
		"chunk_id must come from search_knowledge_base in this turn",
	);
	assert.equal(denyChunkFileScope("file-a", ["file-a", "file-b"]), null);
	assert.equal(
		denyChunkFileScope("file-other", ["file-a"]),
		"chunk does not belong to the bound knowledge-base files",
	);
});
