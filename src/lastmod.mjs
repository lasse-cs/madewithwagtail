import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Shared lastmod logic for site detail pages (/developers/<company>/<site>/)
// and developer profiles (/developers/<slug>/), driven by the
// latest_revision_created_at frontmatter field of the content entries:
// - Site pages use their own latest_revision_created_at.
// - Developer profiles use the date of the most recent site on their
//   profile (their own sites, plus sites credited to them via
//   in_cooperation_with_slug).
// Used both for the visible "Last updated" line and the sitemap <lastmod>.

// Pinned to UTC so builds render identical dates regardless of the
// build machine's timezone.
const dateFormatter = new Intl.DateTimeFormat('en-US', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
});

// Normalise any date-ish value ('' , null, ISO string) to a UTC ISO string.
function normalizeDate(value) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

// Newest of a list of date-ish values, as a UTC ISO string.
export function latestDate(dates) {
  let latest = null;
  for (const value of dates) {
    const date = normalizeDate(value);
    if (date && (!latest || date > latest)) {
      latest = date;
    }
  }
  return latest;
}

// 'December 6, 2024', or null when there is no valid date to show.
export function formatLastUpdated(iso) {
  const date = normalizeDate(iso);
  return date ? dateFormatter.format(new Date(date)) : null;
}

const monthYearFormatter = new Intl.DateTimeFormat('en-US', {
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
});

// 'December 2024', or null when there is no valid date to show.
export function formatListedSince(iso) {
  const date = normalizeDate(iso);
  return date ? monthYearFormatter.format(new Date(date)) : null;
}

function parseField(markdown, field) {
  const match = markdown.match(new RegExp(`^${field}:\\s*["']?([^\\s"']+)["']?\\s*$`, 'm'));
  return match ? match[1] : null;
}

// Scan the content directory for latest_revision_created_at values,
// mirroring the collections in src/content.config.ts:
// <dev>/index.md -> developer profile, <dev>/<site>/index.md -> site.
function scanLastmod() {
  const base = fileURLToPath(new URL('./content/developers', import.meta.url));
  const sites = new Map();
  const developers = new Map();

  if (!existsSync(base)) {
    return { sites, developers };
  }

  // Per developer: the dates of the sites shown on their profile.
  const devDates = new Map();

  for (const dev of readdirSync(base, { withFileTypes: true })) {
    if (!dev.isDirectory()) {
      continue;
    }
    const devPath = join(base, dev.name);
    if (existsSync(join(devPath, 'index.md'))) {
      devDates.set(dev.name, []);
    }
    for (const site of readdirSync(devPath, { withFileTypes: true })) {
      if (!site.isDirectory()) {
        continue;
      }
      const siteIndex = join(devPath, site.name, 'index.md');
      if (!existsSync(siteIndex)) {
        continue;
      }
      const frontmatter = readFileSync(siteIndex, 'utf8');
      const date = normalizeDate(parseField(frontmatter, 'latest_revision_created_at'));
      if (!date) {
        continue;
      }
      const siteId = `${dev.name}/${site.name}`;
      sites.set(siteId, date);
      devDates.get(dev.name)?.push(date);
      const cooperator = parseField(frontmatter, 'in_cooperation_with_slug');
      devDates.get(cooperator ?? '')?.push(date);
    }
  }

  for (const [id, dates] of devDates) {
    const latest = latestDate(dates);
    if (latest) {
      developers.set(id, latest);
    }
  }

  return { sites, developers };
}

// <lastmod> for a sitemap entry URL, or null for pages without one.
// https://madewithwagtail.org/developers/<slug>/ -> developer profile
// https://madewithwagtail.org/developers/<company>/<site>/ -> site page
export function getLastmodForUrl(url) {
  const lastmod = scanLastmodOnce();
  const segments = new URL(url).pathname.split('/').filter(Boolean);
  if (segments[0] !== 'developers') {
    return null;
  }
  if (segments.length === 2) {
    return lastmod.developers.get(segments[1]) ?? null;
  }
  if (segments.length === 3) {
    return lastmod.sites.get(`${segments[1]}/${segments[2]}`) ?? null;
  }
  return null;
}

let lastmodCache;

function scanLastmodOnce() {
  lastmodCache ??= scanLastmod();
  return lastmodCache;
}
