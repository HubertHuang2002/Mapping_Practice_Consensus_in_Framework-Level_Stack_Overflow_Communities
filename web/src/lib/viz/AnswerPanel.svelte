<!--
  AnswerPanel — right-edge slide-in detail surface (ported from render.py answerHTML/renderAnswer).
  Shows ONE original answer: its stats, every practice extracted from it (the clicked one flashes),
  and the full SO body rendered from markdown. Decoupled from CommunityCard — both can be open.

  Content is rendered declaratively (Svelte auto-escapes) except the answer body, which goes through
  mdToHtml (escape-first) into {@html}. The panel stays mounted and retains the last answer while it
  slides out, so closing animates cleanly instead of flashing empty.
-->
<script lang="ts">
	import { mdToHtml, fmtDate } from './markdown';

	let {
		viz,
		aid,
		pidx,
		colors,
		api
	}: {
		viz: any;
		aid: number | null;
		pidx: number | null;
		colors: Record<string, string>;
		api: string;
	} = $props();

	// retain the last opened answer so the slide-out doesn't flash empty
	let shown = $state<number | null>(null);
	$effect(() => {
		if (aid != null) shown = aid;
	});

	const open = $derived(aid != null);
	const answer = $derived(shown != null ? viz.answers[shown] : null);
	const practices = $derived(
		shown != null ? viz.points.filter((p: any) => p.answer_id === shown) : []
	);

	// author-level network authority A, shown in the "THIS AUTHOR" block beside the SO reputation —
	// faithfully tagged by provenance (SO vs ours). When A is absent, show n/a + the honest cause.
	const authPt = $derived(practices.length ? practices[0] : null);
	const authPct = $derived((authPt?.authority ?? null) as number | null); // A = PageRank rank percentile, 1.0 = top
	const authStatus = $derived(authPt?.authority_status ?? 'scored');
	const authVal = $derived(authPct != null ? `top ${Math.max(1, Math.round((1 - authPct) * 100))}%` : 'n/a');
	const authWhy = $derived(authStatus === 'anonymous' ? 'deleted' : 'not in network'); // shown only when A absent
	const authTitle = $derived(
		authPct != null
			? "this author's full-period PageRank rank percentile across the WHOLE React answerer network (1.0 = top) — a global standing, distinct from the per-question centrality the narrative reports"
			: authStatus === 'anonymous'
				? 'deleted / anonymous author — no network authority signal'
				: 'author is outside the answerer network (e.g. self-answer) — no network authority, but votes/accept still apply'
	);

	// the answer body is lazy-loaded from GET /answer/{id} (kept out of the breakdown cache; the
	// materialize step will slim it to a stub). Fetch on open; retain the last body during slide-out.
	let bodyText = $state<string | null>(null);
	let bodyFor = $state<number | null>(null);
	let bodyState = $state<'loading' | 'ready' | 'error'>('loading');
	const bodyReady = $derived(bodyState === 'ready' && bodyFor === shown && bodyText != null);
	$effect(() => {
		if (aid == null) return; // closing — keep the last body so the slide-out doesn't flash
		const id = aid;
		if (bodyFor === id) return; // already loaded / loading this answer
		bodyFor = id;
		bodyState = 'loading';
		fetch(`${api}/answer/${id}`)
			.then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
			.then((d) => {
				if (bodyFor === id) {
					bodyText = d.body;
					bodyState = 'ready';
				}
			})
			.catch(() => {
				if (bodyFor === id) {
					bodyText = null;
					bodyState = 'error';
				}
			});
	});

	const evChips = (ev: string) => ({
		code: ev === 'code' || ev === 'both',
		prose: ev === 'prose' || ev === 'both'
	});
	const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 2) + '…' : s);

	let bodyEl = $state<HTMLElement | undefined>();
	// transient orient: scroll the clicked practice into view + flash it (render.py flashPractice)
	$effect(() => {
		if (!open || pidx == null || !bodyEl) return;
		const el = bodyEl.querySelector<HTMLElement>(`[data-pidx="${pidx}"]`);
		if (el) {
			el.scrollIntoView({ block: 'nearest' });
			el.classList.remove('flash');
			void el.offsetWidth; // reflow so the animation re-triggers
			el.classList.add('flash');
		}
	});
</script>

