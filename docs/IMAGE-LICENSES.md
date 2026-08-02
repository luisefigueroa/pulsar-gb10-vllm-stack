# Published container-image licensing

The repository source code is Apache-2.0. Published container images contain
additional components under their own licenses and are **not licensed solely
under Apache-2.0**.

In particular, the PR-41834 release image is a compatible derived application
container built on NVIDIA's CUDA container. The NVIDIA Deep Learning Container
License is preserved inside the image at:

```text
/NGC-DL-CONTAINER-LICENSE
```

The image also contains vLLM and third-party Python, CUDA, and operating-system
packages under their respective licenses. Pulling or using the image does not
grant rights beyond those component licenses. The image is not sponsored or
endorsed by NVIDIA.

The published image provides the materially additional vLLM OpenAI-compatible
inference-server application. It is intended to run on NVIDIA GPU systems, in
accordance with the NVIDIA license included in the image.

For exact source and build provenance, see [BUILD.md](./BUILD.md).
