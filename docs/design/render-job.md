# The render job: the node driver, the engine side, the frame measurement

Design of #452, resting on the render node smoke test of #115 (stages 1 and 2, closing ruling). Each
choice says what was measured there; each assumed point is a line of "To measure on the first real
job". The shapes of `manifest.json`, `state.json` and `render.json` are brief section 16's, unchanged.

## What exists and what is missing

`factory run` calls the tool `render` (`kit/lib/tools/render.py`), which asks `kit/lib/node.py` for the
driver `RENDER_PROVIDER` names and calls `start()`, `run(job)`, `stop()`; `stop()` gives the dollars and
minutes of the ledger line. `node.PROVIDERS` is empty and nothing starts the engine or measures frames:

1. the driver `ec2`, the name fixed by the closing ruling on #115;
2. the engine side of a job: the stage's scene scripts on the pinned engine, the scene state, the
   frames rendered with Movie Render Graph;
3. the measurement of the frames into `render.json`, and the `manifest.json` that lists it all.

## The node

One on-demand GPU instance of the type in the setting `RENDER_INSTANCE_TYPE`, started from the machine
image in `RENDER_MACHINE_IMAGE`, with the tag the render-only key's policy requires (`RENDER_TAG`,
`key=value`). Measured on #115: the stock GPU machine image (NVIDIA driver, Docker with the NVIDIA
runtime, a local NVMe disk it mounts itself) on a T4 16 GB node with 4 CPUs and 16 GB of memory runs
the pinned image, builds a level in 66 s and renders 24 frames in 62 s, with memory never short
(14.9 GB free after each run). The image's root disk cannot hold the 77 GB engine image, and Docker
there keeps images in containerd's store, so both image stores are bound to the local disk before
the pull (measured: the first two attempts failed on exactly this).

## What the driver needs, and how it reaches it

A driver is made today with one key file: `node.connect(provider, key_file)`. It needs more:

| Need | Where it comes from |
|---|---|
| the node key (key id, secret, region: three lines) | secret `render.node`, as today |
| instance type, machine image, tag, price per hour, time limit | settings `RENDER_INSTANCE_TYPE`, `RENDER_MACHINE_IMAGE`, `RENDER_TAG`, `RENDER_USD_HOUR`, `RENDER_MAX_MIN` |
| the engine image, by digest, and its pull token | setting `UNREAL_IMAGE`, secret `engine.unreal` |
| the asset store and its key | setting `ASSET_STORE`, secret `store.assets` |
| the frame size; the re-render metric | settings `RENDER_RESOLUTION` (`<width>x<height>`), `PERCEPTUAL_METRIC` |
| the cloud endpoint, replaced in tests | setting `RENDER_API_URL`, empty for the real one |

They reach the driver the way every tool already gets its settings and secrets: as checks of the
resource `render.node` in `kit/resources.json`. `factory run` passes exactly the setting and secret
checks of the tool's resource in `call["settings"]` and `call["secrets"]` (`kit/lib/cmd_run.py`
lines 394 and 395), and `factory doctor` then reports a missing one before any job. `gen.model3d`
already lists the secret `store.assets` among its checks, so a resource naming another resource's
key file is the existing form. The driver gets them as a third argument:
`node.connect(provider, key_file, config)`, `config` being `{"settings", "secrets"}` of the call. A
driver class has `NEEDS`, the names it reads; `connect` returns None (the resource is down, nothing
billed) when one is absent, so a job never crashes half-configured. The fake driver of
`tests/test_render.py` takes and records `config` and has `NEEDS = ()`: the existing tests keep their
assertions. `RENDER_API_URL` and the store URL are what let the driver's tests run against a server on
127.0.0.1, as `tests/test_assets.py` does for the store.

## The transport: the asset store, signed links, no login

Measured on #115: the render-only key starts, stops and ends tagged GPU instances and reads their
state; it cannot open a port, make a key pair or read the console. So nothing logs in to the node.
Stage 2 measured the replacement: the node read command files and wrote outputs through links to the
asset store signed in advance; every command's output and three frames came back that way.

