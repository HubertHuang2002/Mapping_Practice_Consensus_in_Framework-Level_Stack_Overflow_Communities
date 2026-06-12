<!--
  ForceField — the organic floating-bubble field (ported from the design reference).

  Owns the SVG + d3 force simulation: per-cluster gooey blobs (gaussian-blur + alpha-threshold
  union), authority-sized nodes, drifting cluster cohesion, whole-blob drag, node drag, hover
  tooltip. Clicks bubble out via callbacks: a blob → onCommunity(cluster); a node → onAnswer(...).

  Node size reads `weight` = √(Q·A): fused answer-quality (within-group vote percentile) × author
  network authority. weight is ALWAYS present (A-absent points degrade to W = Q), so size is never a
  special case. `authority_status` only drives the node STYLE: scored = filled; non_interactive
  (author outside the answerer network, e.g. self-answer) = solid hollow cluster-coloured ring;
  anonymous/deleted = small dashed grey "ghost". Cluster colours come from the shared palette (prop)
  so blobs, community swatches, and practice dots never disagree.

  Pan/zoom (NOT in render.py, but kept from the skeleton): the whole field lives in a #gZoom group
  driven by d3-zoom on the svg. Node/blob drag still works under zoom because d3-drag reports
  container-space coordinates and stops propagation so a node drag never doubles as a pan.

  Timeline mode (swimlanes on a date axis) is intentionally NOT here yet — the gAxis/gLane groups
  and per-node rLane are pre-computed so it drops in later without reshaping this file.
