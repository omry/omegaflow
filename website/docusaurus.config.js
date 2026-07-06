// @ts-check

const config = {
  title: 'OmegaFlow Studio',
  tagline: 'Scripted terminal walkthroughs you can rebuild',
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
    navbar: {
      title: 'OmegaFlow Studio',
      items: [
        {
          href: 'https://github.com/omry/omegaflow',
          label: 'Repo',
          position: 'right',
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
              label: 'OmegaFlow Studio',
              to: '/omegaflow-studio',
            },
            {
              label: 'OmegaFlow Video',
              to: '/omegaflow-video',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} OmegaFlow Studio.`,
    },
  },
};

module.exports = config;
