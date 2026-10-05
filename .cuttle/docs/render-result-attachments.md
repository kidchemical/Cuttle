# Render result attachments

Experimental flag: `render_result_attachments` (default off). Settings →
Experimental → Render result attachments. No browser permission is needed.

`api.device_workers.render_results` owns durable batch-to-chat bindings and
delivery. The host's worker completion hook stages the media and inserts a saved
assistant message without an LLM or browser. Watch reconciliation retries missing
outputs, and live watch cards sync the saved message. Closed chats recover it
from ordinary history when reopened. Existing shared-media previews/lightbox,
posters, download, and seven-day staging retention apply.

## Agent recipe

Register every batch you want to attach, after submitting its shards. Use the
originating chat handle and an **absolute host-visible** frame directory:

```bash
PYTHONPATH=src .venv/bin/python -m api.device_workers.render_results register \
  --batch-id my-batch --session CH-000962 --output-dir /absolute/render/frames
```

HTTP alternative (owner session cookie and access to the destination chat):

```http
POST /api/workers/jobs/batch/my-batch/attachment
{"session_id":962,"output_dir":"/absolute/render/frames"}
```

Registration stores the binding even with the feature disabled; enabling does
not retroactively scan every batch. Completion or reconciliation will deliver it.
Destination and directory cannot be changed on an existing binding. Worker device
tokens cannot register attachments or choose host files. Local CLI registration
is a trusted agent operation, like other mesh platform CLIs.

Rendering and encoding are distinct stages. A verified complete frame inventory
produces a representative middle-frame attachment. To also deliver a video,
provide `--video-path /absolute/render/frames/movie.mp4 --encode-job-id JOB_ID`.
Both values are required, the video must be within the registered directory,
and the encode job must already exist. You can add this pair to a previously
frame-only binding once the encoder is submitted. Merely finding an MP4 on disk
does not mean encoding succeeded. Delivery requires the encoder's successful
job state plus a nonempty, accessible output. Failed/cancelled encodes produce
no video attachment.

Remote output must be copied/mounted into the host-visible directory. If files
arrive after the worker completion report, explicitly reconcile after copying:

```bash
PYTHONPATH=src .venv/bin/python -m api.device_workers.render_results reconcile --batch-id my-batch
```

Or POST `{"session_id":962,"reconcile":true}` to the same endpoint. Existing
`batch-watch` writes also reconcile. Missing files and staging failures remain
retryable; no background polling thread or automatic remote file transfer is
introduced. In-progress thumbnail refresh is deferred.

Expected `frame_<number>.<extension>` files must be nonempty supported images
inside the directory (symlinks escaping it are rejected). All expected frames
must exist and no shard may still be active/cancelled; repaired failed shards
are allowed once their complete output exists. EXR-only batches need conversion
to supported preview images in the registered host directory.

Each stage has a stable delivery key. SQLite inserts the message and its receipt
atomically, serializes concurrent retries, and skips deleted chats. Retries reuse
the saved message instead of restaging/reposting it. Expired staged copies follow
normal shared-media retention and are not reposted automatically.
