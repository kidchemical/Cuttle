## Automatic render result attachments

For batches that should publish their result to the originating chat, register
the batch once with `python -m api.device_workers.render_results register
--batch-id ID --session CH-... --output-dir /absolute/host-visible/frames` after
submitting shards. The experimental `render_result_attachments` flag gates
delivery. The server attaches verified completed frames without an agent reply;
add `--video-path PATH --encode-job-id ID` once the encode job exists for a
separate video attachment after successful encoding. After copying remote output
to the host, run `python -m api.device_workers.render_results reconcile
--batch-id ID`; watch writes also retry. Do not manually repost the same output.
Details and authenticated HTTP API: [render-result-attachments.md](../../../../.cuttle/docs/render-result-attachments.md).
