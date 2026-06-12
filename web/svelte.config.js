import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => filename.split(/[/\\]/).includes('node_modules') ? undefined : true
	},
	// SPA: no prerender, single fallback page; the app fetches the breakdown from the
	// FastAPI serve plane at runtime (ADR 0009 PRESENT layer).
	kit: { adapter: adapter({ fallback: 'index.html' }) }
};

export default config;
