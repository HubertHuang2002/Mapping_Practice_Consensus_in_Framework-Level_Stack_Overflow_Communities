<script lang="ts">
	import { onMount } from 'svelte';
	import ForceField from '$lib/viz/ForceField.svelte';
	import CommunityCard from '$lib/viz/CommunityCard.svelte';
	import AnswerPanel from '$lib/viz/AnswerPanel.svelte';
	import Narrative from '$lib/viz/Narrative.svelte';
	import QueryComposer from '$lib/viz/QueryComposer.svelte';
	import Waiting from '$lib/viz/Waiting.svelte';
	import { clusterColors } from '$lib/viz/palette';

	// Dev: the FastAPI serve plane. (TODO: env-config for build.)
	const API = 'http://localhost:8000';

	type Phase = 'landing' | 'baking' | 'dashboard';
	type Entry = { q: string; group_id: string | null };

	let phase = $state<Phase>('landing');
	let viz = $state<any>(null);
	let err = $state<string | null>(null);

	// cold-path interstitial state (drives <Waiting>)
	let askText = $state('');
	let ready = $state(false); // the Breakdown JSON is actually in hand
	let cached = $state(false); // warm hit vs fresh bake — only affects the closing caption
	let runId = $state(0); // remounts <Waiting> per submission so its frames/check replay
	let pendingViz: any = null; // stashed until the check finishes, then handed to the dashboard
	let bakeStage = $state<string | null>(null); // live stage from the poll (extract|cluster|narrate|…)
	let bakeProg = $state<{ k: number; n: number } | null>(null); // extract sub-progress (answers gated)

	// the landing list = every baked group from the serve plane (GET /queries now carries each group's
	// canonical question title). The FULL set, newest-baked first — not this browser's local history.
	// No seed/placeholder: an empty cache shows an empty list (just the composer), never a fake "baked"
	// card — a hardcoded seed would mislead into thinking a group is warm when it isn't.
	let history = $state<Entry[]>([]);

	async function loadQueries() {
		try {
			const res = await fetch(`${API}/queries`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const { queries } = await res.json();
			history = queries.map((q: any) => ({
				q: q.title || q.query_id,
				group_id: q.query_id
			}));
		} catch {
			history = [];
		}
	}

	onMount(loadQueries);

	// keep the just-submitted query at the top in-session; the canonical list re-syncs from /queries on
	// the next landing visit / reload.
	function rememberQuery(q: string, group_id: string | null) {
		if (!group_id) return;
		history = [{ q, group_id }, ...history.filter((e) => e.group_id !== group_id)];
	}

	function startBaking(text: string) {
		askText = text;
		ready = false;
		cached = false;
		bakeStage = null;
		bakeProg = null;
		err = null;
		runId += 1;
		phase = 'baking';
	}
	function failBaking(msg: string) {
		err = msg;
		phase = 'landing';
	}

	// fetch a baked Breakdown; returns 'ok' | '202' | '404' | 'err'
	async function fetchBreakdownInto(group_id: string, allow404 = false): Promise<string> {
		let res: Response;
		try {
			res = await fetch(`${API}/breakdown/${group_id}`);
		} catch {
			failBaking('serve plane unreachable on :8000');
			return 'err';
		}
		if (res.status === 202) return '202';
		if (res.status === 404 && allow404) return '404';
		if (!res.ok) {
			failBaking(`HTTP ${res.status}`);
			return 'err';
		}
		pendingViz = await res.json();
		ready = true; // → <Waiting> plays the completion check, then calls onDone
		return 'ok';
	}

	function pollBreakdown(group_id: string): Promise<void> {
		return new Promise((resolve) => {
			let tries = 0;
			const iv = setInterval(async () => {
				tries += 1;
				if (tries > 150) {
					clearInterval(iv);
					failBaking('bake timed out');
					return resolve();
				}
				let res: Response;
				try {
					res = await fetch(`${API}/breakdown/${group_id}`);
				} catch {
					return; // transient — keep polling
				}
				if (res.status === 202) {
					try {
						const b = await res.json(); // 202 body carries live {stage, k, n}
						if (b.stage) bakeStage = b.stage;
						bakeProg = b.k != null && b.n != null ? { k: b.k, n: b.n } : bakeProg;
					} catch {
						/* no progress body yet — keep the current stage */
					}
					return; // still baking
				}
				clearInterval(iv);
				if (res.ok) {
					pendingViz = await res.json();
					ready = true;
				} else {
					failBaking(`HTTP ${res.status}`);
				}
				resolve();
			}, 2000);
		});
	}

	async function submit(text: string) {
		startBaking(text);
		let group_id: string | null = null;
		let status = 'baking';
		try {
			const res = await fetch(`${API}/queries`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ query_text: text })
			});
			if (res.status === 422) {
				failBaking('No equivalent React questions found — try rephrasing the problem.');
				return;
			}
			if (!res.ok) {
				failBaking(`HTTP ${res.status}`);
				return;
			}
			({ group_id, status } = await res.json());
		} catch {
			failBaking('serve plane unreachable on :8000');
			return;
		}
		rememberQuery(text, group_id);
		if (!group_id) return failBaking('could not resolve that query');
		if (status === 'ready') {
			cached = true;
			await fetchBreakdownInto(group_id);
		} else {
			await pollBreakdown(group_id);
		}
	}

	async function pick(e: Entry) {
		if (!e.group_id) return submit(e.q);
		startBaking(e.q);
		cached = true; // a remembered group is expected to be warm
		const r = await fetchBreakdownInto(e.group_id, true);
		if (r === '404') {
			cached = false;
			return submit(e.q); // group was evicted — re-resolve
		}
		if (r === '202') {
			cached = false; // still baking from earlier — fall to the live pipeline
			await pollBreakdown(e.group_id);
		}
	}

	function onWaitingDone() {
		viz = pendingViz;
		pendingViz = null;
		narrPhase = 'intro';
		phase = 'dashboard';
	}

	// ── dashboard view state (unchanged) ───────────────────────────────────────────────────────────
	let commCluster = $state<string | null>(null);
	let selAnswer = $state<number | null>(null);
	let selPractice = $state<number | null>(null);

	const colors = $derived(viz ? clusterColors(viz.clusters) : {});

	const narrative = $derived(viz?.meta?.narrative ?? null);
	let narrPhase = $state<'intro' | 'explore'>('intro');
	$effect(() => {
		if (narrative && narrPhase === 'intro') dismissDetails();
	});

	function handleCommunity(cid: string) {
		commCluster = cid;
		selAnswer = null;
		selPractice = null;
	}
	function handleAnswer(aid: number, cid: string, pidx: number) {
		commCluster = cid;
		selAnswer = aid;
		selPractice = pidx;
	}
	function dismissDetails() {
		commCluster = null;
		selAnswer = null;
		selPractice = null;
	}
