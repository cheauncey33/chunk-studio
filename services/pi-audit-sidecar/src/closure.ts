/**
 * Host-side evidence provenance for match/mismatch.
 *
 * Does not re-judge the claim. It only asks whether each cited chunk_id was
 * actually read. Authoritative evidence.text comes from readBodies, never
 * from the model.
 */
export type ClosureMode = "read";

export type ClosureResult = {
	applied: boolean;
	passed: boolean;
	mode: ClosureMode | null;
	reason: string;
};

function asBodyMap(
	readBodies: Map<string, string> | Record<string, string> | undefined,
): Map<string, string> {
	if (!readBodies) return new Map();
	return readBodies instanceof Map ? readBodies : new Map(Object.entries(readBodies));
}

export function citedChunkId(item: Record<string, unknown> | null | undefined): string {
	const chunkId = String(item?.chunk_id || "").trim();
	if (!chunkId || chunkId.includes("placeholder")) return "";
	return chunkId;
}

export function closeEvidence(input: {
	verdict?: unknown;
	evidence?: Array<Record<string, unknown>> | null;
	readBodies?: Map<string, string> | Record<string, string>;
}): ClosureResult {
	const verdict = String(input.verdict || "");
	if (verdict !== "match" && verdict !== "mismatch") {
		return { applied: false, passed: true, mode: null, reason: "not_match_mismatch" };
	}
	const evidence = input.evidence || [];
	const bodies = asBodyMap(input.readBodies);
	const cited = evidence.map(citedChunkId).filter(Boolean);
	if (cited.length === 0) {
		return { applied: true, passed: false, mode: null, reason: "no_cited_chunk" };
	}
	const unread = cited.filter((id) => !bodies.has(id));
	if (unread.length > 0) {
		return { applied: true, passed: false, mode: null, reason: "cited_chunks_not_read" };
	}
	return { applied: true, passed: true, mode: "read", reason: "cited_chunks_read" };
}

/** Replace agent-written evidence.text with the bodies actually returned by read_chunk. */
export function attachAuthoritativeEvidence<T extends Record<string, unknown>>(
	result: T | null,
	readBodies?: Map<string, string> | Record<string, string>,
): T | null {
	if (!result) return result;
	const evidence = Array.isArray(result.evidence) ? result.evidence : [];
	const bodies = asBodyMap(readBodies);
	const next = evidence.map((item) => {
		if (!item || typeof item !== "object") return item;
		const rec = { ...(item as Record<string, unknown>) };
		delete rec.text;
		const chunkId = citedChunkId(rec);
		if (chunkId && bodies.has(chunkId)) {
			rec.text = bodies.get(chunkId);
		}
		return rec;
	});
	return { ...result, evidence: next };
}

/**
 * Seal evidence provenance without rewriting the agent verdict.
 * Failed citation is a protocol error for the caller to record, not unevaluable.
 */
export function applyEvidenceClosure<T extends Record<string, unknown>>(
	result: T | null,
	readBodies?: Map<string, string> | Record<string, string>,
): { result: T | null; closure: ClosureResult; raw_verdict: unknown; raw_kind: unknown } {
	if (!result) {
		return {
			result,
			closure: { applied: false, passed: true, mode: null, reason: "no_result" },
			raw_verdict: null,
			raw_kind: null,
		};
	}
	const evidence = Array.isArray(result.evidence)
		? (result.evidence as Array<Record<string, unknown>>)
		: [];
	const closure = closeEvidence({
		verdict: result.verdict,
		evidence,
		readBodies,
	});
	return {
		result: attachAuthoritativeEvidence(result, readBodies),
		closure,
		raw_verdict: result.verdict,
		raw_kind: result.kind,
	};
}