-->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import {
		forceSimulation,
		forceManyBody,
		forceCollide,
		forceRadial,
		type Simulation
	} from 'd3-force';
	import { select } from 'd3-selection';
	import { drag as d3drag } from 'd3-drag';
	import { zoom as d3zoom, zoomIdentity, type ZoomBehavior } from 'd3-zoom';
	import { clusterColors, clusterNames } from './palette';

	let {
		viz,
		colors,
		onCommunity = (_cid: string) => {},
		onAnswer = (_aid: number, _cid: string, _pidx: number) => {},
		onBackground = () => {},
		highlightAnswer = null
	}: {
		viz: any;
		colors: Record<string, string>;
		onCommunity?: (cid: string) => void;
		onAnswer?: (aid: number, cid: string, pidx: number) => void;
		onBackground?: () => void;
		highlightAnswer?: number | null;
	} = $props();

	let svgEl: SVGSVGElement;
	let gZoom: SVGGElement;
	let gBlob: SVGGElement;
	let gLabel: SVGGElement;
	let gNode: SVGGElement;
	let sim: Simulation<any, undefined> | null = null;
	let zoomBehavior: ZoomBehavior<SVGSVGElement, unknown> | null = null;
	let autoFit = true; // keep the whole field framed during the initial settle, until the user steers
	let sizeMix = $state(0.5); // node size: 0 = VOTES, 1 = AUTHORITY (raw PageRank); default = even blend
	let applySizing: () => void = () => {}; // re-sizes nodes when the slider moves (assigned in buildField)

	// the zoom transform that fits every node (bbox + margin) into the viewport, or null if not ready
	function fitTransform() {
		if (!simNodes.length) return null;
		const W = window.innerWidth;
		const H = window.innerHeight;
		let x0 = Infinity,
			y0 = Infinity,
			x1 = -Infinity,
			y1 = -Infinity;
		for (const n of simNodes) {
			x0 = Math.min(x0, n.x - n.r);
			y0 = Math.min(y0, n.y - n.r);
			x1 = Math.max(x1, n.x + n.r);
			y1 = Math.max(y1, n.y + n.r);
		}
		const bw = x1 - x0 || 1;
		const bh = y1 - y0 || 1;
		const cx = (x0 + x1) / 2;
		const cy = (y0 + y1) / 2;
		// 0.9 leaves a margin so blobs don't kiss the edges; clamp to the zoom extent
		const k = Math.max(0.25, Math.min(6, 0.9 * Math.min(W / bw, H / bh)));
		return zoomIdentity.scale(k).translate(-cx, -cy); // map bbox centre → viewport centre (0,0)
	}

	// d3 selections kept at component scope so the emphasis $effect can re-style after mount
	let nodeSel: any = null;
	let blobSel: any = null;
	let simNodes: any[] = []; // node data, for reset's fit-to-bounds extent

	// hover tooltip (declarative; render.py drove a raw innerHTML div)
	let tip = $state<{ show: boolean; x: number; y: number; p: any; cname: string } | null>(null);

	const evChips = (ev: string): { code: boolean; prose: boolean } => ({
		code: ev === 'code' || ev === 'both',
		prose: ev === 'prose' || ev === 'both'
	});

	let cname: Record<string, string> = {};

	function buildField() {
		const W = window.innerWidth;
		const H = window.innerHeight;
		svgEl.setAttribute('viewBox', `${-W / 2} ${-H / 2} ${W} ${H}`);

		cname = clusterNames(viz.clusters);
		// the shared palette is the source of truth; recompute as a fallback if the prop hasn't
		// propagated by mount (guards against undefined-map throws during HMR / first paint)
		const cmap = colors && Object.keys(colors).length ? colors : clusterColors(viz.clusters);

		// node size = a linear blend of two normalized axes, driven by the slider (sizeMix): 0 = VOTES
		// (within-group), 1 = AUTHORITY (raw PageRank — the global percentile is saturated, so RAW is the
		// only representation that varies). The in-between is the viewer's chosen crowd-vs-authority
		// weighting, NOT a fused "truth". Each axis is normalized to its own group max; status only STYLE.
		const vOf = (p: any) => Math.max(p.vote ?? 0, 0);
		const prOf = (p: any) => p.pagerank ?? 0;
		const maxV = Math.max(1e-6, ...viz.points.map(vOf));
		const maxP = Math.max(1e-6, ...viz.points.map(prOf));
		const radiusOf = (p: any) =>
			8 + 30 * ((1 - sizeMix) * (vOf(p) / maxV) + sizeMix * (prOf(p) / maxP));

		const nodes = viz.points.map((p: any, i: number) => ({
			id: i,
			cluster: p.cluster,
			status: p.authority_status ?? (p.authority == null ? 'anonymous' : 'scored'),
			date: p.date,
			r: radiusOf(p),
			x: (Math.random() - 0.5) * W * 0.5,
			y: (Math.random() - 0.5) * H * 0.5
		}));
		// compressed radius for the (future) timeline lanes — computed now so Phase 4 is a drop-in
		const maxRbase = Math.max(...nodes.map((n: any) => n.r));
		nodes.forEach((n: any) => (n.rLane = 5 + ((n.r - 8) / (maxRbase - 8 || 1)) * 9));
		simNodes = nodes;

		// group members by cluster
		const byC = new Map<string, any[]>();
		for (const n of nodes) {
			if (!byC.has(n.cluster)) byC.set(n.cluster, []);
			byC.get(n.cluster)!.push(n);
		}

		// dynamic-centroid cohesion + centroid separation (floats; bubbles push apart)
		function forceCluster(cohesion: number) {
			let ns: any[];
			function f(alpha: number) {
				const cen: Record<string, any> = {};
				ns.forEach((d) => {
					const k = d.r * d.r;
					const c = cen[d.cluster] || (cen[d.cluster] = { x: 0, y: 0, w: 0, a: 0 });
					c.x += d.x * k;
					c.y += d.y * k;
					c.w += k;
					c.a += d.r * d.r;
				});
				for (const id in cen) {
					cen[id].x /= cen[id].w;
					cen[id].y /= cen[id].w;
					cen[id].R = Math.sqrt(cen[id].a) * 1.8 + 12;
				}
				ns.forEach((d) => {
					const c = cen[d.cluster];
					d.vx -= (d.x - c.x) * cohesion * alpha;
					d.vy -= (d.y - c.y) * cohesion * alpha;
				});
				const ids = Object.keys(cen);
				for (let i = 0; i < ids.length; i++)
					for (let j = i + 1; j < ids.length; j++) {
						const a = cen[ids[i]],
							b = cen[ids[j]];
						let dx = b.x - a.x,
							dy = b.y - a.y,
							dist = Math.hypot(dx, dy) || 1,
							min = a.R + b.R;
						if (dist < min) {
							const p = ((min - dist) / dist) * 0.5 * alpha,
								ox = dx * p,
								oy = dy * p;
							ns.forEach((d) => {
								if (d.cluster === ids[i]) {
									d.vx -= ox;
									d.vy -= oy;
								} else if (d.cluster === ids[j]) {
									d.vx += ox;
									d.vy += oy;
								}
							});
						}
					}
			}
			(f as any).initialize = (n: any[]) => (ns = n);
			return f;
		}

		// collide is the hard constraint (strength 1, many iterations) so practice circles never
		// overlap; cohesion is eased so it can't out-push collision. The blob padding (r+PAD) still
		// overlaps at the collide gap, so the gooey union holds.
		// Place communities by size: each cluster targets a radius from centre inversely proportional
		// to its member count — the biggest aims for r≈0 (centre), the smallest for the outer RING.
		// forceRadial fixes only the radius; the angular spread stays emergent (forceCluster separation
		// + collide ring the smaller clusters around the big central mass).
		const maxN = Math.max(...[...byC.values()].map((a) => a.length));
		const RING = 340; // periphery radius (px, viewBox units) for the smallest clusters
		const sizeFrac = (d: any) => (byC.get(d.cluster)?.length ?? 1) / maxN;
		const targetR = (d: any) => RING * (1 - sizeFrac(d));
		// radius strength also scales with size: the biggest cluster is anchored hard at the centre
		// (so the lopsided ring can't shove it off), the smallest only gently held out on the ring.
		const radialStrength = (d: any) => 0.035 + 0.09 * sizeFrac(d);

		// Overall a gentle field: the attractive/positioning forces (cohesion, charge, radial) are
		// kept light so the layout breathes; collide stays firm — it's the non-overlap constraint.
		sim = forceSimulation(nodes)
			.velocityDecay(0.5) // extra damping so the field eases into place rather than snapping
			.force('cluster', forceCluster(0.3) as any)
			.force('charge', forceManyBody().strength(-10))
			.force('collide', forceCollide((d: any) => d.r + 2.5).strength(1).iterations(4))
			.force('radial', forceRadial(targetR, 0, 0).strength(radialStrength))
			.on('tick', draw);

		// once it settles, snap to a clean final frame and release the camera (so later drags/re-heats
		// don't keep re-framing the view)
		sim.on('end', () => {
			if (!autoFit) return;
			const t = fitTransform();
			if (t && zoomBehavior) zoomBehavior.transform(select(svgEl) as any, t);
			autoFit = false;
		});

		// ── whole-bubble drag (single click → community) ──
		const sdrag = d3drag<any, any>()
			.on('start', (e, d) => {
				autoFit = false;
				d._x0 = e.x;
				d._y0 = e.y;
				if (!e.active) sim!.alphaTarget(0.3).restart();
				d._mem = byC.get(d.id);
				d._mem.forEach((n: any) => {
					n.fx = n.x;
					n.fy = n.y;
				});
			})
			.on('drag', (e, d) => d._mem.forEach((n: any) => {
				n.fx += e.dx;
				n.fy += e.dy;
			}))
			.on('end', (e, d) => {
				if (!e.active) sim!.alphaTarget(0);
				if (Math.hypot(e.x - d._x0, e.y - d._y0) < 4) onCommunity(d.id);
				else d._mem.forEach((n: any) => {
					n.fx = null;
					n.fy = null;
				});
			});

		// per-cluster gooey blob (member circles unioned by the #goo filter)
		const PAD = 6;
		const blobG = select(gBlob)
			.selectAll('g')
			.data(viz.clusters.filter((c: any) => byC.has(c.id)), (c: any) => c.id)
			.join('g')
			.attr('class', 'blob')
			.attr('filter', 'url(#goo)')
			.attr('opacity', 0.34) // set once (not per-tick) so emphasis can dim it
			.call(sdrag as any);
		blobG.each(function (c: any) {
			select(this)
				.selectAll('circle')
				.data(byC.get(c.id)!)
				.join('circle')
				.attr('r', (d: any) => d.r + PAD)
				.attr('fill', cmap[c.id]);
		});
		blobSel = blobG;

		// ── nodes on top ──
		const nodeG = select(gNode).selectAll('g').data(nodes).join('g');
		nodeSel = nodeG;
		nodeG
			.append('circle')
			.attr('class', 'node')
			.attr('r', (d: any) => d.r)
			// scored = filled cluster colour; non_interactive (self-answer, outside the network) = solid
			// hollow cluster-coloured ring (alive, just not in the graph); anonymous/deleted = dashed grey
			// dead ghost. Two distinct null styles so "no author" never reads the same as "self-answer".
			.attr('fill', (d: any) => (d.status === 'scored' ? cmap[d.cluster] : 'transparent'))
			.attr('stroke', (d: any) =>
				d.status === 'scored' ? '#FBF8F2' : d.status === 'non_interactive' ? cmap[d.cluster] : '#9AA0A6'
			)
			.attr('stroke-width', (d: any) =>
				d.status === 'scored' ? 2.4 : d.status === 'non_interactive' ? 2 : 1
			)
			.attr('stroke-dasharray', (d: any) => (d.status === 'anonymous' ? '2,2' : null));

		// hover tooltip
		nodeG
			.on('mouseenter', (e: any, d: any) =>
				(tip = { show: true, x: e.clientX, y: e.clientY, p: viz.points[d.id], cname: cname[viz.points[d.id].cluster] })
			)
			.on('mousemove', (e: any) => tip && (tip = { ...tip, x: e.clientX, y: e.clientY }))
			.on('mouseleave', () => (tip = null));

		// node drag (single click → answer detail)
		const ndrag = d3drag<any, any>()
			.on('start', (e, d) => {
				autoFit = false;
				d._x0 = e.x;
				d._y0 = e.y;
				if (!e.active) sim!.alphaTarget(0.3).restart();
				d.fx = d.x;
				d.fy = d.y;
			})
			.on('drag', (e, d) => {
				d.fx = e.x;
				d.fy = e.y;
			})
			.on('end', (e, d) => {
				if (!e.active) sim!.alphaTarget(0);
				if (Math.hypot(e.x - d._x0, e.y - d._y0) < 4) {
					const p = viz.points[d.id];
					onAnswer(p.answer_id, p.cluster, p.practice_index);
				} else {
					d.fx = null;
					d.fy = null;
				}
			});
		nodeG.call(ndrag as any);

		// ── pan/zoom on the whole field (kept from the skeleton; render.py had none) ──
		zoomBehavior = d3zoom<SVGSVGElement, unknown>()
			.scaleExtent([0.25, 6])
			.on('zoom', (ev) => select(gZoom).attr('transform', ev.transform.toString()));
		// disable dblclick-to-zoom — reserved for cluster trajectory later; reset lives on the button.
		// the moment the user grabs the camera (wheel / pan), stop auto-framing and hand it over.
		select(svgEl).call(zoomBehavior as any).on('dblclick.zoom', null);
		zoomBehavior.on('start', (ev: any) => {
			if (ev.sourceEvent) autoFit = false;
		});

		// click empty background → dismiss the detail surfaces (replaces the panels' ✕ buttons).
		// Only the bare svg is the background; a click on a node/blob targets a <circle>, and a real
		// pan is a drag (no click), so neither closes the panels.
		svgEl.addEventListener('click', (e) => {
			if (e.target === svgEl) onBackground();
		});

		// the slider re-sizes nodes in place: recompute radii, update the DOM + collide radius, re-heat.
		applySizing = () => {
			if (!sim) return;
			for (const n of simNodes) n.r = radiusOf(viz.points[n.id]);
			const maxRb = Math.max(...simNodes.map((n: any) => n.r));
			simNodes.forEach((n: any) => (n.rLane = 5 + ((n.r - 8) / (maxRb - 8 || 1)) * 9));
			nodeSel.select('circle.node').attr('r', (d: any) => d.r);
			blobSel.selectAll('circle').attr('r', (d: any) => d.r + PAD);
			sim.force('collide', forceCollide((d: any) => d.r + 2.5).strength(1).iterations(4));
			sim.alpha(0.5).restart();
		};

		applyEmphasis(highlightAnswer); // honour any pre-existing selection (e.g. after HMR)

		function draw() {
			blobG
				.selectAll('circle')
				.attr('cx', (d: any) => d.x)
				.attr('cy', (d: any) => d.y);

			// floating cluster labels — mean-x, above the topmost member
			const lab = viz.clusters
				.filter((c: any) => byC.has(c.id))
				.map((c: any) => {
					const m = byC.get(c.id)!;
					return {
						id: c.id,
						x: m.reduce((s, n) => s + n.x, 0) / m.length,
						y: Math.min(...m.map((n: any) => n.y - n.r))
					};
				});
			select(gLabel)
				.selectAll('text')
				.data(lab, (d: any) => d.id)
				.join('text')
				.attr('class', 'clab')
				.attr('text-anchor', 'middle')
				.attr('pointer-events', 'none')
				.attr('x', (d: any) => d.x)
				.attr('y', (d: any) => d.y - 8)
				.text((d: any) => (cname[d.id].length > 30 ? cname[d.id].slice(0, 28) + '…' : cname[d.id]));

			select(gNode)
				.selectAll<SVGGElement, any>('g')
				.attr('transform', (d: any) => `translate(${d.x},${d.y})`);

			// keep the whole field framed while it blooms on first load (until the user steers / settles)
			if (autoFit && zoomBehavior) {
				const t = fitTransform();
				if (t) zoomBehavior.transform(select(svgEl) as any, t);
			}
		}
	}

	// Clicking a practice highlights every practice from the SAME answer and recedes the rest, so the
	// answer's footprint across clusters reads at a glance (and mirrors the open AnswerPanel's cards).
	function applyEmphasis(aid: number | null) {
		if (!nodeSel || !blobSel) return;
		const active = aid != null;
		// emphasis is purely opacity — the resting white (paper) edge is kept; dimming the rest is
		// what makes the same-answer practices stand out, without any heavy ring.
		nodeSel
			.select('circle.node')
			.attr('opacity', (d: any) => (!active || viz.points[d.id].answer_id === aid ? 1 : 0.14));
		blobSel.attr('opacity', active ? 0.12 : 0.34);
		if (gLabel) select(gLabel).attr('opacity', active ? 0.25 : 1);
	}
	// re-apply whenever the selected answer changes (null clears it back to the resting field)
	$effect(() => applyEmphasis(highlightAnswer));

	function onResize() {
		if (!svgEl) return;
		const W = window.innerWidth;
		const H = window.innerHeight;
		svgEl.setAttribute('viewBox', `${-W / 2} ${-H / 2} ${W} ${H}`);
		sim?.alpha(0.3).restart();
	}

	onMount(() => {
		buildField();
		window.addEventListener('resize', onResize);
	});
	onDestroy(() => {
		sim?.stop();
		if (typeof window !== 'undefined') window.removeEventListener('resize', onResize);
	});
