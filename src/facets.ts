import { facetSlug } from './helpers.ts';

// Facet routes -> the site collection field they filter on.
export const FACETS = {
  sector: 'sector',
  type: 'site_type',
  capability: 'capability',
} as const;

export type FacetKey = keyof typeof FACETS;

export interface FacetValue {
  slug: string;
  display: string;
  count: number;
}

// Label shown on facet pages, e.g. 'Sector', 'Site type', 'Capability'.
export function facetLabel(facet: FacetKey): string {
  return facet === 'type' ? 'Site type' : facet === 'capability' ? 'Capability' : 'Sector';
}

// All values of a facet with their site counts, sorted most-used first.
// Display spelling is the first variant seen in the content.
export function facetValues(
  facet: FacetKey,
  sites: { data: { [K in (typeof FACETS)[FacetKey]]?: string[] } }[],
) {
  const field = FACETS[facet];
  const values = new Map<string, FacetValue>();
  for (const site of sites) {
    for (const value of site.data[field] ?? []) {
      const slug = facetSlug(value);
      const existing = values.get(slug);
      if (existing) {
        existing.count += 1;
      } else {
        values.set(slug, { slug, display: value, count: 1 });
      }
    }
  }
  return Array.from(values.values()).sort(
    (a, b) => b.count - a.count || a.display.localeCompare(b.display),
  );
}
