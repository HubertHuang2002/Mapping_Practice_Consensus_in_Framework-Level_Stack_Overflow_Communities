<!--
  Waiting — the cold-path interstitial. Shows the real pipeline one frame at a time
  (Retrieve → Gate → Extract → Cluster → Narrate), then a face-ID-style completion check, then
  emits `done` so +page hands off to the dashboard. No fake spinner: the frames ARE the pipeline.

  Driven by a timer, but the SERVE moment is gated on the parent: `ready` flips true once the
  Breakdown JSON is actually in hand (warm cache hit → near-instant; cold bake → after the poll).
  `cached` only changes the closing caption (served-from-cache vs verdict-ready) — honest provenance.

  Props: query, ready, cached, ondone()
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	let {
		query = '',
		ready = false,
		cached = false,
		stage = null,
		prog = null,
		ondone
	}: {
		query?: string;
		ready?: boolean;
		cached?: boolean;
		stage?: string | null;
		prog?: { k: number; n: number } | null;
		ondone?: () => void;
	} = $props();

	const FOOT = [
		'embedding your question, scanning 221,000 questions…',
		'gating which questions ask the same thing…',
		'extracting each pooled answer’s practices…',
		'clustering where the community converges…',
		'composing the one-line verdict…'
	];
	// real bake stage → pipeline frame. Retrieve(0)+Gate(1) happen during resolve (before polling),
	// so they play on a short intro timer; the backend then drives Extract(2)→Cluster(3)→Narrate(4).
	const STAGE_IDX: Record<string, number> = { extract: 2, cluster: 3, narrate: 4, materialize: 4 };

	let idx = $state(0); // 0..4 pipeline frames, 5 = served
	let served = $state(false);
	let caption = $state(FOOT[0]);
	let timer: ReturnType<typeof setInterval>;

	onMount(() => {
		// intro only: walk Retrieve→Gate while resolve runs; once a real stage lands it owns idx.
		timer = setInterval(() => {
			if (served || stage) return;
			if (idx < 1) {
				idx += 1;
				caption = FOOT[idx];
			} // else hold at Gate until the first baking stage arrives
		}, 1500);
	});
	onDestroy(() => clearInterval(timer));

	// the real pipeline stage drives the frame once baking starts (the intro timer steps aside)
	$effect(() => {
		if (served || !stage) return;
		const si = STAGE_IDX[stage];
		if (si != null && si !== idx) {
			idx = si;
			caption = FOOT[si];
		}
	});

	// the serve moment is owned by the parent (the Breakdown is actually ready)
	$effect(() => {
		if (ready && !served) serve();
	});

	function serve() {
		served = true;
		clearInterval(timer);
		idx = 5;
		caption = cached ? '● served from cache' : 'verdict ready';
		setTimeout(() => ondone?.(), 1250); // let the check draw, then hand off
	}

	// Extract is per-answer — surface the live count when the backend reports it.
	const extractCount = $derived(
		stage === 'extract' && prog
			? `gated ${prog.k}/${prog.n} answers`
			: 'pulling each answer’s practices'
	);
</script>

