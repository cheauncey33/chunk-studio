/**
 * Host-side evidence closure for match/mismatch.
 *
 * Does not re-judge the claim. It only asks: can the cited/read standard
 * text locate the Agent's stated standard_value via direct number, unit
 * conversion, or a two-addend formula? Otherwise flip to unevaluable.
 */
export type ClosureMode = "direct" | "unit" | "formula" | "verbatim";

export type ClosureResult = {
	applied: boolean;
	passed: boolean;
	mode: ClosureMode | null;
	reason: string;
};

export type Quantity = {
	value: number;
	unit: string | null;
	raw: string;
};

const UNIT_BASE: Record<string, [string, number]> = {
	"%": ["ratio", 0.01],
	w: ["w", 1],
	kw: ["w", 1000],
	mw: ["w", 1_000_000],
	v: ["v", 1],
	kv: ["v", 1000],
	a: ["a", 1],
	ka: ["a", 1000],
	s: ["s", 1],
	ms: ["s", 0.001],
	hz: ["hz", 1],
	khz: ["hz", 1000],
	kva: ["va", 1000],
	va: ["va", 1],
};

const UNIT_RE = "(?:kVA|kW|kV|kHz|Hz|ms|%|％|V|A|W|s)";
const NUM_RE = "-?\\d+(?:\\.\\d+)?";
const TOKEN_RE = new RegExp(`(${NUM_RE})\\s*(${UNIT_RE})?`, "gi");
function isAllowanceOne(source: string, index: number, token: string, unit: string | undefined): boolean {
	if (token !== "1" || unit) return false;
	const before = source.slice(Math.max(0, index - 3), index);
	const after = source.slice(index + token.length, index + token.length + 2);
	return /\(\s*$/.test(before) && /^[+±]/.test(after);
}

function closeEnough(left: number, right: number): boolean {
	return Math.abs(left - right) <= Math.max(1e-6, Math.abs(right) * 1e-4);
}

function foldUnit(unit: string | null): string | null {
	if (!unit) return null;
	return unit.replace("％", "%").replace("°C", "c").replace("℃", "c").toLowerCase();
}

function toBase(value: number, unit: string | null): { dim: string; base: number } {
	const folded = foldUnit(unit);
	if (!folded) return { dim: "unitless", base: value };
	const hit = UNIT_BASE[folded];
	if (!hit) return { dim: folded, base: value };
	return { dim: hit[0], base: value * hit[1] };
}

function comparable(left: Quantity, right: Quantity): boolean {
	const a = toBase(left.value, left.unit);
	const b = toBase(right.value, right.unit);
	if (a.dim === b.dim) return closeEnough(a.base, b.base);
	if (a.dim === "unitless" && b.dim === "ratio") return closeEnough(a.base, b.base);
	if (b.dim === "unitless" && a.dim === "ratio") return closeEnough(b.base, a.base);
	return false;
}

function collapseThousands(text: string): string {
	// GB tables write 3615 as "3 615". Collapse space/nbsp/comma groups of 3.
	return String(text || "").replace(
		/(?<![0-9.])(\d{1,3}(?:[\u00a0 ,]\d{3})+)(?![0-9])/g,
		(token) => token.replace(/[\u00a0 ,]/g, ""),
	);
}

export function parseQuantities(text: string): Quantity[] {
	const source = collapseThousands(text);
	const out: Quantity[] = [];
	TOKEN_RE.lastIndex = 0;
	let match: RegExpExecArray | null;
	while ((match = TOKEN_RE.exec(source))) {
		const index = match.index;
		if (isAllowanceOne(source, index, match[1], match[2])) continue;
		out.push({
			value: Number(match[1]),
			unit: match[2] ? foldUnit(match[2]) : null,
			raw: match[0].replace(/\s+/g, ""),
		});
	}
	return out;
}

function locateDirect(qty: Quantity, corpus: string): boolean {
	const percent = qty.unit === "%";
	const body = Number.isInteger(qty.value) ? String(qty.value) : String(qty.value);
	const escaped = body.replace(".", "\\.");
	if (percent) {
		if (new RegExp(`(?<![0-9.])${escaped}\\s*%`, "i").test(corpus)) return true;
		if (new RegExp(`(?:≤|>=|≥|<=|<|>)\\s*${escaped}(?![0-9])`).test(corpus)) return true;
		if (!Number.isInteger(qty.value) && new RegExp(`(?<![0-9.])${escaped}(?![0-9])`).test(corpus)) {
			return true;
		}
		return false;
	}
	return new RegExp(`(?<![0-9.])${escaped}(?![0-9])`).test(corpus);
}

function locateUnit(qty: Quantity, found: Quantity[]): boolean {
	return found.some((item) => comparable(qty, item));
}

function locateFormula(target: Quantity, found: Quantity[]): boolean {
	const want = toBase(target.value, target.unit);
	for (let i = 0; i < found.length; i += 1) {
		for (let j = i + 1; j < found.length; j += 1) {
			const left = toBase(found[i].value, found[i].unit);
			const right = toBase(found[j].value, found[j].unit);
			const sameDim = left.dim === right.dim;
			const loose =
				left.dim === "unitless" ||
				right.dim === "unitless" ||
				(left.dim === "ratio" && right.dim === "ratio");
			if (!sameDim && !loose) continue;
			const sum = left.base + right.base;
			if (closeEnough(sum, want.base)) return true;
		}
	}
	return false;
}

function verbatimNeedle(standardValue: string): string | null {
	const stripped = String(standardValue || "")
		.replace(new RegExp(`${NUM_RE}\\s*(${UNIT_RE})?`, "gi"), " ")
		.replace(/[≤≥<>±+()（）:：%,，。；;]/g, " ")
		.trim();
	const token = stripped.split(/\s+/).find((item) => item.length >= 2);
	return token || null;
}

function asBodyMap(
	readBodies: Map<string, string> | Record<string, string> | undefined,
): Map<string, string> {
	if (!readBodies) return new Map();
	return readBodies instanceof Map ? readBodies : new Map(Object.entries(readBodies));
}

function citedChunkId(item: Record<string, unknown> | null | undefined): string {
	const chunkId = String(item?.chunk_id || "").trim();
	if (!chunkId || chunkId.includes("placeholder")) return "";
	return chunkId;
}

export function evidenceCorpus(
	evidence: Array<Record<string, unknown>> | null | undefined,
	readBodies: Map<string, string> | Record<string, string> = new Map(),
): string {
	const bodies = asBodyMap(readBodies);
	const parts: string[] = [];
	for (const item of evidence || []) {
		const chunkId = citedChunkId(item);
		if (!chunkId || !bodies.has(chunkId)) continue;
		parts.push(String(bodies.get(chunkId) || ""));
	}
	return collapseThousands(parts.join("\n").replace(/<[^>]+>/g, " "));
}

function locateQuantity(
	qty: Quantity,
	corpus: string,
	found: Quantity[],
	kind: string,
): ClosureMode | null {
	if (locateDirect(qty, corpus)) return "direct";
	if (locateUnit(qty, found)) return "unit";
	if (kind === "formula_aggregate" && locateFormula(qty, found)) return "formula";
	return null;
}

function combineModes(modes: ClosureMode[]): ClosureMode {
	if (modes.length > 0 && modes.every((mode) => mode === "formula")) return "formula";
	if (modes.some((mode) => mode === "unit")) return "unit";
	return "direct";
}

export function closeEvidence(input: {
	verdict?: unknown;
	kind?: unknown;
	standard_value?: unknown;
	evidence?: Array<Record<string, unknown>> | null;
	readBodies?: Map<string, string> | Record<string, string>;
}): ClosureResult {
	const verdict = String(input.verdict || "");
	if (verdict !== "match" && verdict !== "mismatch") {
		return { applied: false, passed: true, mode: null, reason: "not_match_mismatch" };
	}
	const standardValue = String(input.standard_value || "").trim();
	const evidence = input.evidence || [];
	const bodies = asBodyMap(input.readBodies);
	const cited = evidence.map(citedChunkId).filter(Boolean);
	const unread = cited.filter((id) => !bodies.has(id));
	const corpus = evidenceCorpus(evidence, bodies);
	if (!corpus.trim()) {
		const reason = cited.length > 0 && unread.length === cited.length ? "cited_chunks_not_read" : "no_evidence_text";
		return { applied: true, passed: false, mode: null, reason };
	}
	if (!standardValue) {
		return { applied: true, passed: false, mode: null, reason: "no_standard_value" };
	}

	const targets = parseQuantities(standardValue);
	const found = parseQuantities(corpus);
	const kind = String(input.kind || "");

	if (targets.length > 0) {
		const modes: ClosureMode[] = [];
		for (const qty of targets) {
			const mode = locateQuantity(qty, corpus, found, kind);
			if (!mode) {
				return {
					applied: true,
					passed: false,
					mode: null,
					reason: `quantity_not_in_evidence:${qty.raw}`,
				};
			}
			modes.push(mode);
		}
		return {
			applied: true,
			passed: true,
			mode: combineModes(modes),
			reason: "all_quantities_in_evidence",
		};
	}

	const needle = verbatimNeedle(standardValue);
	if (needle && corpus.includes(needle)) {
		return { applied: true, passed: true, mode: "verbatim", reason: "label_in_evidence" };
	}
	if (needle) {
		return { applied: true, passed: false, mode: null, reason: "label_not_in_evidence" };
	}
	return { applied: true, passed: false, mode: null, reason: "standard_value_not_in_evidence" };
}

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
	const closure = closeEvidence({
		verdict: result.verdict,
		kind: result.kind,
		standard_value: result.standard_value,
		evidence: Array.isArray(result.evidence) ? (result.evidence as Array<Record<string, unknown>>) : [],
		readBodies,
	});
	const raw_verdict = result.verdict;
	const raw_kind = result.kind;
	if (!closure.applied || closure.passed) {
		return { result, closure, raw_verdict, raw_kind };
	}
	return {
		result: {
			...result,
			verdict: "unevaluable",
			kind: "standard_not_found",
		},
		closure,
		raw_verdict,
		raw_kind,
	};
}
