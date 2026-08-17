/* Explicit display variants for the Instagram generator. */
(() => {
  if (!window.RACEDAY_INSTAGRAM_LOGOS) return;
  Object.assign(window.RACEDAY_INSTAGRAM_LOGOS, {
    f1: {
      file: 'f1.svg',
      src: 'instagram-assets/logos/source/f1.svg',
      scale: 1.16,
      maxWidth: 164,
      x: 0,
      y: 0,
      mono: false,
    },
    imsa: {
      file: 'imsa.svg',
      src: 'instagram-assets/logos/source/imsa.svg',
      scale: 1.1,
      maxWidth: 158,
      x: 0,
      y: 0,
      mono: false,
    },
  });
})();