- Signed links are made on the machine that runs `factory run`, with the `store.assets` key, by the
  standard library. The kit signs requests in headers today (`kit/lib/media.py`, `authorization` and
  `_signed`); a link puts the same Signature Version 4 in its query string (`UNSIGNED-PAYLOAD`), and
  the cloud's own API calls need the same signing for the service `ec2`. One shared signer serves the
  three, checked against the published examples of the signing documentation.
- The node holds no credential but the image pull token, which travels in the start script as
  measured (it is gone when the instance ends, and containers cannot read the instance's metadata:
  hop limit 1). No instance profile: the key cannot pass a role.
- A job's files live under `jobs/<nonce>/` in the store, `<nonce>` a random token made by `start()`:
  `links.txt`, `in.tar` (the job description and its text inputs), `out.tar`, `log.txt` and
  `status.json`. Asset binaries are read through signed links to their sha256 names. The store's
  sha256 names are not used for job files: nothing knows an output's hash before it is made.
- `start()` comes before `run(job)` and has no job, so the start script carries only the signed read
  link of `links.txt`, and the node polls it (as stage 2 polled its command files every 4 s) while it
  pulls the image. `run(job)` uploads `in.tar`, then `links.txt` last. The pull and the upload overlap.
- The node writes `out.tar` and `log.txt`, then `status.json` last; the driver waits for
  `status.json` (a signed `HEAD` every 15 s), the instance leaving `running`, or the time limit.
  Links expire `RENDER_MAX_MIN` plus 15 minutes after they are made.

The start script is stage 2's with its command loop replaced by one job: hard stop first, local disk,
both image stores on it, login, pull by digest, logout, fetch `links.txt` then `in.tar`, run the engine
side, put the outputs, `sync`, `shutdown -h now` (the 30 s sleep was for the console and goes). A start
script holds at most 16 KB, so the engine side's files travel in `in.tar`.

## Money, time, and a node that hangs

