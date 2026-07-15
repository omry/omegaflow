module.exports = {
  docs: [
    'intro',
    'quick-start',
    {
      type: 'category',
      label: 'Tutorial',
      link: {
        type: 'doc',
        id: 'tutorial/overview',
      },
      items: [
        'tutorial/quickstart',
        'tutorial/recording-file',
        'tutorial/beat',
        'tutorial/publishing',
      ],
    },
    {
      type: 'category',
      label: 'Recording Files',
      link: {
        type: 'doc',
        id: 'recording-files/overview',
      },
      items: [
        'recording-files/config',
        'recording-files/beat',
        'recording-files/publishing-runtime',
      ],
    },
    {
      type: 'category',
      label: 'OmegaFlow CLI',
      link: {
        type: 'doc',
        id: 'omegaflow',
      },
      items: [
        'cli/command-syntax',
        {
          type: 'category',
          label: 'Actions',
          items: [
            'cli/actions/build-check',
            'cli/actions/bootstrap',
            'cli/actions/watch',
            'cli/actions/list-clean',
            'cli/actions/runs-inspect-output',
          ],
        },
        'configuration',
        'cli/overrides-parameters',
        'cli/runs-troubleshooting',
        'cli/option-reference',
      ],
    },
    'video-output',
  ],
};
