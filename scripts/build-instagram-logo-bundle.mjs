import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const sourceDir = path.join(root, 'instagram-assets', 'logos', 'source');
const outputFile = path.join(root, 'instagram-assets', 'logo-bundle.js');
const inventoryFile = path.join(root, 'instagram-assets', 'logo-inventory.json');
const brandIconFile = path.join(root, 'instagram-assets', 'branding', 'app-icon.png');

// Every correction is optical: source SVGs all use a 500×500 viewBox, but the
// visible marks occupy very different portions of that square.
const logoMap = {
  f1:               { file: 'f1-wit.svg', scale: 1.16, maxWidth: 164, x: 0, y: 0 },
  f2:               { file: 'f2-wit.svg', scale: 1.12, maxWidth: 154, x: 0, y: 0 },
  f3:               { file: 'f3-wit.svg', scale: 1.12, maxWidth: 154, x: 0, y: 0 },
  f1academy:        { file: 'f1a-wit.svg', scale: 1.08, maxWidth: 150, x: 0, y: 0 },
  formulae:         { file: 'fe-wit.svg', scale: 1.06, maxWidth: 150, x: 0, y: 0 },
  indycar:          { file: 'indycar-2-dark.svg', scale: 1.08, maxWidth: 154, x: 0, y: 0 },
  indynxt:          { file: 'indynxt-wit.svg', scale: 1.08, maxWidth: 154, x: 0, y: 0 },
  nascar:           { file: 'nascarcup.svg', scale: 1.12, maxWidth: 162, x: 0, y: 0 },
  nascar_oreilly:   { file: 'nascaroreilly.svg', scale: 1.06, maxWidth: 160, x: 0, y: 0 },
  nascar_trucks:    { file: 'nascartrucks.svg', scale: 1.08, maxWidth: 160, x: 0, y: 0 },
  nascareuro:       { file: 'nascareuro.svg', scale: 1.05, maxWidth: 158, x: 0, y: 0 },
  wec:              { file: 'wec-wit.svg', scale: 1.14, maxWidth: 162, x: 0, y: 0 },
  imsa:             { file: 'imsa-wit.svg', scale: 1.1, maxWidth: 158, x: 0, y: 0 },
  elms:             { file: 'elms-dark.svg', scale: 1.06, maxWidth: 158, x: 0, y: 0 },
  alms:             { file: 'alms-dark.svg', scale: 1.06, maxWidth: 158, x: 0, y: 0 },
  lemanscup:        { file: 'lemanscup-dark.svg', scale: 1.02, maxWidth: 156, x: 0, y: 0 },
  '24hseries':      { file: '24series-dark.svg', scale: 1.02, maxWidth: 152, x: 0, y: 0 },
  igtc:             { file: 'igtc.svg', scale: 1.02, maxWidth: 156, x: 0, y: 0 },
  gtwce:            { file: 'gtweu-dark.svg', scale: 1.04, maxWidth: 158, x: 0, y: 0 },
  gtwca_am:         { file: 'gtwa.svg', scale: 1.04, maxWidth: 158, x: 0, y: 0 },
  gtwca_asia:       { file: 'gtwasia-dark.svg', scale: 1.04, maxWidth: 158, x: 0, y: 0 },
  gtwca_aus:        { file: 'gtwaustralia-dark.svg', scale: 1.04, maxWidth: 158, x: 0, y: 0 },
  british_gt:       { file: 'britishgt-dark.svg', scale: 1.08, maxWidth: 158, x: 0, y: 0 },
  nls:              { file: 'nls-dark.svg', scale: 1.08, maxWidth: 158, x: 0, y: 0 },
  dtm:              { file: 'dtm.svg', scale: 1.16, maxWidth: 160, x: 0, y: 0 },
  btcc:             { file: 'btcc-wit.svg', scale: 1.08, maxWidth: 158, x: 0, y: 0 },
  tcr:              { file: 'tcr-wit.svg', scale: 1.08, maxWidth: 158, x: 0, y: 0 },
  supercars:        { file: 'supercars-wit.svg', scale: 1.08, maxWidth: 158, x: 0, y: 0 },
  porsche_supercup: { file: 'porschesupercup-wit.svg', scale: 1.0, maxWidth: 154, x: 0, y: 0 },
  motogp:           { file: 'motogp-dark.svg', scale: 1.16, maxWidth: 160, x: 0, y: 0 },
  moto2:            { file: 'moto2-dark.svg', scale: 1.16, maxWidth: 160, x: 0, y: 0 },
  moto3:            { file: 'moto3-dark.svg', scale: 1.16, maxWidth: 160, x: 0, y: 0 },
  wsbk:             { file: 'wsbk-dark.svg', scale: 1.04, maxWidth: 154, x: 0, y: 0 },
  wrc:              { file: 'wrc-wit.svg', scale: 1.08, maxWidth: 156, x: 0, y: 0 },
  rx:               { file: 'rx-wit.svg', scale: 1.1, maxWidth: 158, x: 0, y: 0 },
};

const files = fs.readdirSync(sourceDir).filter(file => file.endsWith('.svg')).sort();
const fileMetadata = Object.fromEntries(files.map(file => {
  const raw = fs.readFileSync(path.join(sourceDir, file), 'utf8');
  const viewBox = raw.match(/viewBox=["']([^"']+)["']/i)?.[1] || null;
  const rootBackground = /<rect\b[^>]*(?:width=["'](?:500|100%)["'])[^>]*(?:height=["'](?:500|100%)["'])/i.test(raw);
  return [file, {
    bytes: Buffer.byteLength(raw),
    format: 'SVG',
    viewBox,
    aspectRatio: viewBox ? Number(viewBox.split(/\s+/)[2]) / Number(viewBox.split(/\s+/)[3]) : null,
    transparentBackground: !rootBackground,
  }];
}));

const logos = Object.fromEntries(Object.entries(logoMap).map(([seriesId, config]) => {
  const raw = fs.readFileSync(path.join(sourceDir, config.file), 'utf8');
  return [seriesId, {
    ...config,
    src: `data:image/svg+xml;base64,${Buffer.from(raw).toString('base64')}`,
  }];
}));
const brandIcon = `data:image/png;base64,${fs.readFileSync(brandIconFile).toString('base64')}`;

const inventory = {
  generatedAt: new Date().toISOString(),
  sourceArchive: 'Logos  2.zip',
  sourceFiles: fileMetadata,
  mappings: Object.fromEntries(Object.entries(logoMap).map(([id, value]) => [id, value])),
  unmappedFiles: files.filter(file => !Object.values(logoMap).some(item => item.file === file)),
};

fs.writeFileSync(inventoryFile, `${JSON.stringify(inventory, null, 2)}\n`);
fs.writeFileSync(outputFile,
  `/* Generated by scripts/build-instagram-logo-bundle.mjs. Do not edit by hand. */\n` +
  `window.RACEDAY_INSTAGRAM_LOGOS = ${JSON.stringify(logos)};\n` +
  `window.RACEDAY_INSTAGRAM_BRAND = ${JSON.stringify({ icon: brandIcon })};\n`);

console.log(`Bundled ${Object.keys(logos).length} series logos from ${files.length} supplied SVG files.`);
