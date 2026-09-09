/**
 * Session-local evidence progress for search_standards.
 *
 * First-round chunk ids are seeded as already seen. A search that only
 * returns those cards has new_count=0. Two consecutive zero-new searches
 * block further search; read_chunk stays allowed.
 */
export type SearchProgress = {
	query: string;
	returned: number;
	newCount: number;
	newRatio: number;
	consecutiveZero: number;
	blocked: boolean;
};

export type ProgressSnapshot = {
	seen_chunk_ids: string[];
	search_count: number;
	zero_new_searches: number;
	consecutive_zero_new_searches: number;
	search_blocked: boolean;
};

export function normalizeChunkId(value: unknown): string {
	return String(value ?? "").trim();
}

export function createEvidenceProgress(firstRoundIds: Iterable<unknown> = []) {
	const seen = new Set<string>();
	for (const raw of firstRoundIds) {
		const id = normalizeChunkId(raw);
		if (id) seen.add(id);
	}
	let searchCount = 0;
	let zeroNew = 0;
	let consecutiveZero = 0;
	let blocked = false;

	return {
		recordSearch(query: string, hitIds: Iterable<unknown>): SearchProgress {
			searchCount += 1;
			const returned: string[] = [];
			for (const raw of hitIds) {
				const id = normalizeChunkId(raw);
				if (id && !returned.includes(id)) returned.push(id);
			}
			const novel = returned.filter((id) => !seen.has(id));
			const newCount = novel.length;
			const newRatio = returned.length ? newCount / returned.length : 0;
			if (newCount === 0) {
				consecutiveZero += 1;
				zeroNew += 1;
			} else {
				consecutiveZero = 0;
			}
			for (const id of returned) seen.add(id);
			if (consecutiveZero >= 2) blocked = true;
			return {
				query: String(query || "").trim(),
				returned: returned.length,
				newCount,
				newRatio,
				consecutiveZero,
				blocked,
			};
		},
		recordRead(chunkId: unknown): void {
			const id = normalizeChunkId(chunkId);
			if (id) seen.add(id);
		},
		isSearchBlocked(): boolean {
			return blocked;
		},
		formatSearchNote(progress: SearchProgress): string {
			const lines = [
				`本次检索：`,
				`${progress.returned} 个结果`,
				`新增 chunk：${progress.newCount}`,
				`历史已见：${progress.returned - progress.newCount}`,
			];
			if (progress.newCount === 0) {
				lines.push(
					``,
					`该检索没有获得新证据。`,
					`请不要继续使用相似查询；若已有证据不足，应考虑输出 unevaluable。`,
				);
			}
			if (progress.blocked) {
				lines.push(
					``,
					`已连续两次检索没有新增 chunk，后续 search_standards 将被阻止。请 read 尚未读过的已有片段，或基于现有证据收尾。`,
				);
			}
			return lines.join("\n");
		},
		searchBlockedMessage(): string {
			return [
				`search_standards 已暂停：连续两次检索没有新增 chunk。`,
				`请对尚未 read 的已有 chunk_id 执行 read_chunk，或基于现有证据输出最终 JSON。`,
				`若现有证据不足以证明限值，应输出 unevaluable，不要再换相似检索式。`,
			].join("\n");
		},
		snapshot(): ProgressSnapshot {
			return {
				seen_chunk_ids: [...seen],
				search_count: searchCount,
				zero_new_searches: zeroNew,
				consecutive_zero_new_searches: consecutiveZero,
				search_blocked: blocked,
			};
		},
	};
}

export type EvidenceProgress = ReturnType<typeof createEvidenceProgress>;
