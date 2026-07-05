// @ts-check

const config = {
  title: 'OmegaFlow Studio',
  tagline: 'Scripted terminal and video flows',
  url: 'https://omegaflow.dev',
  baseUrl: '/',
  organizationName: 'omry',
  projectName: 'omegaflow',
  onBrokenLinks: 'throw',
  markdown: {
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
};

module.exports = config;
