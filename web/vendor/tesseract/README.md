# Screenshot reader (self-hosted)

Used only when someone taps "Read a screenshot". Loaded on demand; the picture never leaves the phone.

| File | From | Version | Licence |
|---|---|---|---|
| tesseract.min.js, worker.min.js | npm `tesseract.js` | 7.0.0 | Apache-2.0 |
| tesseract-core-simd-lstm.wasm.js, tesseract-core-lstm.wasm.js | npm `tesseract.js-core` | 7.0.0 | Apache-2.0 |
| eng.traineddata.gz | npm `@tesseract.js-data/eng` (4.0.0_best_int) | 1.0.0 | Apache-2.0 |

The app picks the SIMD core when the browser supports it, else the plain one.
To update: `npm i tesseract.js@<v> tesseract.js-core@<v> @tesseract.js-data/eng` in a scratch folder and copy the same files.