</script>

<svg bind:this={svgEl} role="img" aria-label="practice breakdown force field">
	<defs>
		<!-- gooey union: blur member circles, then alpha-threshold so a cluster reads as one organic blob -->
		<filter id="goo" x="-60%" y="-60%" width="220%" height="220%">
			<feGaussianBlur in="SourceGraphic" stdDeviation="16" result="b" />
			<feColorMatrix in="b" mode="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 26 -11" />
		</filter>
	</defs>
	<g bind:this={gZoom}>
		<g bind:this={gBlob}></g>
		<g bind:this={gLabel} class="labels"></g>
		<g bind:this={gNode}></g>
	</g>
</svg>

<!-- node-size slider: drag from sizing by VOTES (left) to by raw-PageRank AUTHORITY (right). The middle
     is the viewer's own crowd-vs-authority weighting, not a fused metric. -->
<div class="sizer" title="node size: votes ↔ authority (raw PageRank)">
	<span class="sl" class:on={sizeMix < 0.5}>size · votes</span>
	<input
		class="rng"
		type="range"
		min="0"
		max="1"
		step="0.02"
		value={sizeMix}
		oninput={(e) => {
			sizeMix = +(e.currentTarget as HTMLInputElement).value;
			applySizing();
		}}
		aria-label="node size from votes to authority"
	/>
	<span class="sl" class:on={sizeMix >= 0.5}>authority</span>
