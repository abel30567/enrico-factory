#!/bin/bash
# The engine side of one render job (docs/design/render-job.md, "The engine side of a job").
# Reads WORK, IMAGE, LINKS, NODE_LOG and UE_EDITOR only. Fetches each asset binary links.txt names, then builds the
# level with build.py (twice at `simulation`), renders the frames, re-renders the sampled frames at `render`, packs
# out.tar and puts out.tar, log.txt and status.json last. bash 3.2: no bash 4 feature. Not `set -e`: a failed step
# still uploads.
set -u
export LC_ALL=C
UE_EDITOR="${UE_EDITOR:-/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd}"
O="$WORK/out"
ENV="$WORK/Job/job.env"
TAIL="-unattended -nosplash -nosound -stdout -FullStdOutLogOutput"
STEPS=""
FAILED=""
say(){ echo "NODE $(date -u +%H:%M:%S) $*" | tee -a "$NODE_LOG"; }
fail(){ [ -n "$FAILED" ] || FAILED=$1; }
# A JSON string: backslash and double quote escaped, every other control character dropped.
js(){ printf '"%s"' "$(printf '%s' "$1" | tr -d '\000-\037\177' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"; }

mkdir -p "$O" "$WORK/Job"
cat > "$WORK/Job/Job.uproject" <<'P'
{"FileVersion": 3, "EngineAssociation": "", "Category": "", "Description": "",
 "Plugins": [{"Name": "PythonScriptPlugin", "Enabled": true}, {"Name": "MovieRenderPipeline", "Enabled": true}]}
P

# ue STEP LIMIT LOG args... : one editor run in the container, its output appended to LOG.
ue(){
  local step=$1 lim=$2 log=$3 t rc s cmd a mk line
  shift 3
  t=$(date +%s)
  chmod -R 777 "$WORK"
  docker rm -f "ue_$step" >/dev/null 2>&1
  timeout "$lim" docker run --name "ue_$step" --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all -v "$WORK:/work" "$IMAGE" bash -c '"$0" /work/Job/Job.uproject "$@" -unattended -nosplash -nosound -stdout -FullStdOutLogOutput' "$UE_EDITOR" "$@" > "$WORK/ue_$step.txt" 2>&1
  rc=$?
  docker rm -f "ue_$step" >/dev/null 2>&1
  s=$(( $(date +%s) - t ))
  cat "$WORK/ue_$step.txt" >> "$log"
  say step "$step" rc=$rc seconds=$s
  cmd="$(js "$UE_EDITOR"), $(js /work/Job/Job.uproject)"
  for a in "$@" $TAIL; do cmd="$cmd, $(js "$a")"; done
  mk=""
  while IFS= read -r line; do mk="$mk${mk:+, }$(js "$line")"; done <<EOF
$(grep -a 'FACTORY ' "$WORK/ue_$step.txt" | tr -d '\r' | awk '{print substr($0, index($0, "FACTORY "))}')
EOF
  [ "$mk" = '""' ] && mk=""
  rm -f "$WORK/ue_$step.txt"
  STEPS="$STEPS${STEPS:+, }{\"command\": [$cmd], \"markers\": [$mk], \"rc\": $rc, \"seconds\": $s, \"step\": \"$step\"}"
}

# val KEY PATTERN : the value of the line KEY=<value> of job.env when it matches PATTERN.
val(){ sed -n -E "s/^$1=($2)\$/\\1/p" "$ENV" 2>/dev/null | head -1; }

# out/<dir>/<digits>.<ext> becomes out/<prefix>-<nnnnnn>.<ext>, the frame number read in base 10.
frames(){
  local dir=$1 prefix=$2 f b d e
  for f in "$O/$dir"/*; do
    b=${f##*/}; d=${b%.*}; e=${b##*.}
    case "$d" in ''|*[!0-9]*) continue ;; esac
    case "$e" in png|exr) ;; *) continue ;; esac
    mv "$f" "$O/$prefix-$(printf '%06d' $((10#$d))).$e"
  done
}

