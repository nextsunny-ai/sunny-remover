# SUNNY REMOVER

Remove watermarks from AI-generated images, right in your browser. No upload, free.

AI 생성 이미지의 워터마크를 브라우저 안에서 제거합니다. 업로드 없음, 무료.

## How it works

- **In-browser AI inpainting** — runs the MI-GAN model with onnxruntime-web (WebGPU, WASM fallback). Images never leave your device.
- **Auto-detect** the bright watermark (logo / sparkle) in the bottom-right corner, or **paint** the area manually with the brush.
- **PWA** — open on a phone and "Add to Home Screen" to use it like an app.
- Korean / English (browser language auto, with a toggle).

## Run locally

Serve the folder over HTTP (a service worker needs http/https, not `file://`):

```
python -m http.server 8000
```

Then open `http://localhost:8000`.

## Tech

- Engine: [MI-GAN](https://github.com/Picsart-AI-Research/MI-GAN) (`migan_pipeline_v2.onnx`, MIT)
- Runtime: [onnxruntime-web](https://onnxruntime.ai/) (WebGPU / WASM)
- Single static HTML — no build step, no server, no API.

## License

App code: MIT. Model: MIT (MI-GAN).