</div>

{#if tip?.show}
	{@const p = tip.p}
	{@const ev = evChips(p.evidence_type)}
	<div
		class="tip"
		style:left="{Math.min(tip.x + 14, window.innerWidth - 270)}px"
		style:top="{Math.min(tip.y + 14, window.innerHeight - 90)}px"
	>
		<b>{tip.cname}</b>{#if ev.code}<span class="chip">⟨⟩ code</span>{/if}{#if ev.prose}<span
				class="chip">¶ prose</span
			>{/if}<br />
		{p.text}<br />
		<span class="m"
			>{p.vote}▲ · {p.year ?? '—'}
			{#if p.is_accepted} · <span class="star">★</span> accepted{/if}</span
		>
	</div>
{/if}

<style>
	svg {
		position: fixed;
		inset: 0;
		width: 100vw;
		height: 100vh;
		display: block;
		z-index: 0;
		cursor: grab;
		/* gentle entry: the whole field eases in rather than appearing all at once */
		animation: fieldIn 1.2s cubic-bezier(0.22, 0.61, 0.36, 1) both;
	}
	@keyframes fieldIn {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}
	svg:active {
		cursor: grabbing;
	}

	/* d3 creates these SVG elements without Svelte's scope hash → style them globally.
	   opacity/stroke transitions animate the click-to-emphasize dim/highlight. */
	:global(circle.node) {
		cursor: pointer;
		transition: opacity 0.22s ease;
	}
	:global(.blob) {
		cursor: move;
		transition: opacity 0.25s ease;
	}
	.labels {
		transition: opacity 0.22s ease;
	}
	:global(.clab) {
		fill: var(--ink);
		font-family: var(--mono);
		font-size: 10.5px;
		font-weight: 600;
		paint-order: stroke;
		stroke: var(--paper);
		stroke-width: 3.5px;
	}

	/* node-size slider — bottom-center pill */
	.sizer {
		position: fixed;
		left: 50%;
		bottom: 16px;
		transform: translateX(-50%);
		z-index: 11;
		display: flex;
		align-items: center;
		gap: 10px;
		background: rgba(255, 255, 255, 0.78);
		border: 1px solid var(--line);
		border-radius: 999px;
		padding: 5px 14px;
		backdrop-filter: blur(4px);
	}
	.sizer .sl {
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.04em;
		color: var(--muted2);
		white-space: nowrap;
		transition: color 0.2s;
	}
	.sizer .sl.on {
		color: var(--ink);
		font-weight: 600;
	}
	.rng {
		-webkit-appearance: none;
		appearance: none;
		width: 150px;
		height: 3px;
		border-radius: 2px;
		background: var(--line);
		outline: none;
		cursor: pointer;
	}
	.rng::-webkit-slider-thumb {
		-webkit-appearance: none;
		appearance: none;
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: var(--accent);
		border: 2px solid var(--paper);
		box-shadow: 0 1px 4px rgba(60, 50, 30, 0.3);
		cursor: pointer;
	}
	.rng::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: var(--accent);
		border: 2px solid var(--paper);
		cursor: pointer;
	}

	/* hover tooltip — own Svelte-rendered DOM, so plain scoped styles */
	.tip {
		position: fixed;
		z-index: 30;
		pointer-events: none;
		max-width: 260px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 7px 9px;
		font-family: var(--serif);
		font-size: 11.5px;
		line-height: 1.4;
		color: var(--ink);
		box-shadow: 0 6px 22px rgba(60, 50, 30, 0.18);
	}
	.tip .m {
		color: var(--muted);
		font-family: var(--mono);
		font-size: 10px;
	}
	.tip .star {
		color: var(--accepted);
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
</style>
