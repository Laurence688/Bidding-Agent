# Bundled runtime provenance

Portable Release ZIPs include these independently distributed Windows x64 runtimes:

- CPython 3.11.17, Python Standalone Builds release 20261013, install-only stripped archive. Upstream: https://github.com/astral-sh/python-build-standalone/releases/tag/20261013 . Archive SHA256: `54a38c5e7cc1a5fd2b0182b495f29606dc1455d18ebfd3b5bf0a3c09253b5031`.
- Node.js 24.18.0 official Windows x64 ZIP. Upstream: https://nodejs.org/dist/v24.18.0/ . Archive SHA256: `0ae68406b42d7725661da979b1403ec9926da205c6770827f33aac9d8f26e821`.

The portable package retains upstream license notices in each runtime and copies the main Python and Node license files into this directory. Third-party Python dependency license notices remain with their installed distributions under `.runtimes/python/Lib/site-packages`; versions are pinned by `backend/requirements.lock.txt` and installation uses hashed wheels.

These are portable runtimes, not copies of a developer virtual environment. The application source and prebuilt frontend are supplied with empty user catalogs and configuration templates. No personal model API settings, credentials, knowledge files, databases or usage history are included.

Embedding model weights are not bundled. First-use initialization downloads Qwen/Qwen3-Embedding-0.6B separately; its upstream files and license remain in the user's local model directory.
