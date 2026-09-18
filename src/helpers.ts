export function withBase(path: string): string {
  const base = import.meta.env.BASE_URL;
  if (path.startsWith('http') || path.startsWith('#') || path.startsWith('mailto:')) {
    return path;
  }
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const normalizedBase = base.startsWith('/') ? base.slice(base.length) : base;
  return `${normalizedBase}${normalizedPath}`;
}

// URL slug for a facet value, e.g. 'professional services' -> 'professional-services',
// '3D' -> '3d'. Inverse of the facet page's display lookup.
export function facetSlug(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

// Absolute URL for contexts that require one (structured data, social tags).
// Falls back to the production site URL where Astro.site is unset.
export function absoluteUrl(path: string): string {
  const url = new URL(withBase(path), import.meta.env.SITE || 'https://madewithwagtail.org');
  return url.href;
}

// Plain-text excerpt of a Markdown body, for page meta descriptions: strips
// markup, collapses whitespace, and truncates on a word boundary. Returns
// undefined for empty bodies so callers can fall back to the site default.
export function markdownExcerpt(markdown: string | undefined, maxChars = 160): string | undefined {
  if (!markdown) return undefined;
  const text = markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\(([^)]*)\)/g, '$1')
    .replace(/^[ \t]*(?:[#>]+|[-*+]|\d+[.)])[ \t]*/gm, '')
    .replace(/[*_~]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!text) return undefined;
  if (text.length <= maxChars) return text;
  const cutoff = text.slice(0, maxChars);
  const lastSpace = cutoff.lastIndexOf(' ');
  return `${(lastSpace > 0 ? cutoff.slice(0, lastSpace) : cutoff).trimEnd()}…`;
}