# get: each line `GET <sha256> <url>` of links.txt to job/assets/<sha256>.glb, kept only on http 200.
# build.py checks each binary against the sha256 of its job asset.
mkdir -p "$WORK/job/assets"
while read -r m n u; do
  [ "$m" = GET ] && [ ${#n} -eq 64 ] || continue
  case "$n" in *[!0-9a-f]*) continue ;; esac
  f="$WORK/job/assets/$n.glb"
  h=$(curl -s -o "$f.part" -w '%{http_code}' "$u")
  [ "$h" = 200 ] && mv "$f.part" "$f"
  rm -f "$f.part"
  say get "$n" http=$h
done < "$LINKS"

: > "$O/engine-build.log"
ue build 900 "$O/engine-build.log" -ExecutePythonScript=/work/job/build.py -RenderOffscreen
grep -q 'FACTORY build ok' "$O/engine-build.log" && [ -f "$O/scene.json" ] || fail build
if [ -z "$FAILED" ]; then
  WIDTH=$(val WIDTH '[0-9]+'); HEIGHT=$(val HEIGHT '[0-9]+')
  FIRST=$(val FIRST '[0-9]+'); LAST=$(val LAST '[0-9]+')
  STAGE=$(val STAGE '[a-z-]+'); SAMPLED=$(val SAMPLED '"[0-9 ]*"' | tr -d '"')
  if [ ! -f "$ENV" ] || grep -qvE '^(WIDTH=[0-9]+|HEIGHT=[0-9]+|FIRST=[0-9]+|LAST=[0-9]+|STAGE=[a-z-]+|SAMPLED="[0-9 ]*")$' "$ENV" \
    || ! grep -qE '^SAMPLED="[0-9 ]*"$' "$ENV" \
    || [ -z "$WIDTH" ] || [ -z "$HEIGHT" ] || [ -z "$FIRST" ] || [ -z "$LAST" ] || [ -z "$STAGE" ]; then
    fail build
  fi
fi

# At simulation, a second build of the same job: its raw scene is rerun-scene.json, scene.json stays the first.
# The second build starts from a project without the first build's level, sequence and imported assets.
if [ -z "$FAILED" ] && [ "$STAGE" = simulation ]; then
  mv "$O/scene.json" "$O/first-scene.json"
  rm -rf "$WORK/Job/Content"
  ue rebuild 900 "$O/engine-build.log" -ExecutePythonScript=/work/job/build.py -RenderOffscreen
  [ "$(grep -c 'FACTORY build ok' "$O/engine-build.log")" = 2 ] && [ -f "$O/scene.json" ] || fail rebuild
  [ -f "$O/scene.json" ] && mv "$O/scene.json" "$O/rerun-scene.json"
  mv "$O/first-scene.json" "$O/scene.json"
fi

if [ -z "$FAILED" ]; then
  : > "$O/engine-render.log"
  ue render 1800 "$O/engine-render.log" "MoviePipelineEntryMap?game=/Script/MovieRenderPipelineCore.MoviePipelineGameMode" -game -Multiprocess -NoLoadingScreen -FixedSeed -log -nohmd -windowed -ResX=$((10#$WIDTH)) -ResY=$((10#$HEIGHT)) -RenderOffscreen -MoviePipelineConfig=MovieRenderPipeline/QueueManifest.utxt
  frames frames frame
  n=$((10#$FIRST))
  while [ $n -le $((10#$LAST)) ]; do
    for e in png exr; do [ -f "$O/frame-$(printf '%06d' $n).$e" ] || fail render; done
    n=$((n + 1))
  done
fi

if [ -z "$FAILED" ] && [ "$STAGE" = render ] && [ -n "$(echo $SAMPLED)" ]; then
  echo "== rerender" >> "$O/engine-render.log"
  ue rerender 900 "$O/engine-render.log" "MoviePipelineEntryMap?game=/Script/MovieRenderPipelineCore.MoviePipelineGameMode" -game -Multiprocess -NoLoadingScreen -FixedSeed -log -nohmd -windowed -ResX=$((10#$WIDTH)) -ResY=$((10#$HEIGHT)) -RenderOffscreen -MoviePipelineConfig=MovieRenderPipeline/Rerender.utxt
  frames rerender rerender
  for f in $SAMPLED; do [ -f "$O/rerender-$(printf '%06d' $((10#$f))).png" ] || fail rerender; done
fi

# pack: node.json, then out.tar of the outputs that exist, in byte order.
ENGINE=$(grep -a -m1 'Engine Version: ' "$O/engine-build.log" 2>/dev/null | tr -d '\r' | sed 's/^.*Engine Version: //')
[ -n "$ENGINE" ] && ENGINE=$(js "$ENGINE") || ENGINE=null
[ -z "$FAILED" ] && R=ok || R=failed
printf '{"editor": %s, "engine": %s, "image": %s, "result": "%s", "steps": [%s]}\n' \
  "$(js "$UE_EDITOR")" "$ENGINE" "$(js "$IMAGE")" "$R" "$STEPS" > "$O/node.json" || fail pack
set --
for f in $(ls -1 "$O" | grep -E '^(scene\.json|rerun-scene\.json|node\.json|engine-build\.log|engine-render\.log|frame-[0-9]+\.(png|exr)|rerender-[0-9]+\.png)$'); do
  set -- "$@" "$f"
done
rm -f "$WORK/out.tar"
tar -cf "$WORK/out.tar" -C "$O" "$@" || fail pack

url(){ awk -v n="$1" '$1=="PUT" && $2==n {print $3}' "$LINKS"; }
put(){ curl -s -o /dev/null -w '%{http_code}' -X PUT --upload-file "$2" "$(url "$1")"; }
h=$(put out "$WORK/out.tar")
say put out http=$h
[ "$h" = 200 ] || fail upload

if [ -z "$FAILED" ]; then
  say result ok
  echo '{"result": "ok"}' > "$WORK/status.json"
else
  say result failed step=$FAILED
  echo "{\"result\": \"failed\", \"step\": \"$FAILED\"}" > "$WORK/status.json"
fi
{
  grep -a '^NODE ' "$NODE_LOG"
  for l in engine-build.log engine-render.log; do
    [ -f "$O/$l" ] || continue
    echo "== $l (last 100 lines)"
    tail -n 100 "$O/$l"
  done
} > "$WORK/log.txt"
h=$(put log "$WORK/log.txt")
h=$(put status "$WORK/status.json")
exit 0
