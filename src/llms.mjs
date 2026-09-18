import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Build-time generator for /llms.txt (https://llmstxt.org), listing what the
// showcase is, its current size, and its entry points — so AI tools get an
// accurate, structured summary instead of scraping card grids.
//
// An Astro integration with an astro:build:done hook: the counts are computed
// by scanning the content collections' source files (same structure as
// src/lastmod.mjs) so they always match the published site.

const SITE_URL = 'https://madewithwagtail.org';

// Facet routes -> the site collection field they filter on. Mirrors
// src/facets.ts (which can't be imported here: it's an Astro module).
const FACETS = [
  ['sector', 'sector'],
  ['type', 'site_type'],
  ['capability', 'capability'],
];

const facetSlug = (value) =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

// Values of a frontmatter list field, in both YAML spellings:
// 'sector:\n  - Education\n  - ...' and 'sector:\n- Education\n- ...'
function parseFrontmatterList(frontmatter, field) {
  const match = frontmatter.match(new RegExp(`^${field}:\\n((?:[ \\t]*-.+\\n?)+)`, 'm'));
  if (!match) return [];
  return [...match[1].matchAll(/^[ \t]*-[ \t]*(.+)$/gm)].map((m) =>
    m[1].trim().replace(/^["']|["']$/g, ''),
  );
}

const titleCase = (value) => value.charAt(0).toUpperCase() + value.slice(1);

function scanContent() {
  const base = fileURLToPath(new URL('./content/developers', import.meta.url));
  const counts = { developers: 0, sites: 0, facets: {} };

  if (!existsSync(base)) {
    return counts;
  }

  const facetValues = new Map();
  for (const [facet] of FACETS) {
    facetValues.set(facet, new Map());
    counts.facets[facet] = [];
  }

  for (const dev of readdirSync(base, { withFileTypes: true })) {
    if (!dev.isDirectory()) continue;
    if (existsSync(join(base, dev.name, 'index.md'))) {
      counts.developers += 1;
    }
    const devPath = join(base, dev.name);
    for (const site of readdirSync(devPath, { withFileTypes: true })) {
      const siteIndex = join(devPath, site.name, 'index.md');
      if (!site.isDirectory() || !existsSync(siteIndex)) continue;
      counts.sites += 1;
      // Frontmatter only: between the opening and closing '---' lines.
      const frontmatter = readFileSync(siteIndex, 'utf8').split(/^---$/m)[1] ?? '';
      for (const [facet, field] of FACETS) {
        const values = facetValues.get(facet);
        for (const value of parseFrontmatterList(frontmatter, field)) {
          const slug = facetSlug(value);
          const existing = values.get(slug);
          if (existing) {
            existing.count += 1;
          } else {
            values.set(slug, { slug, display: value, count: 1 });
          }
        }
      }
    }
  }

  for (const [facet, values] of facetValues) {
    counts.facets[facet] = [...values.values()].sort(
      (a, b) => b.count - a.count || a.display.localeCompare(b.display),
    );
  }
  return counts;
}

function facetSection(name, values) {
  if (values.length === 0) return '';
  const label = name === 'type' ? 'site type' : name;
  const lines = values.map(
    (value) =>
      `- [${titleCase(value.display)}](${SITE_URL}/sites/${name}/${value.slug}/): ${value.count} ${value.count === 1 ? 'site' : 'sites'}.`,
  );
  return `## Sites by ${label}\n\n${lines.join('\n')}`;
}

export function buildLlmsTxt({ siteUrl = SITE_URL } = {}) {
  const counts = scanContent();
  const facetCount = Object.values(counts.facets).reduce((sum, v) => sum + v.length, 0);

  const sections = [
    '# Made with Wagtail',

    `> A community showcase of production websites and apps built with [Wagtail](https://wagtail.org), the open source Django content management system. Maintained under the official \`wagtail\` GitHub organisation; started at a Springload hackathon in Wellington, NZ in 2015.`,

    `Made with Wagtail currently lists ${counts.sites} sites from ${counts.developers} developers and agencies, tagged across ${facetCount} sector, site type, and capability collections. Sites are submitted by their developers via a GitHub issue form; an automated pipeline validates the URL, captures a screenshot, and opens a pull request for maintainer review before publication.`,

    `## Browse

- [All sites](${siteUrl}/): every showcased site, most recently updated first.
- [Sites by sector, type, and capability](${siteUrl}/sites/): hub for all ${facetCount} facet collections.
- [Developers](${siteUrl}/developers/): ${counts.developers} agencies and individual developers who build with Wagtail, each with a profile and site portfolio.
- [Search](${siteUrl}/search/): full-text search across the showcase.`,

    facetSection('sector', counts.facets.sector),
    facetSection('type', counts.facets.type),
    facetSection('capability', counts.facets.capability),

    `## Submit a site

[Submission form](${siteUrl}/submit-your-site/) — opens a GitHub issue that triggers automated validation and a review pull request.`,

    `## Source

Open source, built with Astro, deployed to GitHub Pages: [github.com/wagtail/madewithwagtail](https://github.com/wagtail/madewithwagtail).`,
  ];

  return `${sections.filter(Boolean).join('\n\n')}\n`;
}

export function llmsTxt() {
  return {
    name: 'llms-txt',
    hooks: {
      'astro:build:done': ({ dir, logger }) => {
        writeFileSync(new URL('llms.txt', dir), buildLlmsTxt());
        logger.info('Wrote llms.txt');
      },
    },
  };
}
