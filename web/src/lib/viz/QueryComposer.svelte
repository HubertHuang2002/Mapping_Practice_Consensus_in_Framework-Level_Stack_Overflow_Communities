<!--
  QueryComposer — the landing (ADR 0009 cold-path entry, front end). A ChatGPT-style centred
  composer in the Editorial·Ink language: greeting + one rounded prompt box (autogrowing textarea,
  scope chip, circular send). Focus reveals a Google-style search-history panel backed by
  localStorage (the only human-readable record of past queries — GET /queries returns group_ids only).

  Emits:  submit(text)            — a typed/new query
          pick({ q, group_id })   — a history entry click (caller can fast-path a known group)
          remove({ q, group_id }) — drop one history row
-->
<script lang="ts">
	type Entry = { q: string; group_id: string | null };
	let {
		history = [],
		onsubmit,
		onpick,
		onremove
	}: {
		history?: Entry[];
		onsubmit?: (text: string) => void;
		onpick?: (e: Entry) => void;
		onremove?: (e: Entry) => void;
	} = $props();

	let text = $state('');
	let open = $state(false);
	let ta: HTMLTextAreaElement;
	let blurTimer: ReturnType<typeof setTimeout>;

	function grow() {
		if (!ta) return;
		ta.style.height = 'auto';
		ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
	}
	function send() {
		const t = text.trim();
		if (!t) return;
		open = false;
		onsubmit?.(t);
	}
	function onKey(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}
	function focusIn() {
		clearTimeout(blurTimer);
		open = true;
	}
	function focusOut() {
		blurTimer = setTimeout(() => (open = false), 130);
	}
</script>

