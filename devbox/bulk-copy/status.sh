#!/usr/bin/env bash
# One-screen status of the step 3 bulk copy, run on sami-rocqperf.
D=/mnt/durable/sami/devbox-copy
tail -2 $D/monitor-sami-rocqperf.log | cut -c1-400
for u in up-rocqperf up-rocqperf-nvme find-durable; do
  echo "devbox-copy-$u: $(systemctl is-active devbox-copy-$u) $(systemctl show -p FreezerState --value devbox-copy-$u)"
done
awk '/ ok /{split($5,b,"="); n[$2]++; s[$2]+=b[2]} / fail /{f[$2]++} END{for(t in n) printf "rocqperf up %s: %d shards %.1f GiB fail=%d\n", t, n[t], s[t]/2^30, f[t]}' $D/upload.log
timeout 30 ssh -o BatchMode=yes sami-leanperf-lan 'awk "/ ok /{split(\$5,b,\"=\"); n[\$2]++; s[\$2]+=b[2]} / fail /{f[\$2]++} END{for(t in n) printf \"leanperf up %s: %d shards %.1f GiB fail=%d\n\", t, n[t], s[t]/2^30, f[t]}" ~/devbox-copy/upload.log; tail -1 ~/devbox-copy/monitor-sami-leanperf.log | cut -c1-200'
timeout 30 ssh -o BatchMode=yes sami@sami-devbox 'awk "/ ok /{split(\$5,b,\"=\"); n[\$1\" \"\$2]++; s[\$1\" \"\$2]+=b[2]} / fail /{f[\$1\" \"\$2]++} END{for(t in n) printf \"pulled %s: %d shards %.1f GiB fail=%d\n\", t, n[t], s[t]/2^30, f[t]}" /mnt/nvme/sami/cutover/bulk-copy.log; df -h --output=target,avail / /mnt/nvme /mnt/durable /mnt/scratch | tail -4 | tr "\n" " "; echo; head -1 /proc/pressure/io'