</script>

{#if phase === 'landing'}
	<QueryComposer {history} onsubmit={submit} onpick={pick} />
	{#if err}
		<div class="status err">⚠ {err}</div>
	{/if}
{/if}

{#if phase === 'baking'}
	{#key runId}
		<Waiting query={askText} {ready} {cached} stage={bakeStage} prog={bakeProg} ondone={onWaitingDone} />
	{/key}
{/if}

{#if phase === 'dashboard' && viz}
	<ForceField
		{viz}
		{colors}
		highlightAnswer={selAnswer}
		onCommunity={handleCommunity}
		onAnswer={handleAnswer}
		onBackground={dismissDetails}
	/>
	<CommunityCard {viz} cid={commCluster} {colors} />
	<AnswerPanel {viz} aid={selAnswer} pidx={selPractice} {colors} api={API} />

	<Narrative
		{narrative}
		nPoints={viz.meta.n_points}
		nClusters={viz.meta.n_clusters}
		bind:phase={narrPhase}
	/>
{/if}

<style>
	.status {
		position: fixed;
		bottom: 38px;
		left: 50%;
		transform: translateX(-50%);
		z-index: 40;
		font-family: var(--mono);
		font-size: 12px;
		color: var(--muted);
	}
	.status.err {
		color: #b3261e;
		max-width: 80vw;
		text-align: center;
	}
</style>
