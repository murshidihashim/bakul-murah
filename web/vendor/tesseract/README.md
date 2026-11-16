# Screenshot reader (self-hosted)

Used only when someone taps "Read a screenshot". Loaded on demand; the picture never leaves the phone.

| File | From | Version | Licence | Also contains (licence) |
|---|---|---|---|---|
| tesseract.min.js, worker.min.js | npm `tesseract.js` | 7.0.0 | Apache-2.0 | regenerator-runtime (MIT), in both. worker.min.js also has base64-js, bmp-js, buffer, is-url, zlibjs (MIT), ieee754 (BSD-3-Clause), idb-keyval and wasm-feature-detect (Apache-2.0). |
| tesseract-core-simd-lstm.wasm.js, tesseract-core-lstm.wasm.js | npm `tesseract.js-core` | 7.0.0 | Apache-2.0 | Tesseract (Apache-2.0), Leptonica (BSD-2-Clause), libjpeg (IJG), libpng (libpng-2.0), zlib (Zlib), libtiff (libtiff), libwebp (BSD-3-Clause), giflib (MIT), OpenLibm (MIT, ISC, BSD-2-Clause) and the Emscripten runtime (Emscripten MIT or NCSA, musl MIT, LLVM Apache-2.0 with LLVM exception, dlmalloc public domain). |
| eng.traineddata.gz | npm `@tesseract.js-data/eng` (4.0.0_best_int) | 1.0.0 | Apache-2.0 | (data, no code) |

Licence texts: `LICENSE-tesseract.js.md` and `LICENSE-tesseract.js-core` (Apache-2.0), and
`THIRD-PARTY-NOTICES.txt` for everything in the last column, with the exact versions and where each text came from.
The two scripts say "see *.min.js.LICENSE.txt"; that text is in `THIRD-PARTY-NOTICES.txt`, section 11.

This software is based in part on the work of the Independent JPEG Group.

The app picks the SIMD core when the browser supports it, else the plain one.
To update: `npm i tesseract.js@<v> tesseract.js-core@<v> @tesseract.js-data/eng` in a scratch folder and copy the same files.
Then update `THIRD-PARTY-NOTICES.txt` for the new versions. tesseract.js-core pins its libraries as git submodules in
`third_party/` at its release tag. Each script's source map (`dist/*.min.js.map` in the npm package) lists the npm
modules inside it.