- `stop()` ends the instance (end, not stop: nothing is kept) and waits until it is `shutting-down` or
  `terminated`. It returns `compute_min` = minutes from the instance's launch time to its end time, and
  `usd` = `compute_min / 60 * RENDER_USD_HOUR`, each rounded up to 4 decimals, at least one minute.
  Both times are the instance's own, read from its state: the launch time, and the time in the state
  reason of an instance the driver ended. An instance that ended itself has no time in its reason
  (assumed); its end is the first poll that saw it not running, at most 15 s late. Billing stops at
  `shutting-down` (the cloud's documentation; the bill is the authority, as on #115).
- A hung node ends itself: the start script's first command is `shutdown -h +<RENDER_MAX_MIN>`, and the
  instance is launched with terminate-on-shutdown. Measured on #115: all four instances ended, two by
  that path (a failed pull, then the stage 2 run that shut itself down after its last command). The
  driver also gives up waiting at `RENDER_MAX_MIN` plus 5 minutes and calls `stop()`. If the machine
  that runs `factory run` dies, the timer still ends the node.
- The root disk is deleted with the instance (set in the launch request, not left to the image).
- The launch request carries an idempotency token, so a launch whose answer was lost is asked again
  without a second node.
- `Unavailable` (the resource is down, nothing billed, the job may run again under the same PASS)
  covers: no capacity for the type, a quota or vCPU limit, throttling, and an endpoint that cannot be
  reached before any instance exists. A refused key, an unknown machine image or a bad parameter is a
  failed start (`node did not start`; `stop()` returns 0 and 0 when no instance was launched): it
  needs a person, not a retry.

## One node per job

The pull is 520 s of the 661 s stage 1 ran (82.5 GB image). A job on a cold node: about 60 s boot
(assumed), 532 s to ready, 66 s build, 62 s render, 62 s re-render of the sampled frames at `render`,
about 30 s to pack and upload (assumed): about 812 s, 13.5 minutes, $0.12 at $0.526 an hour. On a warm
node shared by a batch, each further job is about 190 s, $0.03.

Ruling: one node per job. The tool interface, the gate PASS, the ledger line and `Down` are all per job
hash; a node shared by several jobs would bill several gates on one instance and split its cost by a
guess, and a job that hangs or crashes the engine would spoil the next. At $0.12 a job, the episode cap
of $20 holds about 160 jobs. The pull is removed instead by a machine image holding the engine image
(made once by the orchestrator's admin profile; the render-only key cannot), measured before the first
production episode; a batch is reconsidered only if that fails.

## The engine side of a job

`in.tar` holds `job.json` (the job's hash, stage, unit, `fps`, `first`, `last`, width and height,
the scene scripts in order, the assets as `{id: sha256}`, the sampled frames) and the scripts. The
driver derives it from the job's inputs: every input named `scene.py`, in input order (the stage is
the directory of the last one); the shot's row of the input `shots.json`; `RENDER_RESOLUTION`.

1. **Build**, one editor run: `UnrealEditor-Cmd <project> -ExecutePythonScript=<build script>
   -RenderOffscreen -unattended -nosplash -nosound -stdout`, in the engine container with
   `--gpus all`, as measured. Not the `pythonscript` commandlet: on #115 it ran Python but
   `spawn_actor_from_object` returned None, as it has no editor world; the editor with
   `-ExecutePythonScript` built the level with and without a GPU, and built the golden `prop-drop`
   and `lit-render` scenes unchanged. The build script makes a new level, runs each scene script in
   its own namespace, then records the raw scene in `scene.json` (each actor's label, class, transform,
   scale and bounds in centimetres; the camera; the lights; the console variables the scripts set),
   makes the level sequence with the shot's frame range and a camera cut to the actor `camera`
   (measured from Python), makes the Movie Render Graph job, and saves the level, the sequence and the
   queue manifest.
2. **Exit.** Measured: the first build exited with signal 11 in the Python teardown after the files
   were saved; the second, which dropped its subsystem references and ran `gc.collect()` at the end,
   exited 0. The build script ends that way: it deletes each scene namespace and every engine object
   it holds, collects, and only then logs the line `FACTORY build ok`. The job trusts that line and the
   saved files, never the exit code alone; an exit code other than 0 after the line is a note in
   `node.json`, not a failure.
3. **Render**, a second process, the measured command line: the entry map with the Movie Pipeline game
   mode, `-game -Multiprocess -NoLoadingScreen -FixedSeed -nohmd -windowed -RenderOffscreen
   -MoviePipelineConfig=<queue manifest>`. Measured: Movie Render Graph through
   `set_graph_preset(DefaultRenderGraph)` rendered 24 frames at 1920x1080, as JPEG, under
   `Saved/MovieRenders/`, in 62 s. The job needs PNG frames at the job's size in its own directory,
   plus a float EXR of each frame for the NaN check; the graph's output settings are set from Python.
   The render is a new process, so console variables the scripts set (Lumen, anti-aliasing) are copied
   into the job's settings (measured with the Movie Render Queue setting for them, seen in the log).
4. **Re-render** at `render` only: the sampled frames (first, middle, last until #130 rules), a third
   run. Frames are named `frame-<nnnnnn>.png` and `.exr` by episode frame number; `node.json` records
   the engine build, the command lines, exit codes, marker lines and the seconds of each step.

`state.json` is assembled by the kit on the machine that runs `factory run`, from `scene.json` and the
inputs, so its rules are tested offline: centimetres to metres, rounded to 0.1 mm and 0.001 degrees so
two runs of a static scene give equal states (`kit/lib/reproduce.py` compares the whole state);
`framing` and `line_flips` by projecting the bounds through the camera; `kind` from the asset registry
or, for a golden level, its `LEVEL`; `asset` and `sha256` null for engine primitives. `motion`,
`lipsync` and `caches` stay empty in this version, and a scene naming `asset:<id>` fails the job.

## The frame measurement

On the machine that runs `factory run`, after `out.tar` is unpacked, never on the node: `local.ffmpeg`
and `local.ocr` are installed and checked there, no node minute is billed for it, and every rule runs
offline in tests with fake programs as `tests/test_analysis.py` does. `render.json`: `count` and
`frames` from the files; `frames_sha256` and `sequence_sha256` from the bytes; `black` by ffmpeg's
`blackdetect` on the sequence; `nan` from the EXR frames decoded to 32-bit floats; `text` by `tesseract`
on each frame; `samples` per sampled frame (mean linear colour, Rec. 709 luminance of it, share of
pixels with a channel at 255); `rerender` at `render` by `PERCEPTUAL_METRIC` on each pair. With that
setting empty `rerender` is left out, so the `render` lint fails the stage until `local.metrics`
(#131) exists: a stage is never passed on a score nobody computed. `manifest.json` is written last, so
it lists `render.json`, `state.json`, every frame and both logs with their sha256 and size.

## Two renders are never byte-equal

Measured on #115: the same job rendered twice gave 0 of 24 equal frames by hash; on frame 12 the largest
difference was 9 of 255, the mean 0.0058, and 0.56 % of the bytes differed. With anti-aliasing, Lumen
GI and Lumen reflections off and `-FixedSeed`, still 0 of 24 equal by hash. So no check compares frames
by hash across two runs. In the kit today: `kit/lib/reproduce.py` already compares `render.json`
without the frame hashes and scores differing frames by `PERCEPTUAL_METRIC` against `RERENDER_MIN`; the
`render` lint holds each `rerender` score to 9.9 (`kit/lib/lints/render.py`); the `lighting` lint holds
samples to 10 % luminance and delta-E 1; the golden check (`kit/lib/golden.py`, `_hash_problems`)
compares a run's files with that same run's manifest, which stays exact. One check is at risk:
`simulation` wants equal cache hashes in two runs, and whether an engine cache is byte-equal is not
measured. The measurement of #130 sets the tolerances; no new check may hash frames across runs.

## Recorded sessions as fixtures

Tests never call the cloud. The orchestrator records a session against the real node once; a server
on 127.0.0.1 replays it. One file per exchange, in order,
`tests/fixtures/render/<session>/<nn>-<action>.json`: `{"request": {"method", "path", "params"},
"response": {"status", "body"}}`, `params` the form or query fields without the signature fields,
`body` the answer's text. Before it is committed, instance, reservation, account, image, network and
request ids become fixed invented ones, the store's host becomes `store.invalid`, and the start
script's token is replaced. Error answers no real call can provoke (no capacity, quota) are written in
the documented shape and marked `"source": "written"`. The first real job is recorded the same way,
with its `out.tar` cut to three frames, and becomes the end-to-end fixture.

## To measure on the first real job

1. Boot time from the launch request to the script's first line, and the time to pack and upload.
2. The state reason, with or without a time, of an instance that ended itself; how long `stop()` waits.
3. That the root disk is gone after the end, and that the ledger's dollars match the bill.
4. That a launch with an idempotency token and the tag succeeds with the render-only key.
5. Movie Render Graph from Python: PNG and EXR outputs, the job's size and output directory, a subset
   of frames for the re-render. If the graph cannot be set from Python, the job falls back to the Movie
   Render Queue settings measured on #115 (640x360 PNG with a chosen directory) and this design changes.
6. The pixel distance of two renders with anti-aliasing and Lumen off (not measured: the compare failed).
7. That two builds of one static scene give byte-equal `scene.json`, and that the exit remedy, seen on
   one build, holds on every build.
8. The size of a 1080p PNG and EXR frame, so the size of `out.tar` (one upload holds at most 5 GB).
9. A machine image holding the engine image: its start-to-ready time against the 532 s of a pull.
10. The time OCR takes on 24 frames at 1080p, and whether the engine's own text ever shows.
11. The `two-shot` scenes on the real editor (not run on #115: the wrong path was fetched).
