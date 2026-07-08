// @ts-check

const config = {
  title: 'OmegaFlow',
  tagline: 'Scripted terminal walkthroughs you can rebuild',
  favicon: 'img/favicon.svg',
  url: 'https://omegaflow.dev',
  baseUrl: '/',
  organizationName: 'omry',
  projectName: 'omegaflow',
  onBrokenLinks: 'throw',
  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },
  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: require.resolve('./sidebars.js'),
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      },
    ],
  ],
  themes: ['@docusaurus/theme-mermaid'],
  themeConfig: {
    colorMode: {
      defaultMode: 'dark',
      disableSwitch: true,
      respectPrefersColorScheme: false,
    },
    prism: {
      additionalLanguages: ['bash', 'yaml'],
    },
    navbar: {
      title: 'OmegaFlow',
      logo: {
        alt: 'OmegaFlow mark',
        src: 'img/omegaflow-mascot.svg',
      },
      items: [
        {
          to: '/intro',
          label: 'Docs',
          position: 'left',
        },
        {
          to: '/tutorial',
          label: 'Tutorial',
          position: 'left',
        },
        {
          to: '/omegaflow',
          label: 'CLI',
          position: 'left',
        },
        {
          href: 'https://github.com/omry/omegaflow',
          label: 'OmegaFlow@GitHub',
          position: 'left',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Project',
          items: [
            {
              label: 'Repository',
              href: 'https://github.com/omry/omegaflow',
            },
            {
              label: 'Quick Start',
              to: '/quick-start',
            },
          ],
        },
        {
          title: 'Reference',
          items: [
            {
              label: 'OmegaFlow',
              to: '/omegaflow',
            },
            {
              label: 'Video Output',
              to: '/video-output',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} OmegaFlow.`,
    },
  },
};

module.exports = config;