<div class="wait">
	<div class="kicker2">Mapping the consensus</div>
	<div class="askedq">“{query}”</div>
	<div class="rule"></div>

	<div class="focal">
		<div class="frame" class:on={idx === 0}>
			<div class="bigtile">
				<svg class="bico" viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round">
					<circle class="pt" cx="6" cy="7" r="1" fill="currentColor" stroke="none" /><circle class="pt2" cx="18" cy="8" r="1" fill="currentColor" stroke="none" />
					<circle class="pt" cx="8" cy="17" r="1" fill="currentColor" stroke="none" /><circle class="pt2" cx="17" cy="16" r="1" fill="currentColor" stroke="none" />
					<g class="scan"><circle cx="12" cy="12" r="4" stroke="currentColor" /><path d="M15 15 l3 3" stroke="currentColor" /></g>
				</svg>
			</div>
			<div class="fname">Retrieve</div>
			<div class="fcount">221k questions → candidates</div>
		</div>

		<div class="frame" class:on={idx === 1}>
			<div class="bigtile">
				<svg class="bico" viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round">
					<circle class="fdrop" cx="9" cy="4" r=".9" fill="currentColor" stroke="none" /><circle class="fdrop d2" cx="12" cy="4" r=".9" fill="currentColor" stroke="none" /><circle class="fdrop d3" cx="15" cy="4" r=".9" fill="currentColor" stroke="none" />
					<path d="M5 8 h14 l-5 6 v5 l-4 1 v-6 z" stroke="currentColor" stroke-linejoin="round" />
				</svg>
			</div>
			<div class="fname">Gate</div>
			<div class="fcount">keeping the same-problem questions</div>
		</div>

		<div class="frame" class:on={idx === 2}>
			<div class="bigtile">
				<svg class="bico" viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round">
					<path d="M6 4 h8 l4 4 v12 h-12 z" stroke="currentColor" stroke-linejoin="round" /><path d="M14 4 v4 h4" stroke="currentColor" />
					<circle class="lift" cx="9" cy="12" r=".9" fill="currentColor" stroke="none" /><circle class="lift l2" cx="9" cy="15" r=".9" fill="currentColor" stroke="none" /><circle class="lift l3" cx="9" cy="18" r=".9" fill="currentColor" stroke="none" />
				</svg>
			</div>
			<div class="fname">Extract</div>
			<div class="fcount">{extractCount}</div>
		</div>

		<div class="frame" class:on={idx === 3}>
			<div class="bigtile">
				<svg class="bico" viewBox="0 0 24 24" fill="none" stroke="none">
					<circle class="mig" style="--mx:-2px;--my:-1px" cx="7" cy="8" r="1.7" fill="#332288" /><circle class="mig" style="--mx:-1px;--my:2px" cx="6" cy="13" r="1.7" fill="#332288" />
					<circle class="mig" style="--mx:2px;--my:-2px" cx="17" cy="9" r="1.7" fill="#44AA99" /><circle class="mig" style="--mx:1px;--my:1px" cx="18" cy="14" r="1.7" fill="#44AA99" />
					<circle class="mig" style="--mx:0;--my:-2px" cx="12" cy="18" r="1.7" fill="#CC6677" />
				</svg>
			</div>
			<div class="fname">Cluster</div>
			<div class="fcount">grouping where it converges</div>
		</div>

		<div class="frame" class:on={idx === 4}>
			<div class="bigtile">
				<svg class="bico" viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round">
					<path d="M8 7 q-2 0 -2 2 q0 2 2 2 q0 -2 -1.4 -2" stroke="currentColor" /><path d="M14 7 q-2 0 -2 2 q0 2 2 2 q0 -2 -1.4 -2" stroke="currentColor" />
					<path class="pen" d="M6 16 h12" stroke="currentColor" stroke-width="1.6" />
				</svg>
			</div>
			<div class="fname">Narrate</div>
			<div class="fcount">composing the verdict</div>
		</div>

		<div class="frame served" class:on={served}>
			<svg class="checkmark" class:play={served} viewBox="0 0 52 52" fill="none">
				<circle class="ck-ring" cx="26" cy="26" r="24" stroke="#c8a24a" stroke-width="2.4" />
				<path class="ck-tick" d="M15 26.5 l7.5 7.5 l14.5 -15.5" stroke="#c8a24a" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
			<div class="fname">Mapped</div>
			<div class="fcount">opening the field…</div>
		</div>
	</div>

	<div class="dots" class:hidden={served}>
		{#each [0, 1, 2, 3, 4] as k}
			<i class:done={k < idx} class:cur={k === idx}></i>
		{/each}
	</div>
	<div class="caption" class:ok={served}>{caption}</div>
</div>

<style>
	.wait {
		position: fixed;
		inset: 0;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 6px;
		padding: 0 6%;
	}
	.kicker2 {
		font-family: var(--mono);
		font-size: 10.5px;
		font-weight: 600;
		letter-spacing: 0.18em;
		color: var(--muted2);
		text-transform: uppercase;
	}
	.askedq {
		font-family: var(--serif);
		font-style: italic;
		color: #5a616b;
		font-size: 18px;
		max-width: 42ch;
		text-align: center;
		margin-top: 5px;
	}
	.rule {
		height: 1px;
		width: 48px;
		background: var(--line);
		margin: 9px 0 16px;
	}

	.focal {
		position: relative;
		width: min(420px, 86vw);
		height: 214px;
	}
	.frame {
		position: absolute;
		inset: 0;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 17px;
		opacity: 0;
		transform: translateY(9px);
		pointer-events: none;
		transition: opacity 0.42s ease, transform 0.42s ease;
	}
	.frame.on {
		opacity: 1;
		transform: none;
	}
	.bigtile {
		width: 90px;
		height: 90px;
		border-radius: 24px;
		background: var(--panel);
		border: 1px solid var(--ink);
		color: var(--ink);
		display: flex;
		align-items: center;
		justify-content: center;
		box-shadow: 0 18px 40px -22px rgba(40, 33, 20, 0.6);
	}
	.fname {
		font-family: var(--display);
		font-weight: 600;
		font-size: 25px;
		color: var(--ink);
		line-height: 1;
	}
	.fcount {
		font-family: var(--mono);
		font-size: 12px;
		letter-spacing: 0.02em;
		color: var(--muted);
		min-height: 1.2em;
	}

	.dots {
		display: flex;
		gap: 9px;
		margin-top: 6px;
		height: 8px;
		transition: opacity 0.3s;
	}
	.dots.hidden {
		opacity: 0;
	}
	.dots i {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: none;
		border: 1.4px solid var(--line);
		transition: all 0.3s;
	}
	.dots i.done {
		background: var(--accepted);
		border-color: var(--accepted);
		transform: scale(0.78);
	}
	.dots i.cur {
		background: var(--ink);
		border-color: var(--ink);
	}

	.caption {
		margin-top: 16px;
		font-family: var(--mono);
		font-size: 11px;
		letter-spacing: 0.04em;
		color: var(--muted);
		min-height: 1.3em;
		text-align: center;
	}
	.caption.ok {
		color: var(--accent);
	}

	.bico {
		width: 46px;
		height: 46px;
		display: block;
		overflow: visible;
		color: var(--ink);
	}

	.frame.on .scan {
		animation: scan 1.7s ease-in-out infinite;
	}
	@keyframes scan {
		0%, 100% { transform: translateX(-6px); }
		50% { transform: translateX(7px); }
	}
	.frame.on .pt { animation: tw 1.4s ease-in-out infinite; }
	.frame.on .pt2 { animation: tw 1.4s ease-in-out 0.5s infinite; }
	@keyframes tw {
		0%, 100% { opacity: 0.28; }
		50% { opacity: 1; }
	}
	.frame.on .fdrop { animation: fdrop 1.2s ease-in infinite; }
	.frame.on .fdrop.d2 { animation-delay: 0.4s; }
	.frame.on .fdrop.d3 { animation-delay: 0.8s; }
	@keyframes fdrop {
		0% { transform: translateY(-7px); opacity: 0; }
		40% { opacity: 1; }
		100% { transform: translateY(13px); opacity: 0; }
	}
	.frame.on .lift { animation: lift 1.5s ease-in-out infinite; }
	.frame.on .lift.l2 { animation-delay: 0.3s; }
	.frame.on .lift.l3 { animation-delay: 0.6s; }
	@keyframes lift {
		0% { transform: translate(0, 0); opacity: 0.2; }
		50% { opacity: 1; }
		100% { transform: translate(7px, -8px); opacity: 0; }
	}
	.frame.on .mig { animation: mig 1.9s ease-in-out infinite; }
	@keyframes mig {
		0% { transform: translate(0, 0); }
		50% { transform: translate(var(--mx), var(--my)); }
		100% { transform: translate(0, 0); }
	}
	.frame.on .pen {
		stroke-dasharray: 16;
		stroke-dashoffset: 16;
		animation: draw 1.6s ease-in-out infinite;
	}
	@keyframes draw {
		0% { stroke-dashoffset: 16; }
		55% { stroke-dashoffset: 0; }
		100% { stroke-dashoffset: 0; opacity: 0.4; }
	}

	.checkmark {
		width: 94px;
		height: 94px;
	}
	.checkmark .ck-ring {
		stroke-dasharray: 151;
		stroke-dashoffset: 151;
	}
	.checkmark .ck-tick {
		stroke-dasharray: 40;
		stroke-dashoffset: 40;
	}
	.checkmark.play .ck-ring {
		animation: ckring 0.62s cubic-bezier(0.5, 0, 0.2, 1) forwards;
	}
	.checkmark.play .ck-tick {
		animation: cktick 0.34s ease 0.56s forwards;
	}
	.checkmark.play {
		animation: ckpop 0.42s ease 0.86s;
	}
	@keyframes ckring {
		to { stroke-dashoffset: 0; }
	}
	@keyframes cktick {
		to { stroke-dashoffset: 0; }
	}
	@keyframes ckpop {
		0% { transform: scale(0.92); }
		55% { transform: scale(1.07); }
		100% { transform: scale(1); }
	}
</style>
