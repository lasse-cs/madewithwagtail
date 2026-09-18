// @ts-check

import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import { defineConfig, passthroughImageService } from 'astro/config';
import { getLastmodForUrl } from './src/lastmod.mjs';
import { llmsTxt } from './src/llms.mjs';

// https://astro.build/config
export default defineConfig({
  site: 'https://madewithwagtail.org',
  integrations: [
    react(),
    sitemap({
      // <lastmod> for site and developer profile pages, from the
      // latest_revision_created_at dates shown on those pages.
      serialize(item) {
        // /page/1/ and /developers/page/1/ duplicate their listing roots,
        // which they self-canonical to — keep them out of the sitemap.
        if (/\/(developers\/)?page\/1\/$/.test(new URL(item.url).pathname)) {
          return undefined;
        }
        const lastmod = getLastmodForUrl(item.url);
        if (lastmod) {
          item.lastmod = lastmod;
        }
        return item;
      },
    }),
    llmsTxt(),
  ],
  redirects: {
    // Neat URL to share for new site submissions.
    '/new': 'https://github.com/wagtail/madewithwagtail/issues/new?template=site-submission.yml',
    // Meta-refresh pages for the legacy tag URLs worth preserving; all other
    // legacy /sites/tag/ URLs are left to 404.
    '/sites/tag/education': '/sites/sector/education/',
    '/sites/tag/blog': '/sites/type/blog/',
    '/sites/tag/portfolio': '/sites/type/portfolio/',
    '/sites/tag/industry': '/sites/sector/industry/',
    '/sites/tag/news': '/sites/type/news/',
  },
  // Serve content images as-is: no resizing, re-encoding, or format conversion.
  image: { service: passthroughImageService() },
  vite: {
    css: {
      lightningcss: {
        errorRecovery: true,
      },
    },
  },
});
