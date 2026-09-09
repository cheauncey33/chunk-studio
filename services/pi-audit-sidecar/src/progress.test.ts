import assert from "node:assert/strict";
import { test } from "node:test";

import { createEvidenceProgress } from "./progress.ts";

test("first-round ids count as already seen", () => {
	const progress = createEvidenceProgress(["a", "b", "c"]);
	const first = progress.recordSearch("q1", ["a", "b", "c", "d"]);
	assert.equal(first.newCount, 1);
	assert.equal(first.returned, 4);
	assert.equal(first.consecutiveZero, 0);
	assert.equal(first.blocked, false);
});

test("two consecutive zero-new searches block further search", () => {
	const progress = createEvidenceProgress(["a", "b"]);
	const s1 = progress.recordSearch("q1", ["a", "b"]);
	assert.equal(s1.newCount, 0);
	assert.equal(s1.blocked, false);
	const s2 = progress.recordSearch("q2", ["b", "a"]);
	assert.equal(s2.newCount, 0);
	assert.equal(s2.blocked, true);
	assert.equal(progress.isSearchBlocked(), true);
	assert.equal(progress.snapshot().zero_new_searches, 2);
});

test("a productive search resets the consecutive zero streak", () => {
	const progress = createEvidenceProgress(["a"]);
	progress.recordSearch("q1", ["a"]);
	progress.recordSearch("q2", ["a", "z"]);
	assert.equal(progress.isSearchBlocked(), false);
	assert.equal(progress.snapshot().consecutive_zero_new_searches, 0);
	progress.recordSearch("q3", ["a", "z"]);
	progress.recordSearch("q4", ["z"]);
	assert.equal(progress.isSearchBlocked(), true);
});
