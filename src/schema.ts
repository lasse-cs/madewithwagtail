import { absoluteUrl } from './helpers.ts';

// Builders for the JSON-LD nodes rendered by BaseLayout's `jsonLd` prop.
// Nodes are @context-free: the layout wraps them in a single @graph.

export interface EntityRef {
  '@id': string;
}

// Site-wide entity nodes, referenced from every page via @id:
// the showcase (WebSite) and the project behind it (Organization).
export function siteEntityNodes() {
  const url = absoluteUrl('/');
  return [
    {
      '@type': 'WebSite',
      '@id': `${url}#website`,
      url,
      name: 'Made with Wagtail',
      description: 'A showcase of sites made with Wagtail, the popular Django CMS.',
      publisher: { '@id': `${url}#organization` } satisfies EntityRef,
    },
    {
      '@type': 'Organization',
      '@id': `${url}#organization`,
      url,
      name: 'Made with Wagtail',
      description:
        'A community-maintained showcase of production websites and apps built with Wagtail CMS.',
    },
  ];
}

// A listing page's items (site cards, developer cards, facet results).
export interface ListItem {
  name: string;
  url: string;
}

export function collectionPageNode({
  path,
  name,
  items,
  positionOffset = 0,
}: {
  path: string;
  name: string;
  items: ListItem[];
  positionOffset?: number;
}) {
  const url = absoluteUrl(path);
  return {
    '@type': 'CollectionPage',
    '@id': `${url}#webpage`,
    url,
    name,
    isPartOf: { '@id': `${absoluteUrl('/')}#website` } satisfies EntityRef,
    mainEntity: {
      '@type': 'ItemList',
      itemListElement: items.map((item, index) => ({
        '@type': 'ListItem',
        position: positionOffset + index + 1,
        name: item.name,
        url: item.url,
      })),
    },
  };
}

// Mirrors the visual breadcrumb trail; the final item is the current page,
// as recommended for BreadcrumbList rich results.
export function breadcrumbListNode(items: ListItem[]) {
  return {
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name: item.name,
      item: item.url,
    })),
  };
}

// The showcased site. CreativeWork (not WebSite/Organization): this site
// describes websites that were built, and doesn't own the external entities.
export function creativeWorkNode({
  path,
  name,
  siteUrl,
  description,
  image,
  datePublished,
  dateModified,
  keywords,
  creatorId,
}: {
  path: string;
  name: string;
  siteUrl: string | null;
  description: string | undefined;
  image: string | null;
  datePublished: string;
  dateModified: string;
  keywords: string[];
  creatorId: string | null;
}) {
  const pageUrl = absoluteUrl(path);
  return {
    '@type': 'CreativeWork',
    '@id': `${pageUrl}#creativework`,
    name,
    url: siteUrl ?? undefined,
    mainEntityOfPage: pageUrl,
    description,
    image: image ? absoluteUrl(image) : undefined,
    datePublished: datePublished || undefined,
    dateModified: dateModified || undefined,
    keywords: keywords.length > 0 ? keywords.join(', ') : undefined,
    creator: creatorId ? ({ '@id': creatorId } satisfies EntityRef) : undefined,
    publisher: { '@id': `${absoluteUrl('/')}#organization` } satisfies EntityRef,
  };
}

// A developer/agency profile. Fields are omitted (not emitted empty) when
// absent from the frontmatter.
export function organizationNode({
  path,
  name,
  companyUrl,
  description,
  logo,
  location,
  lat,
  lon,
  twitterHandler,
  githubUser,
}: {
  path: string;
  name: string;
  companyUrl: string | null;
  description: string | undefined;
  logo: string | null;
  location: string | null;
  lat: string | null;
  lon: string | null;
  twitterHandler: string | null;
  githubUser: string | null;
}) {
  const pageUrl = absoluteUrl(path);
  const sameAs = [
    twitterHandler && `https://twitter.com/${twitterHandler.replace(/^@+/, '')}`,
    githubUser && `https://github.com/${githubUser}`,
  ].filter((value) => Boolean(value));
  return {
    '@type': 'Organization',
    '@id': `${pageUrl}#organization`,
    name,
    url: companyUrl ?? pageUrl,
    mainEntityOfPage: pageUrl,
    description,
    logo: logo ? absoluteUrl(logo) : undefined,
    address: location ? { '@type': 'PostalAddress', addressLocality: location } : undefined,
    geo: lat && lon ? { '@type': 'GeoCoordinates', latitude: lat, longitude: lon } : undefined,
    sameAs: sameAs.length > 0 ? sameAs : undefined,
  };
}
