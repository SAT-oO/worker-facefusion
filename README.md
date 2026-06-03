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

This fork ships a RunPod serverless worker (`handler.py`) that runs `facefusion.py headless-run` for face swap jobs and uploads the result to Cloudflare R2.

**For full setup and integration specs** — RunPod endpoint configuration, environment variables, request/response contracts, R2 URL rules, calling the API from your application, FlashBoot, performance profiles, and local testing — see **[RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md)**.

Quick start:

```bash
docker build -t <registry>/worker-facefusion:latest .
docker push <registry>/worker-facefusion:latest
```

Create a RunPod serverless endpoint from that image, set the `R2_*` variables documented in `RUNPOD_SERVERLESS.md`, and submit jobs via `POST https://api.runpod.ai/v2/{endpoint_id}/run`. For local testing: `python handler.py` with [`test_input.json`](test_input.json) in the repo root, or `bash tests/runpod/run_e2e.sh` for a full GPU stack.
