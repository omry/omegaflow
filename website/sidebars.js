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
    'configuration',
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
    'omegaflow',
    'video-output',
  ],
};
