<!--
  Narrative — the per-query narrative surface (signal-table v2, spec §1.2).
  A martini-glass structure (Segel & Heer): an author-driven Overture opens to reader-driven
  exploration, with the verdict persisting as a top-left masthead you can reopen at any time.

    · phase 'intro'   → full-screen Overture: kicker, quoted query, headline (the verdict), then the
                        TWO-AXIS comparison — BY VOTES (the crowd's leading practice + its share) and
                        BY AUTHORITY (the practice the most-central voice backs + its centrality).
                        Both axes name a PRACTICE, so the relation is just "are the two the same":
                        a single Phosphor equals / not-equals icon in the gap (one matched icon family,
                        so the two read identically). Then the describe-only body + "explore ↓".
    · phase 'explore' → the Overture dissolves; the verdict collapses to a borderless TOP-LEFT
                        masthead — glyph + headline + a small stats line (no comparison). Click to reopen.

  Reads viz.meta.narrative verbatim; renders NOTHING when narrative is null (cold groups, pre-narrator).
  Every verdict/number is COMPUTED upstream (query_signal/camp_signal, surfaced by narrative.py); this
  component only lays them out. `phase` is $bindable so +page can close the detail surfaces on reopen.
-->
<script lang="ts">
	let {
		narrative = null,
		nPoints = 0,
		nClusters = 0,
		phase = $bindable('intro')
	}: {
		narrative?: any;
		nPoints?: number;
		nClusters?: number;
		phase?: 'intro' | 'explore';
	} = $props();

	// RQ-1 shape (from votes) → glyph. v2 vocabulary: consensus / polarization / fragmentation.
	const SHAPE: Record<string, string> = {
		consensus: '●',
		polarization: '◐',
		fragmentation: '◌'
	};
	const shapeGlyph = $derived(SHAPE[narrative?.shape] ?? '◐');
	const pct = (x: number | null | undefined) => `${Math.round(100 * (x ?? 0))}%`;

	// the authority overlay is readable only when a central voice exists AND coverage is sufficient
	const authReadable = $derived(
		!!narrative?.top1_author && (narrative?.authority_coverage ?? 0) >= 0.5
	);
	// the relation between the two axes: same practice (agree) vs different practice (diverge)
	const diverges = $derived(!!narrative?.authority_diverges);
</script>

{#if narrative}
	<!-- intro: full-screen Overture -->
	<div class="scrim" class:on={phase === 'intro'}></div>
	<div class="overture" class:on={phase === 'intro'} aria-hidden={phase !== 'intro'}>
		<div class="kicker">Asked · canonical group of {narrative.group_size} questions</div>
		<div class="q">“{narrative.query}”</div>
		<div class="rule"></div>
		<div class="head">{narrative.headline}</div>

		<div class="split">
			<!-- BY VOTES: the crowd's leading practice + its vote share -->
			<div class="col left">
				<div class="clbl">
					<span class="dot"></span>By votes · the crowd
					<!-- shape pill: the dashed border (.fragile) alone signals a borderline label; the body
					     carries the nuance, so no extra word is appended. -->
					<span class="pill" class:fragile={narrative.shape_fragile}>{narrative.shape}</span>
				</div>
				<div class="lead">{narrative.dominant_approach}</div>
				<div class="big">{pct(narrative.vote_leader_share)} <small>of votes</small></div>
				{#if narrative.runner_up}
					<div class="sub">runner-up: <b>{narrative.runner_up}</b> · {pct(narrative.runner_up_share)}</div>
				{/if}
				<div class="foot">
					{#if narrative.effective_camps != null}{narrative.effective_camps.toFixed(1)} effective camps{/if}
				</div>
			</div>

			<!-- BY AUTHORITY: the practice the most-central voice backs + its centrality -->
			<div class="col right">
				{#if authReadable}
					<div class="clbl">
						<span class="dot"></span>By authority · centrality
						{#if narrative.single_voice_dominated}<span class="pill">one voice</span>{/if}
					</div>
					<div class="lead">{narrative.authority_backed}</div>
					<div class="big">{pct(narrative.top1_pr_share)} <small>centrality</small></div>
					<div class="sub">
						most-central voice <b>{narrative.top1_author}</b>{#if narrative.authority_backed_share != null}
							· this camp holds {pct(narrative.authority_backed_share)} of votes{/if}
					</div>
					<div class="foot">coverage {pct(narrative.authority_coverage)}</div>
				{:else}
					<div class="clbl"><span class="dot"></span>By authority · centrality</div>
					<div class="thin">
						authority coverage only {pct(narrative.authority_coverage)} — too thin to read for this question.
					</div>
				{/if}
			</div>

			<!-- the relation icon: Phosphor equals (agree) / not-equals (diverge) — one matched icon
			     family, so the two never read as different typefaces. Ink only, no colour split. -->
			{#if authReadable}
				<div class="seam" aria-hidden="true">
					{#if diverges}
						<svg viewBox="0 0 256 256" fill="currentColor"
							><path
								d="M224,160a8,8,0,0,1-8,8H102.45L53.92,221.38a8,8,0,0,1-11.84-10.76L80.82,168H40a8,8,0,0,1,0-16H95.37L139,104H40a8,8,0,0,1,0-16H153.55l48.53-53.38a8,8,0,0,1,11.84,10.76L175.18,88H216a8,8,0,0,1,0,16H160.63L117,152h99A8,8,0,0,1,224,160Z"
							/></svg
						>
					{:else}
						<svg viewBox="0 0 256 256" fill="currentColor"
							><path
								d="M224,160a8,8,0,0,1-8,8H40a8,8,0,0,1,0-16H216A8,8,0,0,1,224,160ZM40,104H216a8,8,0,0,0,0-16H40a8,8,0,0,0,0,16Z"
							/></svg
						>
					{/if}
				</div>
			{/if}
		</div>

		<div class="body">{narrative.body}</div>
		<button class="ncta" onclick={() => (phase = 'explore')}>explore the field&nbsp;↓</button>
	</div>

	<!-- explore: collapsed masthead, top-left — stats only, no comparison. Click to reopen the Overture. -->
	<div
		class="title"
		class:on={phase === 'explore'}
		role="button"
		tabindex="0"
		aria-label="Reopen the narrative overture"
		onclick={() => (phase = 'intro')}
		onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (phase = 'intro')}
	>
		<div class="kick">Community Consensus · {narrative.group_size} pooled</div>
		<div class="h">
			<span class="gl">{shapeGlyph}</span><span class="ht">{narrative.headline}</span><span class="exp"
				>⤢</span
			>
		</div>
		<div class="stats">
			{nPoints} practices <span class="x">·</span> {nClusters} communities
			{#if narrative.effective_camps != null}<span class="x">·</span>
				{narrative.effective_camps.toFixed(1)} effective{/if}
			{#if authReadable}<span class="x">·</span> authority {diverges ? 'diverges' : 'agrees'}{/if}
		</div>
	</div>
{/if}

<style>
	/* ── scrim + Overture (intro) ─────────────────────────────────────────────────────────────── */
	.scrim {
		position: fixed;
		inset: 0;
		z-index: 14;
		background: rgba(251, 248, 242, 0.5);
		backdrop-filter: blur(5px) saturate(0.72);
		opacity: 0;
		pointer-events: none;
		transition: opacity 0.85s ease;
	}
	.scrim.on {
		opacity: 1;
		pointer-events: auto;
	}
	.overture {
		position: fixed;
		inset: 0;
		z-index: 15;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		text-align: center;
		gap: 14px;
		padding: 0 6%;
		opacity: 0;
		pointer-events: none;
		transition: opacity 0.55s ease;
	}
	.overture.on {
		opacity: 1;
		pointer-events: auto;
	}
	.kicker {
		font-family: var(--mono);
		font-size: 10.5px;
		font-weight: 600;
		letter-spacing: 0.12em;
		color: var(--muted2);
		text-transform: uppercase;
	}
	.q {
		font-family: var(--serif);
		font-style: italic;
		color: #5a616b;
		font-size: 17px;
		line-height: 1.35;
		max-width: 40ch;
	}
	.rule {
		height: 1px;
		width: 54px;
		background: var(--line);
	}
	.head {
		font-family: var(--display);
		font-weight: 600;
		color: var(--ink);
		line-height: 1.14;
		letter-spacing: -0.008em;
		font-size: 29px;
		max-width: 26ch;
	}

	/* ── the two-axis comparison ──────────────────────────────────────────────────────────────── */
	.split {
		position: relative;
		display: flex;
		gap: 64px;
		max-width: 720px;
		width: 100%;
		margin-top: 4px;
		align-items: stretch;
		justify-content: center;
	}
	.col {
		flex: 1;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 12px;
		padding: 14px 17px 15px;
		text-align: left;
		box-shadow: 0 4px 16px rgba(60, 50, 30, 0.05);
		display: flex;
		flex-direction: column;
	}
	.col .clbl {
		font-family: var(--mono);
		font-size: 9px;
		font-weight: 700;
		letter-spacing: 0.11em;
		text-transform: uppercase;
		display: flex;
		align-items: center;
		gap: 6px;
		margin-bottom: 9px;
	}
	.col.left .clbl {
		color: #2f6044;
	}
	.col.right .clbl {
		color: #3a5a7a;
	}
	.col .clbl .dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
	}
	.col.left .clbl .dot {
		background: #2f6044;
	}
	.col.right .clbl .dot {
		background: #3a5a7a;
	}
	.col .pill {
		font-family: var(--mono);
		font-size: 9px;
		font-weight: 600;
		border-radius: 999px;
		padding: 2px 7px;
		margin-left: auto;
	}
	.col.left .pill {
		color: #2f6044;
		background: #eef6f0;
		border: 1px solid #bcd9c8;
	}
	.col.left .pill.fragile {
		color: var(--muted);
		background: var(--panel2);
		border: 1px dashed var(--muted2);
	}
	.col.right .pill {
		color: #3a5a7a;
		background: #eef2f6;
		border: 1px solid #cdd9e3;
	}
	.col .lead {
		font-family: var(--display);
		font-size: 17.5px;
		font-weight: 600;
		line-height: 1.2;
		color: var(--ink);
	}
	.col .big {
		font-family: var(--mono);
		font-size: 22px;
		font-weight: 600;
		line-height: 1;
		margin: 8px 0 2px;
	}
	.col.left .big {
		color: #2f6044;
	}
	.col.right .big {
		color: #3a5a7a;
	}
	.col .big small {
		font-size: 10px;
		font-weight: 500;
		color: var(--muted);
		letter-spacing: 0.02em;
	}
	.col .sub {
		font-family: var(--serif);
		font-size: 12.5px;
		line-height: 1.45;
		color: #4a4f57;
		margin-top: 6px;
	}
	.col .sub b {
		font-weight: 600;
		color: var(--ink);
	}
	.col .foot {
		font-family: var(--mono);
		font-size: 9.5px;
		color: var(--muted);
		margin-top: auto;
		padding-top: 9px;
		letter-spacing: 0.02em;
	}
	.col .thin {
		font-family: var(--serif);
		font-size: 12.5px;
		line-height: 1.45;
		color: var(--muted);
		font-style: italic;
	}

	/* the relation glyph in the gap: '=' base (agree); '.ne' overlays a slash → '≠' (diverge).
	   The '=' is one glyph in one typeface, so agree/diverge never read as different fonts. */
	.seam {
		position: absolute;
		left: 50%;
		top: 50%;
		transform: translate(-50%, -50%);
		z-index: 4;
		width: 40px;
		height: 40px;
		color: var(--accent);
		pointer-events: none;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.seam svg {
		width: 38px;
		height: 38px;
		display: block;
	}

	.body {
		font-family: var(--serif);
		color: #3a3f47;
		line-height: 1.55;
		font-size: 14.5px;
		max-width: 60ch;
		margin-top: 2px;
	}
	.ncta {
		pointer-events: auto;
		cursor: pointer;
		margin-top: 4px;
		font-family: var(--mono);
		font-size: 11px;
		font-weight: 600;
		letter-spacing: 0.08em;
		color: var(--muted);
		background: none;
		border: none;
	}
	.ncta:hover {
		color: var(--ink);
	}

	/* ── collapsed masthead (explore), top-left ───────────────────────────────────────────────── */
	.title {
		position: fixed;
		top: 18px;
		left: 22px;
		z-index: 6;
		max-width: 380px;
		cursor: pointer;
		opacity: 0;
		transform: translateY(-6px);
		pointer-events: none;
		transition:
			opacity 0.4s,
			transform 0.4s;
	}
	.title.on {
		opacity: 1;
		transform: none;
		pointer-events: auto;
	}
	.kick {
		font-family: var(--mono);
		font-size: 10px;
		font-weight: 600;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--muted2);
	}
	.h {
		font-family: var(--display);
		font-weight: 600;
		font-size: 20px;
		line-height: 1.18;
		letter-spacing: -0.008em;
		color: var(--ink);
		margin: 3px 0 7px;
		display: flex;
		align-items: flex-start;
		gap: 8px;
	}
	.h .gl {
		font-size: 14px;
		color: var(--accent);
		margin-top: 3px;
	}
	.h .exp {
		margin-left: 2px;
		font-family: var(--mono);
		font-size: 12px;
		color: var(--muted2);
		margin-top: 4px;
	}
	.title:hover .h .exp {
		color: var(--ink);
	}
	.stats {
		margin-top: 7px;
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.03em;
		color: var(--muted);
		line-height: 1.6;
	}
	.stats .x {
		color: var(--muted2);
	}
</style>
