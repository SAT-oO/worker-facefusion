FaceFusion
==========

> Industry leading face manipulation platform.

[![Build Status](https://img.shields.io/github/actions/workflow/status/facefusion/facefusion/ci.yml.svg?branch=master)](https://github.com/facefusion/facefusion/actions?query=workflow:ci)
[![Coverage Status](https://img.shields.io/coveralls/facefusion/facefusion.svg)](https://coveralls.io/r/facefusion/facefusion)
![License](https://img.shields.io/badge/license-OpenRAIL--AS-green)


Preview
-------

![Preview](https://raw.githubusercontent.com/facefusion/facefusion/master/.github/preview.png?sanitize=true)


Installation
------------

Be aware, the [installation](https://docs.facefusion.io/installation) needs technical skills and is not recommended for beginners. In case you are not comfortable using a terminal, our [Windows Installer](http://windows-installer.facefusion.io) and [macOS Installer](http://macos-installer.facefusion.io) get you started.


Usage
-----

Run the command:

```
python facefusion.py [commands] [options]

options:
  -h, --help                                      show this help message and exit
  -v, --version                                   show program's version number and exit

commands:
    run                                           run the program
    headless-run                                  run the program in headless mode
    batch-run                                     run the program in batch mode
    force-download                                force automate downloads and exit
    benchmark                                     benchmark the program
    job-list                                      list jobs by status
    job-create                                    create a drafted job
    job-submit                                    submit a drafted job to become a queued job
    job-submit-all                                submit all drafted jobs to become a queued jobs
    job-delete                                    delete a drafted, queued, failed or completed job
    job-delete-all                                delete all drafted, queued, failed and completed jobs
    job-add-step                                  add a step to a drafted job
    job-remix-step                                remix a previous step from a drafted job
    job-insert-step                               insert a step to a drafted job
    job-remove-step                               remove a step from a drafted job
    job-run                                       run a queued job
    job-run-all                                   run all queued jobs
    job-retry                                     retry a failed job
    job-retry-all                                 retry all failed jobs
```


Documentation
-------------

Read the [documentation](https://docs.facefusion.io) for a deep dive.


RunPod Serverless
-----------------

This fork ships a RunPod serverless worker (`handler.py`) that runs `facefusion.py headless-run` for face swap jobs and uploads the result to a Cloudflare R2 bucket.

### Environment variables

| Variable | Description |
| --- | --- |
| `R2_ACCOUNT_ID` | Cloudflare account id used to build `https://<id>.r2.cloudflarestorage.com`. |
| `R2_ACCESS_KEY_ID` | R2 S3-compatible access key. |
| `R2_SECRET_ACCESS_KEY` | R2 S3-compatible secret key. |
| `R2_BUCKET` | Default bucket for uploads (overridden if `target_url` points to a different R2 bucket). |
| `R2_PUBLIC_BASE_URL` | Optional. When set, returned `output_url` is `${R2_PUBLIC_BASE_URL}/<key>`; otherwise a 24h presigned GET URL is generated. |

### Request schema (`event.input`)

```json
{
  "source_image_base64": "<base64 PNG/JPEG of source face>",
  "source_image_format": "png",
  "target_url": "https://<bucket>.<account>.r2.cloudflarestorage.com/path/to/target.mp4",
  "output_format": "mp4",
  "processors": ["face_swapper", "face_enhancer"],
  "face_swapper_model": "inswapper_128_fp16",
  "face_enhancer_model": "gfpgan_1.4",
  "extra_args": []
}
```

`source_image_base64` and `target_url` are required; all other fields are optional. `extra_args` is appended verbatim to the `headless-run` CLI.

### Response schema

```json
{
  "output_url": "https://.../outputs/<uuid>.mp4",
  "output_key": "outputs/<uuid>.mp4",
  "bucket": "<bucket>"
}
```

On failure the handler returns `{"error": "...", "stderr": "...", "stdout": "..."}` (or `{"error": "...", "traceback": "..."}`).

### Docker build / push

Models are baked into the image via `python facefusion.py force-download` during build, so cold-starts skip downloads.

```bash
docker build -t <registry>/worker-facefusion:latest .
docker push <registry>/worker-facefusion:latest
```

Point your RunPod serverless endpoint at the pushed image and set the `R2_*` environment variables above. For local testing, RunPod's convention is `python handler.py` with a `test_input.json` in the repo root.
