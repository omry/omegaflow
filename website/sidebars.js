module.exports = {
  docs: [
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      link: {
        type: 'doc',
        id: 'getting-started/index',
      },
      items: [
        'getting-started/install',
        'getting-started/first-video',
        'getting-started/next-steps',
      ],
    },
    'concepts/index',
    {
      type: 'category',
      label: 'Guides',
      collapsed: true,
      link: {
        type: 'doc',
        id: 'guides/index',
      },
      items: [
        {
          type: 'category',
          label: 'Authoring',
          items: [
            'guides/terminal-workflows',
            'guides/browser-workflows',
            'guides/narration-synchronization',
            'guides/guided-playback',
            'guides/presentation-effects',
          ],
        },
        'guides/publishing',
        'guides/troubleshooting',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: true,
      link: {
        type: 'doc',
        id: 'reference/index',
      },
      items: [
        {
          type: 'category',
          label: 'CLI',
          collapsed: true,
          items: [
            'reference/cli/syntax',
            'reference/cli/build-check',
            'reference/cli/bootstrap',
            'reference/cli/watch',
            'reference/cli/list-maintenance',
            'reference/cli/runs',
            'reference/cli/options',
          ],
        },
        {
          type: 'category',
          label: 'Configuration',
          collapsed: true,
          items: [
            'reference/configuration/project',
            'reference/configuration/recordings',
            'reference/configuration/overrides',
          ],
        },
        {
          type: 'category',
          label: 'Recording Files',
          collapsed: true,
          items: [
            'reference/recording-files/index',
            'reference/recording-files/schema',
          ],
        },
        {
          type: 'category',
          label: 'Output',
          collapsed: true,
          items: [
            'reference/output/index',
            'reference/output/presentation',
          ],
        },
      ],
    },
  ],
};
