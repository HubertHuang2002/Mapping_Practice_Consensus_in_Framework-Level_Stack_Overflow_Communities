// Render a Stack Overflow answer body (markdown subset) → HTML, ported verbatim from
// the design reference's mdToHtml. Escape-FIRST so any raw HTML in the body is shown
// literally, never executed — the output is safe to drop into {@html}.
//
// Supported: fenced code blocks, inline code, **bold**, [text](http-link), -/* and 1. lists,
// > blockquotes, paragraphs. Everything else degrades to plain escaped text.

export function mdToHtml(src: string | null | undefined): string {
	if (!src || !src.trim()) return '<p class="empty">(no content captured)</p>';
	const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

	// pull fenced blocks out before inline rules touch them
	const code: string[] = [];
	src = src.replace(/```[^\n]*\n?([\s\S]*?)```/g, (_m, c) => {
		code.push(esc(c.replace(/\s+$/, '')));
		return '@@CB:' + (code.length - 1) + '@@';
	});

	const inl = (t: string) =>
		esc(t)
			.replace(/`([^`]+)`/g, '<code>$1</code>')
			.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
			.replace(
				/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
				'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
			);

	let html = '';
	let para: string[] = [];
	let list: string | null = null;
	let listTag: 'ul' | 'ol' = 'ul';
	const flushP = () => {
		if (para.length) {
			html += '<p>' + inl(para.join(' ')) + '</p>';
			para = [];
		}
	};
	const flushL = () => {
		if (list) {
			html += '<' + listTag + '>' + list + '</' + listTag + '>';
			list = null;
		}
	};

	for (const raw of src.split('\n')) {
		const line = raw.replace(/\s+$/, '');
		let m: RegExpMatchArray | null;
		if (/^\s*@@CB:(\d+)@@\s*$/.test(line)) {
			flushP();
			flushL();
			html += '<pre class="code"><code>' + code[+line.match(/@@CB:(\d+)@@/)![1]] + '</code></pre>';
		} else if (!line.trim()) {
			flushP();
			flushL();
		} else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
			flushP();
			if (listTag !== 'ul') flushL();
			listTag = 'ul';
			list = (list || '') + '<li>' + inl(m[1]) + '</li>';
		} else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
			flushP();
			if (listTag !== 'ol') flushL();
			listTag = 'ol';
			list = (list || '') + '<li>' + inl(m[1]) + '</li>';
		} else if ((m = line.match(/^\s*>\s?(.*)$/))) {
			flushP();
			flushL();
			html += '<blockquote>' + inl(m[1]) + '</blockquote>';
		} else {
			flushL();
			para.push(line);
		}
	}
	flushP();
	flushL();
	return html;
}

/** epoch seconds → "YYYY-MM" (render.py fmtDate). */
export function fmtDate(e: number | null | undefined): string {
	if (!e) return '—';
	const d = new Date(e * 1000);
	return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0');
}
