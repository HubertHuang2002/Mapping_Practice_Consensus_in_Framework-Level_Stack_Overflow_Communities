// SPA mode: render entirely client-side. There is no server at runtime — the static bundle
// fetches the breakdown from the FastAPI serve plane (ADR 0009). No prerender, no SSR.
export const ssr = false;
export const prerender = false;
