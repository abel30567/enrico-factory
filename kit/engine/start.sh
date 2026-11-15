#!/bin/bash
# Render node start script: one job (docs/design/render-job.md). Hard stop first: the instance ends itself (terminate on shutdown).
shutdown -h +__MAX_MIN__
IMAGE='__IMAGE__'
NODE_LOG=/var/log/factory-node.log
say(){ echo "NODE $(date -u +%H:%M:%S) $*" | tee -a "$NODE_LOG" > /dev/console; }
T0=$(date +%s)
say start kernel=$(uname -r) cpu=$(nproc) mem_gb=$(free -g | awk '/Mem:/{print $2}')
say gpu "$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1 | head -1)"
systemctl stop docker docker.socket containerd 2>/dev/null
# The local disk: the machine image may mount it itself (a volume group on the device), else it is formatted here.
D=$(lsblk -dno NAME,MODEL | awk '/Instance Storage/{print "/dev/"$1; exit}')
W=""
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  [ -n "$D" ] && W=$(lsblk -rno MOUNTPOINT "$D" | grep -m1 '^/') && [ -n "$W" ] && break
  W=""; sleep 5
done
if [ -z "$W" ] && [ -n "$D" ]; then mkfs.ext4 -F -q "$D" && mkdir -p /mnt/factory && mount "$D" /mnt/factory && W=/mnt/factory; fi
[ -n "$W" ] || W=/var/lib/factory
mkdir -p "$W/docker" "$W/containerd"
# The image store may be docker's or containerd's: put both on the local disk with bind mounts.
for d in docker containerd; do
  mkdir -p /var/lib/$d; cp -a /var/lib/$d/. "$W/$d/" 2>/dev/null; mount --bind "$W/$d" /var/lib/$d
done
systemctl start containerd
systemctl start docker
say disk dev=$D work=$W avail=$(df -h --output=avail "$W" | tail -1 | tr -d ' ')
REGISTRY="${IMAGE%%/*}"
printf '%s\n' '__TOKEN__' | docker login "$REGISTRY" -u x --password-stdin >/dev/null 2>&1; say login rc=$?
t=$(date +%s); docker pull -q "$IMAGE" >/var/log/pull.log 2>&1; P=$?
docker logout "$REGISTRY" >/dev/null 2>&1
say pull rc=$P sec=$(( $(date +%s)-t ))
# links.txt exists only once the driver has uploaded in.tar, so it is polled until it is there.
until curl -sf -o "$W/links.txt" '__LINKS__'; do sleep 4; done
say links n=$(wc -l < "$W/links.txt")
put(){ h=$(curl -s -o /dev/null -w '%{http_code}' -X PUT --upload-file "$2" "$(awk -v n="$1" '$1=="PUT" && $2==n {print $3}' "$W/links.txt")"); say put $1 http=$h; }
ends(){ say result failed step=$1 total_sec=$(( $(date +%s)-T0 )); grep '^NODE ' "$NODE_LOG" > "$W/log.txt"; put log "$W/log.txt"; put status "$W/status.json"; }
if [ $P -ne 0 ]; then
  echo '{"result": "failed", "step": "pull"}' > "$W/status.json"; ends pull
  sync; shutdown -h now; exit 0
fi
curl -sf -o "$W/in.tar" "$(awk '$1=="GET" && $2=="in" {print $3}' "$W/links.txt")"; F=$?
mkdir -p "$W/work/job" "$W/work/out"
[ $F -eq 0 ] && { tar -xf "$W/in.tar" -C "$W/work/job"; F=$?; }
say fetch rc=$F bytes=$(wc -c < "$W/in.tar" 2>/dev/null)
if [ $F -ne 0 ] || [ ! -f "$W/work/job/job.sh" ]; then
  echo '{"result": "failed", "step": "fetch"}' > "$W/status.json"; ends fetch
  sync; shutdown -h now; exit 0
fi
say ready total_sec=$(( $(date +%s)-T0 ))
WORK="$W/work" LINKS="$W/links.txt"
export WORK IMAGE LINKS NODE_LOG
bash "$W/work/job/job.sh" > /dev/console 2>&1
say done rc=$? total_sec=$(( $(date +%s)-T0 ))
sync
shutdown -h now
