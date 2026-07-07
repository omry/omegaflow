module.exports = {
  docs: [
    'intro',
    'quick-start',
    'studio-configuration',
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
    'omegaflow-studio',
    'omegaflow-video',
  ],
};
