import { readFile, writeFile } from 'node:fs/promises';

// Regenerate after editing the scanner's clock or venue timezone registry.
const root = new URL('../', import.meta.url);
const registry = JSON.parse(await readFile(new URL('session-time-sources.json', root), 'utf8'));
const bundle = { editorTimeZones: registry.editorTimeZones, eventTimeZones: registry.eventTimeZones };
await writeFile(new URL('instagram-assets/timezone-bundle.js', root),
  '/* Generated from session-time-sources.json: stored clock zones and circuit zones. */\n' +
  `window.RACEDAY_INSTAGRAM_TIME_ZONES = ${JSON.stringify(bundle, null, 2)};\n`);
console.log('Instagram timezone bundle rebuilt.');
