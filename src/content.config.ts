import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const developers = defineCollection({
  // One directory per developer; the profile page is <dev>/index.md, and
  // sites they built live in <dev>/<site>/index.md (see the sites collection).
  loader: glob({ base: './src/content/developers', pattern: '*/index.md' }),
  schema: z.object({
    // The developer slug is the developer directory name, i.e. the first
    // segment of site entry IDs ('<company>/<site>').
    title: z.string(),
    first_published_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    latest_revision_created_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    // The logo is colocated at src/content/developers/<id>/<id>.max-120x120.webp,
    // derived from the entry ID in code — no frontmatter needed.
    location: z.string().nullable().default(null),
    lat: z.string().nullable().default(null),
    lon: z.string().nullable().default(null),
    company_url: z.string().nullable().default(null),
    // Profile page derives the Twitter profile URL from the handler
    // (https://twitter.com/<twitter_handler>).
    twitter_handler: z.string().nullable().default(null),
    // Profile page derives the GitHub profile URL from the username
    // (https://github.com/<github_user>).
    github_user: z.string().nullable().default(null),
    // Verbatim URLs for profiles on other platforms (Instagram, GitLab,
    // personal sites). Unused by pages for now.
    online_profiles: z.array(z.string()).default([]),
  }),
});

const sites = defineCollection({
  // Site entries live inside their developer directory:
  // <dev>/<site>/index.md. IDs are '<dev>/<site>', which keeps site slugs
  // unique across developers.
  loader: glob({ base: './src/content/developers', pattern: '*/*/index.md' }),
  schema: z.object({
    // The site slug is the last segment of the entry ID
    // ('<company>/<site>' -> 'site'). Site slugs are unique across
    // developers: when two developers built a site together, it lives
    // under one directory and the other is credited with
    // in_cooperation_with_slug.
    // The company slug is the first segment of the entry ID
    // ('<company>/<site>' -> 'company'), matching the parent folder.
    // All site entries are live; only developer profiles use live: false.
    title: z.string(),
    first_published_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    latest_revision_created_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    site_url: z.string().nullable().default(null),
    // The screenshot is colocated at
    // src/content/developers/<company>/<site>/<site>.fill-1200x996.webp,
    // derived from the entry ID in code — no frontmatter needed.
    in_cooperation_with_slug: z.string().nullable().default(null),
    // Faceted classification, migrated from the legacy free-form tags field.
    // Sector: what the site's organisation does (education, healthcare, ...).
    sector: z.array(z.string()).default([]),
    // Site type: what the site is for its users (blog, news, e-commerce, ...).
    site_type: z.array(z.string()).default([]),
    // Capability: what the build achieved (multilingual, maps, headless, ...).
    capability: z.array(z.string()).default([]),
    // Front-end technologies, from the submission pipeline's Wappalyzer scan
    // (e.g. ['React', 'Tailwind CSS']) or migrated from legacy tech tags.
    // The site page omits it when empty.
    technologies: z.array(z.string()).default([]),
  }),
});

const facets = defineCollection({
  // Hand-written intro copy for facet pages (/sites/<facet>/<value>/), one
  // file per facet value that has one: <facet>/<value-slug>.md, e.g.
  // 'sector/government.md'. Rendered above the site grid on the facet page.
  loader: glob({ base: './src/content/facets', pattern: '*/*.md' }),
  schema: z.object({}),
});

export const collections = { developers, sites, facets };
