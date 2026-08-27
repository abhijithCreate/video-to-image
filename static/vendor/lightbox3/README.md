# Lightbox3 (vendored)

- Version: 1.3.0
- Source: https://lokeshdhakar.com/projects/lightbox3/ (npm: `lightbox3`)
- Licence: MIT

Vendored rather than loaded from a CDN so image viewing keeps working on a
self-hosted or offline deployment, and so the version cannot drift underneath
us. To update: fetch `dist/lightbox3.css` and `dist/lightbox3.min.js` for the
new version, bump the version above, and re-check the caption markup in
`static/js/app.js` still renders (captions are injected as HTML).