<div class="landing">
	<div class="greet">
		<div class="kicker">Community Consensus · React</div>
		<div class="title">What does the community agree on?</div>
		<div class="sub">
			Ask a React question — every equivalent question’s answers are pooled and mapped, showing where
			the community converges and where it splits.
		</div>
	</div>

	<div class="ask" class:open>
		<div class="composer">
			<textarea
				bind:this={ta}
				bind:value={text}
				rows="1"
				placeholder="Ask a React question…  e.g. why does useEffect run twice on mount?"
				autocomplete="off"
				oninput={grow}
				onkeydown={onKey}
				onfocus={focusIn}
				onblur={focusOut}
			></textarea>
			<div class="crow">
				<span class="scope">scope · <b>[reactjs]</b> · 221k questions</span>
				<button class="send" title="map it" onclick={send} aria-label="map it">↑</button>
			</div>
		</div>

		{#if history.length}
			<div class="history">
				<div class="hhead"><span class="t">Mapped queries · {history.length}</span></div>
				<div class="hlist">
				{#each history as e (e.group_id)}
					<div
						class="hrow"
						role="button"
						tabindex="0"
						onmousedown={(ev) => ev.preventDefault()}
						onclick={() => {
							open = false;
							onpick?.(e);
						}}
						onkeydown={(ev) => {
							if (ev.key === 'Enter' || ev.key === ' ') {
								ev.preventDefault();
								open = false;
								onpick?.(e);
							}
						}}
					>
						<span class="ic">
							<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
								<path d="M3.5 12 a8.5 8.5 0 1 0 2.6 -6.1" />
								<path d="M3.2 4.5 v3.4 h3.4" />
								<path d="M12 8 V12 l3 1.8" />
							</svg>
						</span>
						<span class="txt">{e.q}</span>
						{#if onremove}
							<span
								class="x"
								role="button"
								tabindex="0"
								aria-label="remove"
								onmousedown={(ev) => ev.preventDefault()}
								onclick={(ev) => {
									ev.stopPropagation();
									onremove?.(e);
								}}
								onkeydown={(ev) => {
									if (ev.key === 'Enter' || ev.key === ' ') {
										ev.preventDefault();
										ev.stopPropagation();
										onremove?.(e);
									}
								}}>✕</span
							>
						{/if}
					</div>
				{/each}
				</div>
			</div>
		{/if}
	</div>
</div>

<style>
	.landing {
		position: fixed;
		inset: 0;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 30px;
		padding: 6vh 6%;
	}
	.greet {
		text-align: center;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 13px;
	}
	.kicker {
		font-family: var(--mono);
		font-size: 10.5px;
		font-weight: 600;
		letter-spacing: 0.16em;
		color: var(--muted2);
		text-transform: uppercase;
	}
	.title {
		font-family: var(--display);
		font-weight: 600;
		color: var(--ink);
		line-height: 1.1;
		letter-spacing: -0.012em;
		font-size: 40px;
	}
	.sub {
		font-family: var(--serif);
		font-style: italic;
		color: #5a616b;
		font-size: 15.5px;
		max-width: 46ch;
		line-height: 1.45;
	}

	.ask {
		width: min(680px, 92vw);
		position: relative;
	}
	.composer {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 26px;
		padding: 18px 20px 14px;
		box-shadow: 0 24px 60px -40px rgba(40, 33, 20, 0.55);
		transition: border-color 0.2s, box-shadow 0.2s;
	}
	.composer:focus-within {
		border-color: var(--accepted);
		box-shadow: 0 24px 60px -36px rgba(40, 33, 20, 0.5), 0 0 0 3px rgba(200, 162, 74, 0.14);
	}
	.composer textarea {
		width: 100%;
		border: none;
		outline: none;
		background: none;
		resize: none;
		font-family: var(--serif);
		font-size: 19px;
		line-height: 1.5;
		color: var(--ink);
		max-height: 140px;
		min-height: 30px;
	}
	.composer textarea::placeholder {
		color: var(--muted2);
		font-style: italic;
	}
	.crow {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-top: 10px;
	}
	.scope {
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.05em;
		color: var(--muted2);
		border: 1px solid var(--line);
		border-radius: 999px;
		padding: 4px 11px;
		display: inline-flex;
		gap: 6px;
		align-items: center;
	}
	.scope b {
		color: var(--ink);
		font-weight: 600;
	}
	.send {
		cursor: pointer;
		background: var(--ink);
		color: var(--paper);
		border: none;
		width: 38px;
		height: 38px;
		border-radius: 50%;
		flex: none;
		font-size: 17px;
		display: flex;
		align-items: center;
		justify-content: center;
		transition: background 0.2s;
	}
	.send:hover {
		background: var(--accent);
	}

	.history {
		position: absolute;
		left: 8px;
		right: 8px;
		top: calc(100% + 10px);
		z-index: 6;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: 16px;
		padding: 9px;
		box-shadow: 0 28px 60px -34px rgba(40, 33, 20, 0.5);
		opacity: 0;
		transform: translateY(-6px);
		pointer-events: none;
		transition: opacity 0.2s, transform 0.2s;
	}
	.hlist {
		/* show ~4 rows; the rest scroll. Header (.hhead) stays fixed above this box. */
		max-height: 152px;
		overflow-y: auto;
		overscroll-behavior: contain; /* don't chain the scroll to the page behind the dropdown */
	}
	.ask.open .history {
		opacity: 1;
		transform: none;
		pointer-events: auto;
	}
	.hhead {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 4px 11px 9px;
	}
	.hhead .t {
		font-family: var(--mono);
		font-size: 9px;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--muted2);
	}
	.hrow {
		display: flex;
		align-items: center;
		gap: 12px;
		width: 100%;
		box-sizing: border-box; /* width:100% must include the padding, else the row overflows the dropdown */
		cursor: pointer;
		padding: 9px 11px;
		border-radius: 11px;
		border: none;
		background: none;
		font-family: var(--serif);
		font-size: 15px;
		color: var(--ink);
		text-align: left;
		transition: background 0.14s;
	}
	.hrow:hover {
		background: var(--panel2);
	}
	.hrow .ic {
		flex: none;
		color: var(--muted2);
		display: flex;
	}
	.hrow .txt {
		flex: 1;
		min-width: 0; /* let the flex item shrink so ellipsis applies (else long text overflows the row) */
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.hrow .x {
		flex: none;
		color: var(--muted2);
		opacity: 0;
		font-family: var(--mono);
		font-size: 13px;
		transition: opacity 0.14s;
	}
	.hrow:hover .x {
		opacity: 0.7;
	}
	.hrow .x:hover {
		color: var(--ink);
		opacity: 1;
	}
</style>