<aside class="panel" class:open>
	{#if answer}
		<div class="pbody" bind:this={bodyEl}>
			<div class="sec">
				<div class="lab">ORIGINAL ANSWER #{shown}</div>
				<div class="meta">by <b>{answer.author || 'unknown'}</b> · {fmtDate(answer.date)}</div>

				<div class="lab2">THIS ANSWER</div>
				<div class="grid">
					<div><b>{answer.vote}</b><span>votes</span></div>
					<div>
						<b>{#if answer.is_accepted}<span class="gold">✓</span>{:else}—{/if}</b><span>accepted</span>
					</div>
				</div>

				<div class="lab2">THIS AUTHOR</div>
				<div class="authrows">
					<div class="arow">
						<span class="src so">SO</span><span class="anm">reputation</span><b
							>{answer.reputation ?? '—'}</b
						>
					</div>
					<div class="arow ours" class:na={authPct == null} title={authTitle}>
						<span class="src ours">ours</span><span class="anm">global network rank</span><b
							>{authVal}{#if authPct == null}<span class="why"> · {authWhy}</span>{/if}</b
						>
					</div>
				</div>

				<div class="lab2">EXTRACTED PRACTICES ({practices.length})</div>
				{#each practices as q (q.practice_index)}
					{@const ev = evChips(q.evidence_type)}
					<div class="prc" data-pidx={q.practice_index}>
						<span class="pdot" style:background={colors[q.cluster]}></span><span
							class="cnm"
							title={viz.clusters.find((c: any) => c.id === q.cluster)?.name}
							>{trunc(viz.clusters.find((c: any) => c.id === q.cluster)?.name ?? q.cluster, 42)}</span
						>{#if ev.code}<span class="chip">⟨⟩ code</span>{/if}{#if ev.prose}<span class="chip"
								>¶ prose</span
							>{/if}
						<div class="stmt">{q.text}</div>
						{#if q.conditions && q.conditions.length}
							<div class="when"><b>WHEN</b> · {q.conditions.join(' · ')}</div>
						{/if}
					</div>
				{/each}

				<div class="lab2">ANSWER CONTENT</div>
				<div class="body">
					{#if bodyReady}{@html mdToHtml(bodyText ?? '')}
					{:else if bodyState === 'error'}<span class="empty">couldn’t load answer body.</span>
					{:else}<span class="empty">loading…</span>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</aside>

<style>
	.panel {
		position: fixed;
		top: 0;
		right: 0;
		height: 100vh;
		width: 360px;
		z-index: 20;
		box-sizing: border-box;
		background: var(--panel);
		border-left: 1px solid var(--line);
		box-shadow: -8px 0 30px rgba(60, 50, 30, 0.1);
		transform: translateX(100%);
		transition: transform 0.26s cubic-bezier(0.4, 0, 0.2, 1);
		overflow-y: auto;
		padding: 16px 18px 28px;
	}
	.panel.open {
		transform: translateX(0);
	}
	.sec {
		margin: 4px 0 14px;
		padding-bottom: 12px;
		border-bottom: 1px solid var(--line);
	}
	.lab {
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.1em;
		color: var(--muted2);
		font-weight: 600;
		margin-bottom: 6px;
	}
	.lab2 {
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.08em;
		color: var(--muted2);
		font-weight: 600;
		margin: 12px 0 5px;
	}
	.grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 7px 9px;
		margin: 4px 0 8px;
	}
	.grid > div {
		background: var(--panel2);
		border-radius: 7px;
		padding: 6px 9px;
	}
	.grid b {
		display: block;
		font-family: var(--mono);
		font-size: 14.5px;
		font-weight: 600;
		color: var(--ink);
	}
	.grid span {
		font-family: var(--mono);
		font-size: 9.5px;
		color: var(--muted);
	}
	/* author-level provenance rows: SO's number (reputation) vs OUR derived metric (network authority),
	   each faithfully source-tagged so a derived value never reads as a raw SO fact */
	.authrows {
		display: flex;
		flex-direction: column;
		gap: 5px;
		margin: 4px 0 8px;
	}
	.arow {
		display: grid;
		grid-template-columns: auto 1fr auto;
		align-items: center;
		gap: 8px;
		background: var(--panel2);
		border-radius: 7px;
		padding: 6px 9px;
	}
	.arow.ours {
		box-shadow: inset 2px 0 0 #3a5a7a; /* accent spine = "our derived metric", not an SO fact */
	}
	.arow .anm {
		font-family: var(--mono);
		font-size: 10px;
		color: var(--muted);
	}
	.arow b {
		font-family: var(--mono);
		font-size: 13.5px;
		font-weight: 600;
		color: var(--ink);
	}
	.arow.na b {
		color: var(--muted2);
		font-style: italic;
		font-size: 12px;
	}
	.arow .why {
		font-weight: 400;
		font-size: 9.5px;
		color: var(--muted);
	}
	.src {
		font-family: var(--mono);
		font-size: 8px;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		padding: 2px 5px;
		border-radius: 4px;
	}
	.src.so {
		color: #8a6d2f;
		background: #eee7d8;
	}
	.src.ours {
		color: #fbf8f2;
		background: #3a5a7a;
	}
	.meta {
		font-size: 11.5px;
		color: var(--muted);
		margin: 2px 0 4px;
	}
	.gold {
		color: var(--accepted);
	}

	/* practice cards — uniform; the clicked one flashes via .flash */
	.prc {
		background: #fcfaf5;
		border: 1px solid #efe9dc;
		border-radius: 8px;
		padding: 9px 11px;
		margin: 7px 0;
	}
	.prc .cnm {
		font-family: var(--display);
		font-weight: 600;
		font-size: 13px;
		color: var(--ink);
	}
	.prc .pdot {
		display: inline-block;
		width: 8px;
		height: 8px;
		border-radius: 50%;
		margin-right: 6px;
		vertical-align: middle;
	}
	.prc .stmt {
		font-family: var(--serif);
		font-size: 13.5px;
		line-height: 1.5;
		color: var(--ink);
		margin-top: 4px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		font-family: var(--mono);
		font-size: 8.5px;
		font-weight: 600;
		color: #5b6470;
		background: #eee7d8;
		border-radius: 4px;
		padding: 1px 6px;
		margin-left: 5px;
		vertical-align: middle;
	}
	.when {
		margin-top: 6px;
		font-family: var(--mono);
		font-size: 10px;
		color: var(--muted);
		line-height: 1.5;
	}
	.when b {
		color: #8a6d2f;
	}
	/* transient orient on click: fade-in then settle to the default card bg (no persistent state) */
	:global(.prc.flash) {
		animation: prflash 1.15s ease-out;
	}
	@keyframes prflash {
		0% {
			background: #ece4d2;
			box-shadow: 0 0 0 2px rgba(36, 26, 18, 0.32);
		}
		100% {
			background: #fcfaf5;
			box-shadow: 0 0 0 2px rgba(36, 26, 18, 0);
		}
	}

	/* answer body — rendered from {@html}; descendants lack the scope hash, so globalize them under
	   the scoped .body ancestor (contained, not leaked) */
	.body {
		font-family: var(--serif);
		font-size: 12.5px;
		line-height: 1.55;
		color: var(--ink);
		word-break: break-word;
		max-height: 46vh;
		overflow-y: auto;
		background: var(--panel2);
		border-radius: 8px;
		padding: 11px 12px;
	}
	.body :global(p) {
		margin: 0 0 9px;
	}
	.body :global(p:last-child) {
		margin-bottom: 0;
	}
	.body :global(code) {
		font-family: var(--mono);
		font-size: 11px;
		background: #efe7d6;
		border: 1px solid #e5dbc6;
		border-radius: 3px;
		padding: 0 4px;
	}
	.body :global(pre.code) {
		font-family: var(--mono);
		font-size: 11.5px;
		line-height: 1.5;
		color: var(--ink);
		background: #f3efe6;
		border: 1px solid #e2d9c6;
		border-radius: 7px;
		padding: 10px 11px;
		margin: 8px 0;
		overflow-x: auto;
	}
	.body :global(pre.code code) {
		font: inherit;
		background: none;
		border: none;
		padding: 0;
	}
	.body :global(ul),
	.body :global(ol) {
		margin: 6px 0 9px;
		padding-left: 20px;
	}
	.body :global(li) {
		margin: 3px 0;
	}
	.body :global(a) {
		color: #3a5a7a;
		text-decoration: underline;
		text-underline-offset: 2px;
	}
	.body :global(blockquote) {
		margin: 7px 0;
		padding: 2px 0 2px 11px;
		border-left: 3px solid var(--line);
		color: var(--muted);
	}
	.body :global(.empty) {
		color: var(--muted);
	}
</style>
