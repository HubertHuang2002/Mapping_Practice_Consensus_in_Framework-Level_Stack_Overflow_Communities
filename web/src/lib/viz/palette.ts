// Shared cluster palette — the single source of colour for the field AND the detail panels, so a
// blob, a community swatch, and a practice dot for the same cluster never disagree. Order-stable:
// colours are assigned by cluster order, long-tail always neutral grey.

export const COLORS = [
	'#332288', // Paul-Tol muted, CVD-safe
	'#88CCEE',
	'#44AA99',
	'#117733',
	'#DDCC77',
	'#CC6677',
	'#882255',
	'#AA4499'
];
export const TAIL = 'long-tail';
export const GREY = '#9AA0A6';

export type Cluster = { id: string; name: string; n: number };

/** cluster id → hex colour (long-tail → grey), assigned in array order. */
export function clusterColors(clusters: Cluster[]): Record<string, string> {
	const color: Record<string, string> = {};
	let ci = 0;
	for (const c of clusters) color[c.id] = c.id === TAIL ? GREY : COLORS[ci++ % COLORS.length];
	return color;
}

/** cluster id → name. */
export function clusterNames(clusters: Cluster[]): Record<string, string> {
	const name: Record<string, string> = {};
	for (const c of clusters) name[c.id] = c.name;
	return name;
}
