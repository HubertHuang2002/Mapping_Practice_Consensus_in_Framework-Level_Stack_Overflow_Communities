<!--
  CommunityCard — bottom-left detail surface for ONE selected community (cluster). Narrates THAT
  camp's own signals (signal-table v2, camp_signal — attached to each cluster shell by materialize):
  by VOTES (its share of the vote, prevalence, internal agreement) and by AUTHORITY (its share of the
  query's network centrality + its most-central voice). Decoupled from AnswerPanel — both can be open.

  Replaces the old Σauthority sum (a percentile-then-sum that collapses to prevalence — misleading;
  signal-table-v2.md §9). Long-tail / pre-aggregate shells lack camp fields → a minimal card is shown.
-->
<script lang="ts">
	let {
		viz,
		cid,
		colors
	}: {
		viz: any;
		cid: string | null;
		colors: Record<string, string>;
	} = $props();

	const cluster = $derived(cid ? viz.clusters.find((c: any) => c.id === cid) : null);
	const pct = (x: number | null | undefined) => `${Math.round(100 * (x ?? 0))}%`;
	const hasCamp = $derived(cluster != null && cluster.vote_share != null);

	// year range still comes from the points (camp_signal has no dates); cheap and local.
	const years = $derived.by(() => {
		if (!cid) return null;
		const ys = viz.points
			.filter((p: any) => p.cluster === cid)
			.map((p: any) => p.year)
			.filter((y: any) => y != null);
		return ys.length ? { min: Math.min(...ys), max: Math.max(...ys) } : null;
	});
	const authReadable = $derived(hasCamp && (cluster.authority_coverage ?? 0) >= 0.5 && cluster.top_author);
</script>

{#if cid && cluster}
	<aside class="community">
		<div class="top">
			<div class="lab">Community</div>
			<div class="chips">
				{#if cluster.is_vote_leader}<span class="chip v">● vote-leader</span>{/if}
				{#if cluster.is_authority_backed}<span class="chip a">◆ most-central voice</span>{/if}
			</div>
		</div>
		<div class="cname"><span class="sw" style:background={colors[cid]}></span>{cluster.name}</div>
		{#if cluster.exemplar}
			<div class="exemplar">“{cluster.exemplar}”</div>
		{/if}
		<div class="csub">
			<b>{cluster.n}</b> practices{#if cluster.prevalence_n != null} · from <b>{cluster.prevalence_n}</b> answers{/if}{#if years}
				· {years.min}–{years.max}{/if}
		</div>

		{#if hasCamp}
			<!-- BY VOTES -->
			<div class="axis v">
				<div class="al">By votes</div>
				<div class="arow">
					<span class="big">{pct(cluster.vote_share)}</span><span class="au">of votes</span>
					{#if cluster.prevalence_share != null}<span class="dot2">·</span>
						<span class="sec">{pct(cluster.prevalence_share)} of answers</span>{/if}
				</div>
				{#if cluster.voting_agreement != null}
					<div class="note">within-camp agreement {cluster.voting_agreement.toFixed(2)}</div>
				{/if}
			</div>

			<!-- BY AUTHORITY -->
			<div class="axis a">
				<div class="al">By authority · network centrality</div>
				{#if authReadable}
					<div class="arow">
						<span class="big">{pct(cluster.author_pr_share)}</span><span class="au">of centrality</span>
					</div>
					<div class="note">
						top voice <b>{cluster.top_author}</b>{#if cluster.top_author_pr_share != null}
							· holds {pct(cluster.top_author_pr_share)} alone{/if} · coverage {pct(cluster.authority_coverage)}
					</div>
				{:else}
					<div class="note thin">authority too thin to read for this camp (coverage {pct(cluster.authority_coverage)}).</div>
				{/if}
			</div>
		{:else}
			<div class="note thin">idiosyncratic long tail — no aggregated camp signal.</div>
		{/if}
	</aside>
{/if}

<style>
	.community {
		position: fixed;
		left: 18px;
		bottom: 16px;
		z-index: 12;
		width: 300px;
		max-height: 76vh;
		box-sizing: border-box;
		overflow-y: auto;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 11px;
		padding: 14px 15px;
		box-shadow: 0 8px 30px rgba(60, 50, 30, 0.12);
	}
	.top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}
	.lab {
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted2);
		font-weight: 600;
	}
	.chips {
		display: flex;
		gap: 5px;
	}
	.chip {
		font-family: var(--mono);
		font-size: 8.5px;
		font-weight: 700;
		letter-spacing: 0.02em;
		border-radius: 999px;
		padding: 2px 7px;
		white-space: nowrap;
	}
	.chip.v {
		color: #2f6044;
		background: #eef6f0;
		border: 1px solid #bcd9c8;
	}
	.chip.a {
		color: #3a5a7a;
		background: #eef2f6;
		border: 1px solid #cdd9e3;
	}
	.cname {
		font-family: var(--display);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.3;
		margin: 8px 0 6px;
		padding-right: 4px;
	}
	.cname .sw {
		display: inline-block;
		width: 11px;
		height: 11px;
		border-radius: 50%;
		margin-right: 7px;
	}
	.exemplar {
		font-family: var(--serif);
		font-style: italic;
		font-size: 12px;
		line-height: 1.4;
		color: #5a616b;
		margin: -2px 0 8px;
	}
	.csub {
		font-family: var(--mono);
		font-size: 10px;
		color: var(--muted);
		margin: 0 0 10px;
		line-height: 1.5;
	}
	.csub b {
		color: var(--ink);
	}

	/* two-axis, mirroring the narrative: votes (green) / authority (blue) */
	.axis {
		border-top: 1px solid var(--line);
		padding: 9px 0 2px;
	}
	.al {
		font-family: var(--mono);
		font-size: 9px;
		font-weight: 700;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		margin-bottom: 5px;
	}
	.axis.v .al {
		color: #2f6044;
	}
	.axis.a .al {
		color: #3a5a7a;
	}
	.arow {
		display: flex;
		align-items: baseline;
		gap: 5px;
	}
	.big {
		font-family: var(--mono);
		font-size: 20px;
		font-weight: 600;
		line-height: 1;
	}
	.axis.v .big {
		color: #2f6044;
	}
	.axis.a .big {
		color: #3a5a7a;
	}
	.au {
		font-family: var(--mono);
		font-size: 10px;
		color: var(--muted);
	}
	.dot2 {
		color: var(--muted2);
	}
	.sec {
		font-family: var(--mono);
		font-size: 11px;
		color: #4a4f57;
	}
	.note {
		font-family: var(--serif);
		font-size: 12px;
		line-height: 1.45;
		color: #4a4f57;
		margin-top: 5px;
	}
	.note b {
		font-weight: 600;
		color: var(--ink);
	}
	.note.thin {
		color: var(--muted);
		font-style: italic;
	}
</style>
