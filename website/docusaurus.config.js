// @ts-check

const config = {
  title: 'OmegaFlow',
  tagline: 'Scripted workflows you can rebuild as video',
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
    image: 'img/omegaflow-social.png',
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
        src: 'img/omegaflow-logo.svg',
      },
      items: [
        {
          to: '/getting-started/',
          label: 'Get Started',
          position: 'left',
        },
        {
          to: '/tutorial/',
          label: 'Tutorial',
          position: 'left',
        },
        {
          to: '/guides/',
          label: 'Guides',
          position: 'left',
        },
        {
          to: '/reference/',
          label: 'Reference',
          position: 'left',
        },
        {
          href: 'https://github.com/omry/omegaflow',
          label: 'GitHub',
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
              label: 'MPL 2.0 License',
              href: 'https://github.com/omry/omegaflow/blob/main/LICENSE',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} OmegaFlow.`,
    },
  },
};

module.exports = config;
