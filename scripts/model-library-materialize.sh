#!/usr/bin/env bash
# Source-only helpers for materializing model-library runtime views.

# Materialize source into hub_dest on rank.
# mode: durable-home-symlink | force-copy
#
# A durable-home runtime view must be a symlink to that exact tree. Creating
# another copy would violate the one-durable-home storage contract, so
# preparation stops if the symlink cannot be created or verified.
materialize_tree_on_rank() {
  local rank="${1:?}" source="${2:?}" hub_dest="${3:?}" mode="${4:?}"
  local dest_parent qsrc qdst qparent method
  dest_parent=$(dirname "$hub_dest")
  qsrc=$(printf '%q' "$source")
  qdst=$(printf '%q' "$hub_dest")
  qparent=$(printf '%q' "$dest_parent")

  case "$mode" in
    durable-home-symlink|force-copy) ;;
    *) warn "unsupported model-tree materialization mode: $mode"; return 2 ;;
  esac

  if [ "$rank" = 0 ]; then
    mkdir -p "$dest_parent"
    rm -rf "$hub_dest"
    if [ "$mode" = durable-home-symlink ]; then
      if ln -sfn "$source" "$hub_dest" 2>/dev/null \
          && [ -L "$hub_dest" ] \
          && [ "$(readlink -- "$hub_dest")" = "$source" ]; then
        log "rank 0 materialize=symlink_home → $hub_dest"
        return 0
      fi
      rm -rf "$hub_dest"
      warn "rank 0: durable-home view requires an exact symlink; preparation cannot continue"
      return 1
    fi
    mkdir -p "$hub_dest"
    rsync -a --delete "$source"/ "$hub_dest"/
    log "rank 0 materialize=rsync → $hub_dest"
    return 0
  fi

  if [ "$mode" = durable-home-symlink ]; then
    if method=$(ssh_node "$rank" \
      "set -euo pipefail
       mkdir -p $qparent
       rm -rf $qdst
       if ln -sfn $qsrc $qdst 2>/dev/null &&
          [ -L $qdst ] && [ \"\$(readlink -- $qdst)\" = $qsrc ]; then
         echo symlink_home
         exit 0
       fi
       rm -rf $qdst
       echo 'durable-home view requires an exact symlink' >&2
       exit 1"
    ); then
      log "rank $rank materialize=${method} → $hub_dest"
      return 0
    fi
    warn "rank $rank: durable-home view requires an exact symlink; preparation cannot continue"
    return 1
  fi

  # Transfer mounts and force-copy callers require a fresh copied destination.
  ssh_node "$rank" \
    "set -euo pipefail
     mkdir -p $qparent
     rm -rf $qdst
     mkdir -p $qdst
     if cp -a $qsrc/. $qdst/ 2>/dev/null; then echo cp_a; exit 0; fi
     rsync -a --delete $qsrc/ $qdst/
     echo rsync" \
    | { read -r method || method=rsync; log "rank $rank materialize=${method} → $hub_dest"; }
}
